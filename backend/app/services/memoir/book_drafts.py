from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.schemas.memoir_book import (
    MemoirAudioPlanRequest,
    MemoirBookAudioPlanResponse,
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookChapterDraftRevisionRequest,
    MemoirBookChapterDraftResponse,
    MemoirBookChapterDraftVersionResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio_jobs import AudioSource, complete_audio_plan, prepare_audio_plan
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.book_output_snapshots import (
    normalized_section_snapshots,
    sectioned_chapter_content,
)
from app.services.memoir import chapter_generation


def _version_response(
    version: MemoirBookChapterDraftVersion,
) -> MemoirBookChapterDraftVersionResponse:
    return MemoirBookChapterDraftVersionResponse(
        id=version.id,
        draft_id=version.draft_id,
        project_id=version.project_id,
        chapter_id=version.chapter_id,
        version=version.version,
        content=version.content,
        sections=list(version.section_snapshot_json),
        created_by=MemoirCreatedBy(version.created_by),
        model=version.model,
        prompt_snapshot=version.prompt_snapshot,
        feedback=version.feedback,
        created_at=version.created_at,
    )


def _draft_response(
    draft: MemoirBookChapterDraft,
) -> MemoirBookChapterDraftResponse:
    versions = [
        _version_response(version)
        for version in sorted(
            draft.versions,
            key=lambda item: (item.version, item.created_at, item.id),
        )
    ]
    return MemoirBookChapterDraftResponse(
        id=draft.id,
        project_id=draft.project_id,
        chapter_id=draft.chapter_id,
        status=MemoirBookChapterDraftStatus(draft.status),
        metadata_json=draft.metadata_json or {},
        pending_source_count=0,
        removed_source_count=0,
        latest_version=versions[-1] if versions else None,
        versions=versions,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


async def _load_chapter(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    allow_admin: bool = False,
) -> MemoirBookPlanChapter:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .join(MemoirBookPlan, MemoirBookPlanChapter.plan_id == MemoirBookPlan.id)
        .where(
            MemoirBookPlan.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
        .options(
            selectinload(MemoirBookPlanChapter.plan),
            selectinload(MemoirBookPlanChapter.draft).selectinload(
                MemoirBookChapterDraft.versions
            ),
        )
        .execution_options(populate_existing=True)
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


def _require_confirmed_plan(chapter: MemoirBookPlanChapter) -> None:
    if chapter.plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")


async def _load_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookChapterDraft | None:
    return await db.scalar(
        select(MemoirBookChapterDraft)
        .where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
        .options(selectinload(MemoirBookChapterDraft.versions))
        .execution_options(populate_existing=True)
    )


async def get_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    allow_admin: bool = False,
) -> MemoirBookChapterDraftResponse:
    await _load_chapter(
        db,
        user_id=user_id,
        project_id=project_id,
        chapter_id=chapter_id,
        allow_admin=allow_admin,
    )
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="章节稿不存在")
    return _draft_response(draft)


async def chapter_draft_responses_by_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: list[str],
) -> dict[str, MemoirBookChapterDraftResponse | None]:
    responses: dict[str, MemoirBookChapterDraftResponse | None] = {
        chapter_id: None
        for chapter_id in chapter_ids
    }
    if not chapter_ids:
        return responses
    drafts = (
        await db.execute(
            select(MemoirBookChapterDraft)
            .where(
                MemoirBookChapterDraft.project_id == project_id,
                MemoirBookChapterDraft.chapter_id.in_(chapter_ids),
            )
            .options(selectinload(MemoirBookChapterDraft.versions))
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    responses.update({
        draft.chapter_id: _draft_response(draft)
        for draft in drafts
    })
    return responses


async def confirm_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    allow_admin: bool = False,
) -> MemoirBookChapterDraftResponse:
    chapter = await _load_chapter(
        db,
        user_id=user_id,
        project_id=project_id,
        chapter_id=chapter_id,
        allow_admin=allow_admin,
    )
    _require_confirmed_plan(chapter)
    await chapter_generation.confirm_chapter_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    await db.commit()
    refreshed = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if refreshed is None:
        raise HTTPException(status_code=500, detail="章节稿确认失败")
    return _draft_response(refreshed)


async def revise_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterDraftRevisionRequest,
    allow_admin: bool = False,
) -> MemoirBookChapterDraftResponse:
    await chapter_generation.lock_chapter_draft_write(db, project_id=project_id, chapter_id=chapter_id)
    chapter = await _load_chapter(
        db,
        user_id=user_id,
        project_id=project_id,
        chapter_id=chapter_id,
        allow_admin=allow_admin,
    )
    _require_confirmed_plan(chapter)
    context = await chapter_generation.load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    draft = await _load_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if draft is None or not draft.versions:
        raise HTTPException(status_code=404, detail="章节稿不存在")
    latest = max(
        draft.versions,
        key=lambda item: (item.version, item.created_at, item.id),
    )
    if latest.id != req.expected_version_id:
        raise HTTPException(status_code=409, detail="正文已更新，请刷新后再修改")
    if req.expected_input_signature is not None and req.expected_input_signature != context.input_signature:
        raise HTTPException(status_code=409, detail="配对提问或素材已变化，请刷新核对后再保存")
    current = chapter_generation.chapter_draft_is_current(context, latest)
    question_changes = chapter_generation.chapter_draft_question_changes(context, latest) if not current else set()
    edited_section_ids = {revision.section_id for revision in req.sections
        if any(isinstance(item, dict) and item.get("section_id") == revision.section_id
            and item.get("content") != revision.content for item in latest.section_snapshot_json)}
    reviewed_questions = question_changes & edited_section_ids
    if not current and not (reviewed_questions and req.expected_input_signature == context.input_signature):
        raise HTTPException(status_code=409, detail="本章素材已变化，请先重新整理正文")
    latest_sections = list(latest.section_snapshot_json)
    if not latest_sections:
        raise HTTPException(
            status_code=409,
            detail="当前正文还没有小节结构，请先重新整理本章",
        )
    expected_section_ids = [
        str(section.get("section_id") or "")
        for section in latest_sections
        if isinstance(section, dict)
    ]
    revised_section_ids = [
        section.section_id for section in req.sections
    ]
    if (
        len(expected_section_ids) != len(latest_sections)
        or revised_section_ids != expected_section_ids
    ):
        raise HTTPException(
            status_code=409,
            detail="本章小节已经变化，请刷新后再修改",
        )
    revised_sections: list[dict] = []
    legacy_user_edit = latest.created_by == MemoirCreatedBy.USER.value and not any(
        isinstance(item, dict) and item.get("user_edited") is True for item in latest_sections)
    for previous, revision in zip(
        latest_sections,
        req.sections,
        strict=True,
    ):
        revised = dict(previous)
        if legacy_user_edit:
            revised["user_edited"] = True
        if revision.content != previous.get("content"):
            # A user version copies every section. Record the actual edited
            # section so later automatic updates can retain the other prose.
            revised.pop("body_digest", None)
            revised.pop("body_digest_hash", None)
            revised["user_edited"] = True
            revised["user_edited_at"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
        revised["content"] = revision.content
        if revision.section_id in reviewed_questions:
            section = next(item for item in context.sections if item.section_id == revision.section_id)
            revised["input_signature"] = chapter_generation._section_input_signature(context, section)
        revised_sections.append(revised)
    content = "\n\n".join(
        section["content"] for section in revised_sections
    )
    if revised_sections == latest_sections:
        return _draft_response(draft)
    current_materials = {item["material_id"]: item for item in chapter_generation._snapshot(
        context, used_material_ids=set(latest.used_material_ids_json))} if reviewed_questions else {}
    current_version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=latest.version + 1,
        content=content,
        created_by=MemoirCreatedBy.USER.value,
        model=None,
        prompt_snapshot=latest.prompt_snapshot,
        feedback="用户按小节修改正文",
        input_signature=(context.input_signature
            if question_changes <= reviewed_questions else latest.input_signature),
        used_material_ids_json=list(latest.used_material_ids_json),
        material_snapshot_json=[
            current_materials[saved["material_id"]]
            if isinstance(saved, dict) and saved.get("section_id") in reviewed_questions else saved
            for saved in latest.material_snapshot_json
        ],
        section_snapshot_json=revised_sections,
    )
    db.add(current_version)
    await db.flush()
    draft.status = MemoirBookChapterDraftStatus.DRAFT.value
    draft.updated_at = datetime.now(UTC).replace(tzinfo=None)
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    if output is not None:
        invalidate_book_output(output)
        db.add(output)
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=project_id, source="manual_draft", settled=True)
    await db.commit()
    refreshed = await _load_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if refreshed is None:
        raise HTTPException(status_code=500, detail="章节正文保存失败")
    return _draft_response(refreshed)


async def _chapter_audio_source(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str,
    req: MemoirAudioPlanRequest, section_id: str | None = None, allow_admin: bool = False,
) -> AudioSource:
    chapter = await _load_chapter(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id, allow_admin=allow_admin)
    _require_confirmed_plan(chapter)
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if draft is None or not draft.versions:
        raise HTTPException(400, "章节稿为空，无法朗读")
    if req.version_id:
        version = next((v for v in draft.versions if v.id == req.version_id), None)
        if version is None:
            raise HTTPException(404, "章节稿版本不存在")
    else:
        version = max(draft.versions, key=lambda v: (v.version, v.created_at, v.id))
    sections = normalized_section_snapshots(version.section_snapshot_json)
    if section_id is None:
        text = sectioned_chapter_content(sections) or version.content.strip()
    else:
        section = next((s for s in sections if s["section_id"] == section_id), None)
        if section is None:
            raise HTTPException(404, "小节正文不存在")
        text = f"{section['title']}\n\n{section['content']}".strip()
    return AudioSource(version.id, text)


async def prepare_chapter_draft_audio(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str,
    req: MemoirAudioPlanRequest, section_id: str | None = None, allow_admin: bool = False,
) -> MemoirBookAudioPlanResponse:
    source = await _chapter_audio_source(
        db, user_id=user_id, project_id=project_id, chapter_id=chapter_id,
        section_id=section_id, req=req, allow_admin=allow_admin,
    )
    return await prepare_audio_plan(db, user_id=user_id, project_id=project_id, source=source, req=req, allow_admin=allow_admin)


async def synthesize_chapter_draft_audio(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str,
    req: MemoirBookAudioRequest, section_id: str | None = None, allow_admin: bool = False,
) -> MemoirBookAudioResponse:
    plan_req = MemoirAudioPlanRequest(**req.model_dump(), retry_failed=True)
    source = await _chapter_audio_source(
        db, user_id=user_id, project_id=project_id, chapter_id=chapter_id,
        section_id=section_id, req=plan_req, allow_admin=allow_admin,
    )
    return await complete_audio_plan(db, user_id=user_id, project_id=project_id, source=source, req=plan_req, allow_admin=allow_admin)
