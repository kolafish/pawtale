from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.schemas.memoir_book import (
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookOutputChapterEditorialCopyUpdateRequest,
    MemoirBookOutputEditorialCopyRegenerateRequest,
    MemoirBookOutputGenerateRequest,
    MemoirBookOutputResponse,
    MemoirBookOutputUpdateRequest,
    MemoirBookOutputVersionResponse,
    MemoirBookTitleSuggestionRequest,
    MemoirBookTitleSuggestionResponse,
)
from app.services.file_exports import ExportFile
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio import (
    audio_cache_payload,
    audio_response,
    cached_audio_response,
    discard_staged_audio_files,
    effective_tts_speed_ratio,
    source_text_hash,
    synthesize_text_audio,
)
from app.services.memoir.book_manuscript_ai import generate_manuscript_editorial_copy, generate_title_suggestions
from app.services.memoir.book_output_manuscript import (
    DEFAULT_BOOK_COVER_STYLE,
    apply_editorial_copy as _apply_editorial_copy,
    chapter_id_set as _chapter_id_set,
    cover_style as _cover_style,
    effective_book_metadata as _effective_book_metadata,
    fallback_title_suggestions as _fallback_title_suggestions,
    formal_book_title as _formal_book_title,
    manuscript_from_snapshot as _manuscript_from_snapshot,
    manuscript_response as _manuscript_response,
    positive_int as _positive_int,
    request_book_metadata_patch as _request_book_metadata_patch,
    safe_str as _safe_str,
    stored_cover_style as _stored_cover_style,
    stored_manuscript_metadata as _stored_manuscript_metadata,
    structured_content_response as _structured_content_response,
    target_set as _target_set,
    update_chapter_editorial_copy as _update_chapter_editorial_copy,
    updated_manuscript_payload as _updated_manuscript_payload,
)
from app.services.memoir.book_output_export import (
    MemoirBookOutputExportFormat,
    MemoirBookOutputExportLayout,
    render_book_output_export,
)
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter
from app.services.memoir.voice_tts_types import TTSClient


BOOK_TTS_CACHE_KEY = "book_tts"
AI_MODEL_METADATA_KEY = "ai_model"
SNAPSHOT_COMPARE_KEYS = (
    "chapter_id",
    "chapter_title",
    "chapter_sort_order",
    "draft_id",
    "draft_version_id",
    "draft_version",
    "content_char_count",
)


def _project_ai_model(project: object) -> str | None:
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(AI_MODEL_METADATA_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _remember_project_ai_model(project: object, ai_model: str | None) -> None:
    model = (ai_model or "").strip()
    if not model:
        return
    metadata = getattr(project, "metadata_json", None)
    setattr(
        project,
        "metadata_json",
        {
            **(metadata if isinstance(metadata, dict) else {}),
            AI_MODEL_METADATA_KEY: model,
        },
    )


def _latest_chapter_version(draft: MemoirBookChapterDraft | None) -> MemoirBookChapterDraftVersion | None:
    if not draft or not draft.versions:
        return None
    return draft.versions[-1]


def _output_version_response(version: MemoirBookOutputVersion, *, title: str) -> MemoirBookOutputVersionResponse:
    snapshot = [
        item for item in (version.chapter_snapshot_json or [])
        if isinstance(item, dict)
    ]
    manuscript = _manuscript_response(
        title=title,
        content=version.content,
        snapshot=snapshot,
        stored=version.manuscript_json,
    )
    return MemoirBookOutputVersionResponse(
        id=version.id,
        output_id=version.output_id,
        project_id=version.project_id,
        plan_id=version.plan_id,
        version=version.version,
        content=version.content,
        chapter_snapshot_json=snapshot,
        structured_content=_structured_content_response(
            title=title,
            content=version.content,
            snapshot=snapshot,
        ),
        manuscript=manuscript,
        created_by=MemoirCreatedBy(version.created_by),
        created_at=version.created_at,
    )


def _output_response(
    output: MemoirBookOutput,
    *,
    stale_chapter_titles: list[str] | None = None,
) -> MemoirBookOutputResponse:
    versions = [_output_version_response(version, title=output.title) for version in output.versions]
    stale_titles = stale_chapter_titles or []
    return MemoirBookOutputResponse(
        id=output.id,
        project_id=output.project_id,
        plan_id=output.plan_id,
        title=output.title,
        status=MemoirOutputStatus(output.status),
        metadata_json=output.metadata_json or {},
        is_stale=bool(stale_titles),
        stale_chapter_titles=stale_titles,
        latest_version=versions[-1] if versions else None,
        versions=versions,
        created_at=output.created_at,
        updated_at=output.updated_at,
    )


async def _load_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookPlan:
    await load_memoir_project(db, project_id, user_id)
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
        .execution_options(populate_existing=True)
    )
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return plan


def _require_confirmed_chapters(plan: MemoirBookPlan) -> list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]]:
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")
    chapters = sorted(plan.chapters, key=lambda item: item.sort_order)
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")

    missing: list[str] = []
    confirmed: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]] = []
    for chapter in chapters:
        version = _latest_chapter_version(chapter.draft)
        if not chapter.draft or chapter.draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value or not version:
            missing.append(chapter.title)
            continue
        confirmed.append((chapter, version))
    if missing:
        preview = "、".join(missing[:3])
        suffix = "等章节" if len(missing) > 3 else ""
        raise HTTPException(status_code=400, detail=f"请先确认所有章节：{preview}{suffix}")
    return confirmed


def _compose_book_content(
    *,
    title: str,
    chapters: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]],
    source_refs_by_chapter: dict[str, list[dict[str, object]]] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    toc = "\n".join(f"{index}. {chapter.title}" for index, (chapter, _) in enumerate(chapters, start=1))
    body_parts: list[str] = []
    snapshot: list[dict[str, object]] = []
    for index, (chapter, version) in enumerate(chapters, start=1):
        content = version.content.strip()
        body_parts.append(f"第{index}章 {chapter.title}\n\n{content}")
        snapshot.append(
            _chapter_snapshot(
                chapter,
                version,
                chapter_index=index,
                source_refs=(source_refs_by_chapter or {}).get(chapter.id),
            )
        )
    content = f"{title}\n\n目录\n{toc}\n\n" + "\n\n".join(body_parts)
    return content.strip(), snapshot


def _compose_book_content_from_snapshot(
    *,
    title: str,
    snapshot: list[dict[str, object]],
) -> str:
    toc = "\n".join(
        f"{_positive_int(item.get('chapter_index')) or index}. {_safe_str(item.get('chapter_title')) or _safe_str(item.get('title')) or f'第{index}章'}"
        for index, item in enumerate(snapshot, start=1)
        if isinstance(item, dict)
    )
    body_parts: list[str] = []
    for index, item in enumerate(snapshot, start=1):
        if not isinstance(item, dict):
            continue
        chapter_index = _positive_int(item.get("chapter_index")) or index
        chapter_title = _safe_str(item.get("chapter_title")) or _safe_str(item.get("title")) or f"第{chapter_index}章"
        chapter_content = _safe_str(item.get("content"))
        if chapter_content:
            body_parts.append(f"第{chapter_index}章 {chapter_title}\n\n{chapter_content}")
    return (f"{title}\n\n目录\n{toc}\n\n" + "\n\n".join(body_parts)).strip()


def _chapter_snapshot(
    chapter: MemoirBookPlanChapter,
    version: MemoirBookChapterDraftVersion,
    *,
    chapter_index: int,
    source_refs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    content = version.content.strip()
    refs = source_refs or []
    source_ref_snapshot = [
        {
            "utterance_id": _safe_str(item.get("utterance_id")),
            "transcript_version_id": _safe_str(item.get("transcript_version_id")) or None,
            "voice_session_id": _safe_str(item.get("voice_session_id")) or None,
            "quote": _safe_str(item.get("quote")),
            "summary": _safe_str(item.get("summary")) or _safe_str(item.get("source_summary")),
            "representative_quote": _safe_str(item.get("representative_quote")) or _safe_str(item.get("quote")),
        }
        for item in refs
        if isinstance(item, dict) and _safe_str(item.get("utterance_id"))
    ]
    chapter_metadata = chapter.metadata_json if isinstance(chapter.metadata_json, dict) else {}
    return {
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "chapter_index": chapter_index,
        "chapter_sort_order": chapter.sort_order,
        "chapter_summary": (
            _safe_str(chapter_metadata.get("chapter_summary"))
            or _safe_str(chapter_metadata.get("summary"))
            or _safe_str(chapter_metadata.get("editorial_summary"))
        ),
        "metadata_json": chapter_metadata,
        "draft_id": chapter.draft.id if chapter.draft else "",
        "draft_version_id": version.id,
        "draft_version": version.version,
        "content_char_count": len(content),
        "source_count": len(source_ref_snapshot),
        "source_refs": source_ref_snapshot,
        "content": content,
    }


def _current_book_snapshot(
    plan: MemoirBookPlan,
    *,
    source_refs_by_chapter: dict[str, list[dict[str, object]]] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        return [], ["章节规划"]
    chapters = sorted(plan.chapters, key=lambda item: item.sort_order)
    snapshot: list[dict[str, object]] = []
    unavailable_titles: list[str] = []
    for index, chapter in enumerate(chapters, start=1):
        version = _latest_chapter_version(chapter.draft)
        if not chapter.draft or chapter.draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value or not version:
            unavailable_titles.append(chapter.title)
            continue
        snapshot.append(
            _chapter_snapshot(
                chapter,
                version,
                chapter_index=index,
                source_refs=(source_refs_by_chapter or {}).get(chapter.id),
            )
        )
    return snapshot, unavailable_titles


def _snapshot_stale_chapter_titles(
    latest_version: MemoirBookOutputVersion | None,
    current_snapshot: list[dict[str, object]],
    unavailable_titles: list[str],
) -> list[str]:
    if unavailable_titles:
        return unavailable_titles
    if not latest_version:
        return []
    stored_snapshot = [
        item for item in (latest_version.chapter_snapshot_json or [])
        if isinstance(item, dict)
    ]
    stored_by_chapter_id = {
        item.get("chapter_id"): item
        for item in stored_snapshot
        if isinstance(item.get("chapter_id"), str)
    }
    stale_titles: list[str] = []
    for current in current_snapshot:
        chapter_id = current.get("chapter_id")
        stored = stored_by_chapter_id.get(chapter_id)
        if not stored or any(stored.get(key) != current.get(key) for key in SNAPSHOT_COMPARE_KEYS):
            stale_titles.append(str(current.get("chapter_title") or "未命名章节"))
    if len(stored_snapshot) != len(current_snapshot) and not stale_titles:
        stale_titles.append("章节结构")
    return stale_titles


def _output_stale_chapter_titles(
    output: MemoirBookOutput,
    plan: MemoirBookPlan,
    *,
    source_refs_by_chapter: dict[str, list[dict[str, object]]] | None = None,
) -> list[str]:
    current_snapshot, unavailable_titles = _current_book_snapshot(
        plan,
        source_refs_by_chapter=source_refs_by_chapter,
    )
    latest_version = output.versions[-1] if output.versions else None
    return _snapshot_stale_chapter_titles(latest_version, current_snapshot, unavailable_titles)


async def _latest_output_version_number(db: AsyncSession, output_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirBookOutputVersion.version)).where(
            MemoirBookOutputVersion.output_id == output_id
        )
    )
    return int(value or 0)


async def _load_output(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookOutput | None:
    return await db.scalar(
        select(MemoirBookOutput)
        .where(MemoirBookOutput.project_id == project_id)
        .options(selectinload(MemoirBookOutput.versions))
        .execution_options(populate_existing=True)
    )


async def get_book_output(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookOutputResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    plan = await _load_plan(db, user_id=user_id, project_id=project_id)
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    return _output_response(
        output,
        stale_chapter_titles=_output_stale_chapter_titles(
            output,
            plan,
            source_refs_by_chapter=refs_by_chapter,
        ),
    )


async def generate_book_output(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookOutputGenerateRequest,
) -> MemoirBookOutputResponse:
    project = await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, user_id=user_id, project_id=project_id)
    chapters = _require_confirmed_chapters(plan)
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    user_title = _safe_str(req.title)
    title = _formal_book_title(user_title or plan.title)
    cover_style = _cover_style(req.cover_style)
    content, snapshot = _compose_book_content(
        title=title,
        chapters=chapters,
        source_refs_by_chapter=refs_by_chapter,
    )
    requested_ai_model = (req.ai_model or "").strip()
    ai_model = requested_ai_model or _project_ai_model(project)
    _remember_project_ai_model(project, req.ai_model)
    editorial_copy = (
        await generate_manuscript_editorial_copy(
            title=title,
            snapshot=snapshot,
            ai_model=ai_model,
        )
        if ai_model
        else None
    )
    if not user_title and editorial_copy and editorial_copy.book_title:
        generated_title = _formal_book_title(editorial_copy.book_title)
        if generated_title and generated_title != title:
            title = generated_title
            content, snapshot = _compose_book_content(
                title=title,
                chapters=chapters,
                source_refs_by_chapter=refs_by_chapter,
            )

    output = await _load_output(db, project_id=project_id)
    latest_version = output.versions[-1] if output and output.versions else None
    metadata_patch = _request_book_metadata_patch(req)
    book_metadata = _effective_book_metadata(
        project=project,
        stored=latest_version.manuscript_json if latest_version else None,
        patch=metadata_patch,
    )
    stale_titles = (
        _snapshot_stale_chapter_titles(output.versions[-1], snapshot, [])
        if output and output.versions
        else []
    )
    metadata_changed = bool(
        latest_version
        and metadata_patch
        and any(_stored_manuscript_metadata(latest_version.manuscript_json or {}).get(key, "") != value for key, value in metadata_patch.items())
    )
    title_or_style_changed = bool(
        output
        and latest_version
        and (
            (user_title and output.title != title)
            or (req.cover_style and _stored_cover_style(latest_version) != cover_style)
            or metadata_changed
        )
    )
    if output and output.versions and not req.force_regenerate and not stale_titles and not title_or_style_changed:
        return _output_response(output)

    if not output:
        output = MemoirBookOutput(
            project_id=project_id,
            plan_id=plan.id,
            title=title,
            status=MemoirOutputStatus.DRAFT.value,
            metadata_json={},
        )
        db.add(output)
        await db.flush()
    else:
        output.plan_id = plan.id
        output.title = title
        output.status = MemoirOutputStatus.DRAFT.value
        output.metadata_json = {
            **(output.metadata_json or {}),
            BOOK_TTS_CACHE_KEY: None,
        }

    manuscript = _manuscript_from_snapshot(
        title=title,
        content=content,
        snapshot=snapshot,
        editorial_copy=editorial_copy,
        cover_style_value=cover_style,
        metadata=book_metadata,
    )
    version_number = await _latest_output_version_number(db, output.id) + 1
    db.add(
        MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=plan.id,
            version=version_number,
            content=content,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript.model_dump(mode="json") if manuscript else {},
            created_by=MemoirCreatedBy.SYSTEM.value,
        )
    )
    await db.commit()
    refreshed = await _load_output(db, project_id=project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="全书稿保存失败")
    return _output_response(refreshed)


async def update_book_output(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookOutputUpdateRequest,
) -> MemoirBookOutputResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    snapshot = [item for item in (latest.chapter_snapshot_json or []) if isinstance(item, dict)]
    fields_set = getattr(req, "model_fields_set", set())
    title = _formal_book_title(req.title if "title" in fields_set and req.title is not None else output.title)
    cover_style = _cover_style(req.cover_style) if "cover_style" in fields_set and req.cover_style else _stored_cover_style(latest)
    metadata_patch = _request_book_metadata_patch(req)
    content = _compose_book_content_from_snapshot(title=title, snapshot=snapshot)
    latest.content = content
    latest.manuscript_json = _updated_manuscript_payload(
        title=title,
        stored=latest.manuscript_json or {},
        content=content,
        snapshot=snapshot,
        cover_style_value=cover_style,
        metadata_patch=metadata_patch,
    )
    output.title = title
    output.metadata_json = {
        **(output.metadata_json or {}),
        BOOK_TTS_CACHE_KEY: None,
    }
    await db.commit()
    refreshed = await _load_output(db, project_id=project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="全书稿保存失败")
    return _output_response(refreshed)


async def regenerate_book_output_editorial_copy(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookOutputEditorialCopyRegenerateRequest,
) -> MemoirBookOutputResponse:
    project = await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    snapshot = [item for item in (latest.chapter_snapshot_json or []) if isinstance(item, dict)]
    manuscript = _manuscript_response(
        title=output.title,
        content=latest.content,
        snapshot=snapshot,
        stored=latest.manuscript_json,
    )
    if not manuscript:
        raise HTTPException(status_code=400, detail="全书稿结构不可用")

    requested_ai_model = (req.ai_model or "").strip()
    ai_model = requested_ai_model or _project_ai_model(project)
    _remember_project_ai_model(project, req.ai_model)
    targets = _target_set(req.targets)
    chapter_ids = _chapter_id_set(req.chapter_ids)
    editorial_copy = await generate_manuscript_editorial_copy(
        title=output.title,
        snapshot=snapshot,
        ai_model=ai_model,
        targets=sorted(targets),
        chapter_ids=sorted(chapter_ids),
        feedback=req.feedback,
    )
    if not editorial_copy:
        raise HTTPException(status_code=503, detail="成书文案生成服务暂时不可用")

    _apply_editorial_copy(
        manuscript,
        editorial_copy,
        targets=targets,
        chapter_ids=chapter_ids,
    )
    latest.manuscript_json = manuscript.model_dump(mode="json")
    output.metadata_json = {
        **(output.metadata_json or {}),
        BOOK_TTS_CACHE_KEY: None,
    }
    await db.commit()
    refreshed = await _load_output(db, project_id=project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="全书稿保存失败")
    return _output_response(refreshed)


async def update_book_output_chapter_editorial_copy(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookOutputChapterEditorialCopyUpdateRequest,
) -> MemoirBookOutputResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    snapshot = [item for item in (latest.chapter_snapshot_json or []) if isinstance(item, dict)]
    manuscript = _manuscript_response(
        title=output.title,
        content=latest.content,
        snapshot=snapshot,
        stored=latest.manuscript_json,
    )
    if not manuscript:
        raise HTTPException(status_code=400, detail="全书稿结构不可用")
    _update_chapter_editorial_copy(
        manuscript,
        chapter_id,
        fields_set=getattr(req, "model_fields_set", set()),
        intro=req.intro,
        pull_quote=req.pull_quote,
        pull_quote_source_ref_id=req.pull_quote_source_ref_id,
    )

    latest.manuscript_json = manuscript.model_dump(mode="json")
    output.metadata_json = {
        **(output.metadata_json or {}),
        BOOK_TTS_CACHE_KEY: None,
    }
    await db.commit()
    refreshed = await _load_output(db, project_id=project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="全书稿保存失败")
    return _output_response(refreshed)


async def suggest_book_output_titles(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookTitleSuggestionRequest,
) -> MemoirBookTitleSuggestionResponse:
    project = await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, user_id=user_id, project_id=project_id)
    chapters = _require_confirmed_chapters(plan)
    base_title = _formal_book_title(plan.title)
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    _, snapshot = _compose_book_content(
        title=base_title,
        chapters=chapters,
        source_refs_by_chapter=refs_by_chapter,
    )
    requested_ai_model = (req.ai_model or "").strip()
    ai_model = requested_ai_model or _project_ai_model(project)
    _remember_project_ai_model(project, req.ai_model)
    generated = await generate_title_suggestions(
        title=base_title,
        snapshot=snapshot,
        count=req.count,
        ai_model=ai_model,
    ) if ai_model else None
    fallback = _fallback_title_suggestions(base_title, req.count)
    suggestions: list[str] = []
    for suggestion in [*(generated.suggestions if generated else []), *fallback]:
        title = _formal_book_title(suggestion)
        if title and title not in suggestions:
            suggestions.append(title)
        if len(suggestions) >= req.count:
            break
    return MemoirBookTitleSuggestionResponse(
        suggestions=suggestions,
        provider="ai" if generated else None,
        model=generated.model if generated else None,
    )


def _select_output_version(
    output: MemoirBookOutput,
    *,
    version_id: str | None = None,
) -> MemoirBookOutputVersion | None:
    versions = sorted(output.versions, key=lambda item: item.version)
    if not versions:
        return None
    if version_id:
        return next((version for version in versions if version.id == version_id), None)
    return versions[-1]


async def export_book_output_file(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    export_format: MemoirBookOutputExportFormat,
    layout: MemoirBookOutputExportLayout = MemoirBookOutputExportLayout.STANDARD,
    version_id: str | None = None,
    show_source_appendix: bool | None = None,
) -> ExportFile:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    version = _select_output_version(output, version_id=version_id)
    if not version:
        raise HTTPException(status_code=404, detail="全书版本不存在")
    content = version.content.strip()
    manuscript = _manuscript_response(
        title=output.title,
        content=content,
        snapshot=[item for item in (version.chapter_snapshot_json or []) if isinstance(item, dict)],
        stored=version.manuscript_json,
    )
    return render_book_output_export(
        title=output.title,
        version=version.version,
        content=content,
        manuscript=manuscript.model_dump(mode="json") if manuscript else {},
        show_source_appendix=show_source_appendix,
        export_format=export_format,
        layout=layout,
    )


async def synthesize_book_output_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookAudioRequest,
    tts_client: TTSClient | None = None,
) -> MemoirBookAudioResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    text = latest.content.strip()
    text_hash = source_text_hash(text)
    speed_ratio = effective_tts_speed_ratio(req.speed_ratio)
    cached = cached_audio_response(
        output.metadata_json or {},
        cache_key=BOOK_TTS_CACHE_KEY,
        text_hash=text_hash,
        voice=req.voice,
        speed_ratio=speed_ratio,
        force_regenerate=req.force_regenerate,
    )
    if cached:
        return cached

    synthesis = await synthesize_text_audio(
        db,
        user_id=user_id,
        text=text,
        voice=req.voice,
        speed_ratio=req.speed_ratio,
        tts_client=tts_client,
    )
    try:
        output.metadata_json = {
            **(output.metadata_json or {}),
            BOOK_TTS_CACHE_KEY: audio_cache_payload(synthesis, voice=req.voice, speed_ratio=speed_ratio),
        }
        db.add(output)
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio_files(synthesis.staged_audio)
        raise
    return audio_response(synthesis)
