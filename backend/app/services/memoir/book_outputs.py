from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.config import get_settings
from app.domain.memoir.types import (
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.models.memoir import (
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
    MemoirBookOutputResponse,
    MemoirBookOutputUpdateRequest,
    MemoirBookOutputVersionResponse,
    MemoirBookOutputVersionSummaryResponse,
    MemoirBookTitleSuggestionRequest,
    MemoirBookTitleSuggestionResponse,
)
from app.services.file_exports import ExportFile
from app.services.memoir.access import load_memoir_project
from app.services.memoir import (
    book_editorial_actions,
    book_review_answer_state,
    book_reviews,
)
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
    apply_editorial_copy as _apply_editorial_copy,
    chapter_id_set as _chapter_id_set,
    cover_style as _cover_style,
    fallback_title_suggestions as _fallback_title_suggestions,
    formal_book_title as _formal_book_title,
    editable_manuscript as _editable_manuscript,
    manuscript_response as _manuscript_response,
    request_book_metadata_patch as _request_book_metadata_patch,
    stored_cover_style as _stored_cover_style,
    target_set as _target_set,
    update_chapter_editorial_copy as _update_chapter_editorial_copy,
    updated_manuscript_payload as _updated_manuscript_payload,
)
from app.services.memoir.book_output_snapshots import (
    load_book_plan_drafts,
    latest_chapter_version,
    readable_chapter_versions,
    book_content_from_chapter_snapshots,
    chapter_snapshot,
    legacy_chapter_version_needs_upgrade,
    protected_legacy_chapter_version,
)
from app.services.memoir.book_plan_sources import included_book_chapters
from app.services.memoir.book_output_export import (
    MemoirBookOutputExportFormat,
    MemoirBookOutputExportLayout,
    render_book_output_export,
)
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.book_output_versions import (
    append_book_output_version,
    book_output_version_title,
)
from app.services.memoir.project_ai_preferences import (
    resolve_project_ai_model,
    select_project_ai_model,
)
from app.services.memoir.voice_tts_types import TTSClient


BOOK_TTS_CACHE_KEY = "book_tts"
SNAPSHOT_COMPARE_KEYS = (
    "chapter_id",
    "chapter_title",
    "chapter_sort_order",
    "draft_id",
    "draft_version_id",
    "draft_version",
    "content_char_count",
    "section_count",
)


def _output_version_response(
    version: MemoirBookOutputVersion,
    *,
    fallback_title: str,
) -> MemoirBookOutputVersionResponse:
    snapshot = [
        item for item in (version.chapter_snapshot_json or [])
        if isinstance(item, dict)
    ]
    title = book_output_version_title(version, fallback=fallback_title)
    manuscript = _manuscript_response(
        title=title,
        content=version.content,
        snapshot=snapshot,
        stored=version.manuscript_json,
    )
    return MemoirBookOutputVersionResponse(
        id=version.id,
        version=version.version,
        content=version.content,
        manuscript=manuscript,
        created_by=MemoirCreatedBy(version.created_by),
        created_at=version.created_at,
    )


def _output_version_summary_response(
    version: MemoirBookOutputVersion,
) -> MemoirBookOutputVersionSummaryResponse:
    return MemoirBookOutputVersionSummaryResponse(
        id=version.id,
        version=version.version,
        created_by=MemoirCreatedBy(version.created_by),
        created_at=version.created_at,
    )


def _output_response(
    output: MemoirBookOutput,
    *,
    stale_chapter_titles: list[str] | None = None,
) -> MemoirBookOutputResponse:
    versions = [
        _output_version_summary_response(version)
        for version in output.versions
    ]
    latest_version = (
        _output_version_response(
            output.versions[-1],
            fallback_title=output.title,
        )
        if output.versions
        else None
    )
    stale_titles = stale_chapter_titles or []
    return MemoirBookOutputResponse(
        id=output.id,
        project_id=output.project_id,
        plan_id=output.plan_id,
        title=output.title,
        status=MemoirOutputStatus(output.status),
        is_stale=bool(stale_titles),
        stale_chapter_titles=stale_titles,
        latest_version=latest_version,
        versions=versions,
        created_at=output.created_at,
        updated_at=output.updated_at,
    )


def _compose_book_content(
    *,
    title: str,
    chapters: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]],
    source_refs_by_chapter: dict[str, list[dict[str, object]]] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    snapshot: list[dict[str, object]] = []
    for index, (chapter, version) in enumerate(chapters, start=1):
        snapshot.append(
            chapter_snapshot(
                chapter,
                version,
                chapter_index=index,
                source_refs=(source_refs_by_chapter or {}).get(chapter.id),
            )
        )
    return book_content_from_chapter_snapshots(title=title, snapshots=snapshot), snapshot


def _compose_book_content_from_snapshot(
    *,
    title: str,
    snapshot: list[dict[str, object]],
) -> str:
    return book_content_from_chapter_snapshots(
        title=title,
        snapshots=[item for item in snapshot if isinstance(item, dict)],
    )


def _replace_book_content_title(content: str, title: str) -> str:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        stripped = line.lstrip()
        heading = stripped[: len(stripped) - len(stripped.lstrip("#"))]
        spacer = " " if heading and stripped[len(heading):].startswith(" ") else ""
        indentation = line[: len(line) - len(stripped)]
        lines[index] = f"{indentation}{heading}{spacer}{title}"
        return "\n".join(lines)
    return title


async def synchronize_book_output_title(
    db: AsyncSession,
    *,
    project_id: str,
    title: str,
    created_by: MemoirCreatedBy = MemoirCreatedBy.USER,
) -> bool:
    canonical_title = title.strip()
    if not canonical_title:
        raise ValueError("回忆录名称不能为空")
    output = await _load_output(db, project_id=project_id)
    if not output:
        return False
    if not output.versions:
        if output.title != canonical_title:
            output.title = canonical_title
            output.updated_at = utcnow_naive()
            db.add(output)
        return True
    latest = output.versions[-1]
    snapshot = [
        item for item in (latest.chapter_snapshot_json or [])
        if isinstance(item, dict)
    ]
    content = _replace_book_content_title(latest.content, canonical_title)
    manuscript_json = _updated_manuscript_payload(
        title=canonical_title,
        stored=latest.manuscript_json or {},
        content=content,
        snapshot=snapshot,
        cover_style_value=_stored_cover_style(latest),
    )
    version_changed = content != latest.content or manuscript_json != latest.manuscript_json
    title_changed = output.title != canonical_title
    if version_changed:
        await append_book_output_version(
            db,
            output=output,
            content=content,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript_json,
            created_by=created_by,
        )
    if title_changed:
        output.title = canonical_title
        output.updated_at = utcnow_naive()
    if version_changed or title_changed:
        invalidate_book_output(output)
        output.metadata_json = {
            **(output.metadata_json or {}),
            BOOK_TTS_CACHE_KEY: None,
        }
        db.add(output)
    return True


def _current_book_snapshot(
    plan: MemoirBookPlan,
    *,
    source_refs_by_chapter: dict[str, list[dict[str, object]]] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        return [], ["章节规划"]
    chapters = included_book_chapters(plan)
    snapshot: list[dict[str, object]] = []
    unavailable_titles: list[str] = []
    for index, chapter in enumerate(chapters, start=1):
        version = latest_chapter_version(chapter.draft)
        if (
            not chapter.draft
            or not version
            or not version.content.strip()
            or (
                legacy_chapter_version_needs_upgrade(version)
                and not protected_legacy_chapter_version(
                    chapter.draft,
                    version,
                )
            )
        ):
            unavailable_titles.append(chapter.title)
            continue
        snapshot.append(
            chapter_snapshot(
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
    allow_admin: bool = False,
) -> MemoirBookOutputResponse:
    response = await book_output_response_or_none(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    return response


async def get_book_output_version(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    version_id: str,
    allow_admin: bool = False,
) -> MemoirBookOutputVersionResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await _load_output(db, project_id=project_id)
    if not output:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    version = next(
        (item for item in output.versions if item.id == version_id),
        None,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="书稿版本不存在")
    return _output_version_response(version, fallback_title=output.title)


async def book_output_response_or_none(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> MemoirBookOutputResponse | None:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await _load_output(db, project_id=project_id)
    if not output:
        return None
    plan = await load_book_plan_drafts(db, project_id=project_id)
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    try:
        current_review = await book_reviews.get_current_book_review(
            db,
            project_id=project_id,
        )
    except HTTPException as exc:
        if exc.status_code not in {409, 422}:
            raise
        current_review = None
    answer_application_current = False
    editorial_revision_current = False
    if current_review is not None:
        answer_context = await book_review_answer_state.load_answer_context(
            db,
            review=current_review,
        )
        answer_application_current = (
            book_review_answer_state.marker_matches_context(
                output=output,
                version=output.versions[-1] if output.versions else None,
                context=answer_context,
            )
        )
        editorial_revision_current = (
            book_editorial_actions.marker_matches_review(
                output=output,
                version=output.versions[-1] if output.versions else None,
                review=current_review,
                answers_signature=answer_context.answers_signature,
            )
        )
    return _output_response(
        output,
        stale_chapter_titles=(
            []
            if answer_application_current or editorial_revision_current
            else _output_stale_chapter_titles(
                output,
                plan,
                source_refs_by_chapter=refs_by_chapter,
            )
        ),
    )


async def update_book_output(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookOutputUpdateRequest,
    allow_admin: bool = False,
) -> MemoirBookOutputResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    snapshot = [item for item in (latest.chapter_snapshot_json or []) if isinstance(item, dict)]
    fields_set = getattr(req, "model_fields_set", set())
    title = (
        req.title.strip()
        if "title" in fields_set and req.title is not None
        else project.title.strip()
    )
    if project.title != title:
        project.title = title
        project.updated_at = utcnow_naive()
    cover_style = _cover_style(req.cover_style) if "cover_style" in fields_set and req.cover_style else _stored_cover_style(latest)
    metadata_patch = _request_book_metadata_patch(req)
    content = _compose_book_content_from_snapshot(title=title, snapshot=snapshot)
    manuscript_json = _updated_manuscript_payload(
        title=title,
        stored=latest.manuscript_json or {},
        content=content,
        snapshot=snapshot,
        cover_style_value=cover_style,
        metadata_patch=metadata_patch,
    )
    version_changed = content != latest.content or manuscript_json != latest.manuscript_json
    title_changed = output.title != title
    if version_changed:
        await append_book_output_version(
            db,
            output=output,
            content=content,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript_json,
            created_by=MemoirCreatedBy.USER,
        )
    if title_changed:
        output.title = title
    if version_changed or title_changed:
        invalidate_book_output(output)
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
    allow_admin: bool = False,
) -> MemoirBookOutputResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    snapshot = [item for item in (latest.chapter_snapshot_json or []) if isinstance(item, dict)]
    manuscript = _editable_manuscript(
        title=project.title,
        content=latest.content,
        snapshot=snapshot,
        stored=latest.manuscript_json,
    )
    if not manuscript:
        raise HTTPException(status_code=400, detail="全书稿结构不可用")

    base_version_id = latest.id
    output_title = project.title
    ai_model = select_project_ai_model(project, req.ai_model)
    targets = _target_set(req.targets)
    chapter_ids = _chapter_id_set(req.chapter_ids)
    # Persist a changed model preference and release all locks before the
    # provider call. The output row is locked and the base version revalidated
    # immediately before appending the generated version.
    await db.commit()
    editorial_copy = await generate_manuscript_editorial_copy(
        title=output_title,
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
    manuscript_json = manuscript.model_dump(mode="json")
    await db.execute(
        select(MemoirBookOutput.id)
        .where(MemoirBookOutput.id == output.id)
        .with_for_update()
    )
    output = await _load_output(db, project_id=project_id)
    if (
        output is None
        or not output.versions
        or output.versions[-1].id != base_version_id
    ):
        raise HTTPException(status_code=409, detail="生成期间全书稿已更新，请重试")
    latest = output.versions[-1]
    if manuscript_json != latest.manuscript_json:
        await append_book_output_version(
            db,
            output=output,
            content=latest.content,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript_json,
            created_by=MemoirCreatedBy.AI,
        )
    invalidate_book_output(output)
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
    allow_admin: bool = False,
) -> MemoirBookOutputResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    snapshot = [item for item in (latest.chapter_snapshot_json or []) if isinstance(item, dict)]
    manuscript = _editable_manuscript(
        title=project.title,
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

    manuscript_json = manuscript.model_dump(mode="json")
    if manuscript_json != latest.manuscript_json:
        await append_book_output_version(
            db,
            output=output,
            content=latest.content,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript_json,
            created_by=MemoirCreatedBy.USER,
        )
    invalidate_book_output(output)
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
    allow_admin: bool = False,
) -> MemoirBookTitleSuggestionResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    plan = await load_book_plan_drafts(db, project_id=project_id)
    chapters = readable_chapter_versions(plan)
    base_title = _formal_book_title(project.title)
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    _, snapshot = _compose_book_content(
        title=base_title,
        chapters=chapters,
        source_refs_by_chapter=refs_by_chapter,
    )
    ai_model = resolve_project_ai_model(project, req.ai_model)
    await db.commit()
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
    allow_admin: bool = False,
    timeout_seconds: float | None = None,
) -> ExportFile:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    version = _select_output_version(output, version_id=version_id)
    if not version:
        raise HTTPException(status_code=404, detail="全书版本不存在")
    content = version.content.strip()
    title = book_output_version_title(version, fallback=output.title)
    manuscript = _manuscript_response(
        title=title,
        content=content,
        snapshot=[item for item in (version.chapter_snapshot_json or []) if isinstance(item, dict)],
        stored=version.manuscript_json,
    )
    deadline_seconds = max(
        0.01,
        timeout_seconds
        if timeout_seconds is not None
        else get_settings().memoir_book_export_deadline_seconds,
    )
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                render_book_output_export,
                title=title,
                version=version.version,
                content=content,
                manuscript=manuscript.model_dump(mode="json") if manuscript else {},
                show_source_appendix=show_source_appendix,
                export_format=export_format,
                layout=layout,
            ),
            timeout=deadline_seconds,
        )
    except TimeoutError as exc:
        minutes = max(1, round(deadline_seconds / 60))
        raise HTTPException(
            status_code=504,
            detail=f"书稿导出超过 {minutes} 分钟，请稍后重试。",
        ) from exc


async def synthesize_book_output_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookAudioRequest,
    tts_client: TTSClient | None = None,
    allow_admin: bool = False,
) -> MemoirBookAudioResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
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
