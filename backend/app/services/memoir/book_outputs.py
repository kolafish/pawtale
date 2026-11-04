from __future__ import annotations

import asyncio
from datetime import UTC, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.database import async_session_maker
from app.models.projects import Project
from app.services import content_storage
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
    MemoirEditingJob,
)
from app.schemas.memoir_book import (
    MemoirBookCustomLayout,
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookAudioPlanRequest,
    MemoirBookOutputChapterEditorialCopyUpdateRequest,
    MemoirBookOutputEditorialCopyRegenerateRequest,
    MemoirBookOutputResponse,
    MemoirBookOutputUpdateRequest,
    MemoirBookManuscriptTemplate,
    MemoirBookOutputVersionResponse,
    MemoirBookOutputVersionSummaryResponse,
    MemoirBookTitleSuggestionRequest,
    MemoirBookTitleSuggestionResponse,
)
from app.services.memoir.featured_family_notes import attach_chapter_notes, export_text_with_notes
from app.services.file_exports import ExportFile
from app.services.memoir.access import load_memoir_project
from app.services.memoir import (
    book_editorial_actions,
    book_review_answer_state,
    book_reviews,
    completion_issues,
    chapter_generation,
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
    stored_manuscript_metadata,
    template_cover_style as _template_cover_style,
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
from app.services.memoir.book_output_export_cache import PreparedBookExport, render_cached_book_export
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter
from app.services.memoir.sections import lock_project_chapters
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.book_output_versions import (
    AUTOMATIC_EDITORIAL_COPY_KEY,
    automatic_editorial_copy_state,
    claim_editorial_copy_fields,
    append_book_output_version,
    book_output_version_title,
    book_editorial_copy_signature,
    chapter_editorial_copy_signature,
    retained_chapter_quotes,
    refresh_book_level_copy,
)
from app.services.memoir.project_ai_preferences import (
    resolve_project_ai_model,
    select_project_ai_model,
    project_transcript_polish_level,
)


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
        db.add(output)
    return True


def _current_book_snapshot(
    plan: MemoirBookPlan,
    *,
    source_refs_by_chapter: dict[str, list[dict[str, object]]] | None = None,
    blocked_chapter_ids: set[str] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        return [], ["章节规划"]
    chapters = included_book_chapters(plan)
    blocked_ids = blocked_chapter_ids or set()
    snapshot: list[dict[str, object]] = []
    unavailable_titles: list[str] = []
    for chapter in chapters:
        if chapter.id in blocked_ids:
            continue
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
            if (
                (version is None or not version.content.strip())
                and source_refs_by_chapter is not None
                and not source_refs_by_chapter.get(chapter.id)
            ):
                # Empty planned chapters are omitted by partial book assembly.
                # Once material arrives, the missing draft must block export.
                continue
            unavailable_titles.append(chapter.title)
            continue
        snapshot.append(
            chapter_snapshot(
                chapter,
                version,
                chapter_index=len(snapshot) + 1,
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
    blocked_chapter_ids: set[str] | None = None,
) -> list[str]:
    current_snapshot, unavailable_titles = _current_book_snapshot(
        plan,
        source_refs_by_chapter=source_refs_by_chapter,
        blocked_chapter_ids=blocked_chapter_ids,
    )
    latest_version = output.versions[-1] if output.versions else None
    return _snapshot_stale_chapter_titles(latest_version, current_snapshot, unavailable_titles)


async def _load_output(
    db: AsyncSession,
    *,
    project_id: str,
    for_update: bool = False,
) -> MemoirBookOutput | None:
    if for_update:
        await db.execute(select(MemoirBookOutput.id).where(
            MemoirBookOutput.project_id == project_id,
        ).with_for_update())
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
    if response.latest_version:
        await attach_chapter_notes(db, project_id, response.latest_version.manuscript)
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
    response = _output_version_response(version, fallback_title=output.title)
    await attach_chapter_notes(db, project_id, response.manuscript)
    return response


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
    pending_facts = await chapter_generation.pending_fact_chapter_ids(
        db, project_id=project_id, chapter_versions=readable_chapter_versions(plan, allow_partial=True),
    )
    return _output_response(
        output,
        stale_chapter_titles=(
            []
            if not pending_facts and (answer_application_current or editorial_revision_current)
            else _output_stale_chapter_titles(
                output,
                plan,
                source_refs_by_chapter=refs_by_chapter,
                blocked_chapter_ids=pending_facts | await completion_issues.open_blocking_book_chapter_ids(
                    db,
                    project_id=project_id,
                    all_chapter_ids=(chapter.id for chapter in included_book_chapters(plan)),
                ),
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
    output = await _load_output(db, project_id=project_id, for_update=True)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    if req.expected_version_id is not None and req.expected_version_id != latest.id:
        raise HTTPException(status_code=409, detail="全书已更新，请刷新核对后再保存")
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
    if "cover_style" in fields_set and req.cover_style:
        cover_style = _cover_style(req.cover_style)
    else:
        cover_style = _template_cover_style(req.manuscript_template) or _stored_cover_style(latest)
    metadata_patch = _request_book_metadata_patch(req)
    stored_metadata = stored_manuscript_metadata(latest.manuscript_json)
    claim_editorial_copy_fields(output, targets=[key for key in ("preface", "afterword", "back_cover_quote")
        if key in metadata_patch and metadata_patch[key] != stored_metadata[key]], chapter_fields={})
    content = _compose_book_content_from_snapshot(title=title, snapshot=snapshot)
    manuscript_json = _updated_manuscript_payload(
        title=title,
        stored=latest.manuscript_json or {},
        content=content,
        snapshot=snapshot,
        cover_style_value=cover_style,
        metadata_patch=metadata_patch,
        manuscript_template=req.manuscript_template,
        custom_layout=req.custom_layout,
    )
    if (req.manuscript_template is not None or req.custom_layout is not None) and not manuscript_json:
        raise HTTPException(status_code=400, detail="请先更新书稿，再选择版式")
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
    await db.commit()
    return await get_book_output(db, user_id=user_id, project_id=project_id, allow_admin=allow_admin)


async def regenerate_book_output_editorial_copy(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookOutputEditorialCopyRegenerateRequest,
    allow_admin: bool = False,
    automatic: bool = False,
    retry: bool = False,
) -> MemoirBookOutputResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await _load_output(db, project_id=project_id, for_update=True)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    snapshot = [item for item in (latest.chapter_snapshot_json or []) if isinstance(item, dict)]
    if not snapshot:
        raise HTTPException(
            status_code=409,
            detail="当前整本书没有可用章节。请在书稿页处理待确认事项，等后台更新书稿后再重写。",
        )
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
    ownership = automatic_editorial_copy_state(output, output.versions)
    base_ownership = (list(ownership.manual_targets), {key: list(fields) for key, fields in ownership.manual_chapter_fields.items()})
    state = ownership if automatic else None
    if state is not None:
        if state.signature_version < 2:
            # A new evidence policy requires one fresh automatic pass. Preserve
            # ownership, but do not translate an old checkpoint into a cache hit.
            state.chapter_input_signatures = {}
            state.signature_version = 2
        signature = book_editorial_copy_signature(output_title, snapshot)
        if state.input_signature == signature and (not retry or state.status == "succeeded"):
            await db.commit()
            return _output_response(output)
        signatures = {item["chapter_id"]: chapter_editorial_copy_signature(item) for item in snapshot}
        current_copy = {key for key, value in signatures.items() if state.chapter_input_signatures.get(key) == value}
        baseline = next((version for version in output.versions if version.version == state.processed_version), None)
        if (baseline and state.status == "succeeded"
                and state.input_signature == book_editorial_copy_signature(output_title, baseline.chapter_snapshot_json)
                and not refresh_book_level_copy(baseline.chapter_snapshot_json, snapshot, preserved=current_copy)):
            targets -= {"preface", "afterword", "back_cover_quote"}
        state.input_signature = signature
        targets -= set(state.manual_targets)
        chapter_ids = {
            chapter_id for chapter_id, signature in signatures.items()
            if (state.chapter_input_signatures.get(chapter_id) != signature
                or (retry and chapter_id in state.failed_chapter_ids))
            and not {"intro", "pull_quotes"}.issubset(state.manual_chapter_fields.get(chapter_id, []))
        }
        state.chapter_input_signatures = signatures
        state.failed_chapter_ids = [value for value in state.failed_chapter_ids if value in signatures
            and not {"intro", "pull_quotes"}.issubset(state.manual_chapter_fields.get(value, []))]
        for target, field in (("chapter_intros", "intro"), ("pull_quotes", "pull_quotes")):
            if all(field in state.manual_chapter_fields.get(chapter_id, []) for chapter_id in chapter_ids):
                targets.discard(target)
    protected_book_copy = stored_manuscript_metadata(latest.manuscript_json)
    protected_chapter_copy = {chapter.chapter_id: chapter.model_copy(deep=True) for chapter in manuscript.chapters}
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
        polish_level=project_transcript_polish_level(project),
    ) if targets else None
    if editorial_copy:
        generated = {item.chapter_id: item for item in editorial_copy.chapter_intros}
        eligible_chapters = {chapter.chapter_id for chapter in manuscript.chapters
            if not chapter_ids or chapter.chapter_id in chapter_ids}
        has_book_copy = any(target in targets and (getattr(editorial_copy, target)
                or (target == "back_cover_quote" and editorial_copy.valid_empty_back_cover_quote))
            for target in ("preface", "afterword", "back_cover_quote"))
        has_chapter_copy = any(
            item.chapter_id in eligible_chapters and any(
                target in targets and (getattr(item, attribute) or (target == "pull_quotes" and item.valid_empty_quote))
                and (state is None or field not in state.manual_chapter_fields.get(item.chapter_id, []))
                for target, attribute, field in (("chapter_intros", "intro", "intro"), ("pull_quotes", "pull_quote", "pull_quotes"))
            ) for item in generated.values()
        )
        if not has_book_copy and not has_chapter_copy:
            editorial_copy = None
    if not editorial_copy and not automatic:
        raise HTTPException(status_code=503, detail="成书文案生成服务暂时不可用")

    if editorial_copy:
        snapshots_by_id = {item["chapter_id"]: item for item in snapshot}
        for chapter in manuscript.chapters:
            if ("pull_quotes" in targets and (not chapter_ids or chapter.chapter_id in chapter_ids)
                    and (state is None or "pull_quotes" not in state.manual_chapter_fields.get(chapter.chapter_id, []))):
                chapter_snapshot = snapshots_by_id[chapter.chapter_id]
                chapter.pull_quotes = retained_chapter_quotes(
                    chapter.model_dump(), chapter_snapshot, polish_level=project_transcript_polish_level(project),
                )
        _apply_editorial_copy(manuscript, editorial_copy, targets=targets, chapter_ids=chapter_ids)
    # Legacy cleanup may supply defaults for blank fields. Preserve authored
    # choices unless this request explicitly regenerates that same field.
    protected_targets = set(ownership.manual_targets) - targets
    if "preface" in protected_targets:
        for section in manuscript.front_matter:
            if section.kind == "preface":
                section.content = protected_book_copy["preface"]
    if "afterword" in protected_targets:
        manuscript.back_matter.afterword.content = protected_book_copy["afterword"]
    if "back_cover_quote" in protected_targets:
        manuscript.render_settings.back_cover_quote = protected_book_copy["back_cover_quote"]
    if state is not None:
        for chapter in manuscript.chapters:
            original = protected_chapter_copy[chapter.chapter_id]
            fields = state.manual_chapter_fields.get(chapter.chapter_id, [])
            if "intro" in fields:
                chapter.intro = original.intro
            if "pull_quotes" in fields:
                chapter.pull_quotes = original.pull_quotes
        failed = set(state.failed_chapter_ids) - chapter_ids
        if not editorial_copy and targets:
            failed |= chapter_ids
        state.failed_chapter_ids = sorted(failed)
        state.status = "failed" if failed or (not editorial_copy and targets) else "succeeded"
    manuscript_json = manuscript.model_dump(mode="json") if editorial_copy else latest.manuscript_json
    review_metadata_changed = stored_manuscript_metadata(manuscript_json) != protected_book_copy
    parent_id = await db.scalar(select(MemoirEditingJob.id).where(
        MemoirEditingJob.dedupe_key == f"update-book-draft:{project_id}",
    )) if review_metadata_changed else None
    if parent_id:
        # The parent takes directory locks before writing its job/output. Keep
        # that order and commit the copy plus its wake-up together.
        await lock_project_chapters(db, project_id=project_id)
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
        if automatic and output is not None and output.versions:
            return _output_response(output)
        raise HTTPException(status_code=409, detail="生成期间全书稿已更新，请重试")
    latest = output.versions[-1]
    current_ownership = automatic_editorial_copy_state(output, output.versions)
    if (current_ownership.manual_targets, current_ownership.manual_chapter_fields) != base_ownership:
        if automatic:
            await db.commit()
            return _output_response(output)
        raise HTTPException(status_code=409, detail="生成期间文案选择已更新，请重试")
    if not automatic and editorial_copy:
        claim_editorial_copy_fields(output,
            targets=[key for key in ("preface", "afterword", "back_cover_quote") if key in targets
                and (getattr(editorial_copy, key) or (key == "back_cover_quote" and editorial_copy.valid_empty_back_cover_quote))],
            chapter_fields={chapter.chapter_id: [field for target, field in (("chapter_intros", "intro"), ("pull_quotes", "pull_quotes"))
                if target in targets and (field == "pull_quotes" or generated[chapter.chapter_id].intro)]
                for chapter in manuscript.chapters if chapter.chapter_id in generated
                and (not chapter_ids or chapter.chapter_id in chapter_ids)})
    if manuscript_json != latest.manuscript_json:
        latest = await append_book_output_version(
            db,
            output=output,
            content=latest.content,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript_json,
            created_by=MemoirCreatedBy.SYSTEM if automatic else MemoirCreatedBy.AI,
        )
        invalidate_book_output(output)
    if state is not None:
        state.processed_version = latest.version
        output.metadata_json = {**(output.metadata_json or {}), AUTOMATIC_EDITORIAL_COPY_KEY: state.model_dump(mode="json")}
    if parent_id:
        from app.services.memoir.automatic_book_draft import schedule_book_draft
        await schedule_book_draft(db, project_id=project_id, source="editorial_copy", settled=True)
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
    output = await _load_output(db, project_id=project_id, for_update=True)
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

    fields_set = req.model_fields_set
    claim_editorial_copy_fields(output, targets=[], chapter_fields={chapter_id: [
        field for field, supplied in (("intro", "intro" in fields_set),
            ("pull_quotes", bool({"pull_quote", "pull_quote_source_ref_id"} & fields_set))) if supplied
    ]})
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


async def prepare_book_output_export(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    export_format: MemoirBookOutputExportFormat,
    layout: MemoirBookOutputExportLayout = MemoirBookOutputExportLayout.STANDARD,
    version_id: str | None = None,
    show_source_appendix: bool | None = None,
    preview_template: MemoirBookManuscriptTemplate | None = None,
    preview_custom_layout_patch: dict[str, str] | None = None,
    allow_admin: bool = False,
) -> PreparedBookExport:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    if not output:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    query = select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.output_id == output.id)
    if version_id:
        query = query.where(MemoirBookOutputVersion.id == version_id)
    else:
        query = query.order_by(MemoirBookOutputVersion.version.desc()).limit(1)
    version = await db.scalar(query.execution_options(populate_existing=True))
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
    if (preview_template is not None or preview_custom_layout_patch) and not manuscript:
        raise HTTPException(status_code=400, detail="请先更新书稿，再预览版式")
    if manuscript and (preview_template is not None or preview_custom_layout_patch):
        manuscript = manuscript.model_copy(deep=True)
        if preview_template is not None:
            manuscript.profile.template = preview_template
            manuscript.render_settings.custom_layout = MemoirBookCustomLayout()
            matching_cover = _template_cover_style(preview_template)
            if matching_cover is not None:
                manuscript.render_settings.cover_style = matching_cover
        if preview_custom_layout_patch:
            manuscript.render_settings.custom_layout = manuscript.render_settings.custom_layout.model_copy(
                update=preview_custom_layout_patch
            )
        manuscript.schema_version = 5
    await attach_chapter_notes(db, project_id, manuscript)
    if manuscript and export_format == MemoirBookOutputExportFormat.TXT:
        content = export_text_with_notes(content, manuscript)
    return PreparedBookExport(
        owner_id=project.user_id, project_id=project_id, version_id=version.id,
        version=version.version, title=title, content=content, manuscript=manuscript,
        export_format=export_format,
        layout=layout if export_format == MemoirBookOutputExportFormat.PDF else MemoirBookOutputExportLayout.STANDARD,
        show_source_appendix=(
            show_source_appendix if show_source_appendix is not None
            else manuscript.render_settings.show_source_appendix if manuscript else True
        ),
        render_date=version.created_at.replace(tzinfo=UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat(),
    )


_book_export_tasks: dict[str, asyncio.Task[ExportFile]] = {}


async def _render_project_book_export(prepared: PreparedBookExport) -> ExportFile:
    # Reuse the project lock and directory so deletion includes cached PDFs and
    # cannot race a render that outlives its HTTP request.
    async with content_storage.project_content_lock(prepared.project_id):
        async with async_session_maker() as db:
            exists = await db.scalar(select(Project.id).where(
                Project.id == prepared.project_id, Project.user_id == prepared.owner_id,
            ))
        if not exists:
            raise HTTPException(status_code=404, detail="项目不存在")
        worker = asyncio.get_running_loop().run_in_executor(
            None, lambda: render_cached_book_export(prepared, prepared.cache_key, render_book_output_export)
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise


def _finish_book_export(cache_key: str, task: asyncio.Task[ExportFile]) -> None:
    _book_export_tasks.pop(cache_key, None)
    if not task.cancelled():
        task.exception()  # Retrieve failures when an HTTP waiter has already timed out.


async def export_book_output_file(
    prepared: PreparedBookExport, *, timeout_seconds: float | None = None,
) -> ExportFile:
    deadline_seconds = max(
        0.01,
        timeout_seconds
        if timeout_seconds is not None
        else get_settings().memoir_book_export_deadline_seconds,
    )
    cache_key = await asyncio.to_thread(lambda: prepared.cache_key)
    task = _book_export_tasks.get(cache_key)
    if task is None:
        task = asyncio.create_task(_render_project_book_export(prepared))
        _book_export_tasks[cache_key] = task
        task.add_done_callback(lambda finished: _finish_book_export(cache_key, finished))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=deadline_seconds)
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
    allow_admin: bool = False,
) -> MemoirBookAudioResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    from app.services.memoir.book_audio_jobs import complete_book_audio

    return await complete_book_audio(
        db, user_id=user_id, project_id=project_id,
        req=MemoirBookAudioPlanRequest(
            version_id=latest.id, voice=req.voice, speed_ratio=req.speed_ratio,
            force_regenerate=req.force_regenerate, retry_failed=True,
        ),
        allow_admin=allow_admin,
    )
