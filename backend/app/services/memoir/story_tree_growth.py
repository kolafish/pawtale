from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.config import get_settings
from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirSectionMaterialAssignedBy,
    MemoirSessionGoal,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirEditingJob,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookPlanGenerateRequest
from app.schemas.memoir_sections import (
    MemoirSectionMaterialPlacementRequest,
    MemoirSectionRegenerateRequest,
    MemoirSectionVersionReference,
)
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir import automation_events, editing_jobs
from app.services.memoir.book_plan_sources import load_plan_with_chapters
from app.services.memoir.book_plan_write_workflow import generate_book_plan
from app.services.memoir.chapter_split import split_large_chapters
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.material_organization import material_organization_candidates
from app.services.memoir.material_roles import is_ready_story_material
from app.services.memoir.project_ai_preferences import load_project_ai_model
from app.services.memoir.section_regeneration import (
    SectionRegenerationConflict,
    SectionRegenerationNeedsMaterial,
    regenerate_chapter_sections,
)
from app.services.memoir.sections import place_material
from app.services.memoir.semantic_state import semantic_json_signature
from app.services.memoir.transcript_projection import (
    current_transcript_version_id,
    current_utterance_text,
    has_story_content,
    source_text_hash,
)


SYSTEM_PROMPT = (
    "你是一位克制而有整体感的中文回忆录结构编辑。"
    "请把本次新讲述作为一个整体理解，只在确有新主题时生长章节或小节，"
    "不要改名、删除、移动或重排已有结构。"
)
SETTLE_SECONDS = 3
MAX_UNASSIGNED_ATTEMPTS = 2
ALLOWED_ACTIONS = {
    "assign_existing",
    "create_section",
    "create_chapter",
    "leave_unassigned",
}


class _GrowthGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_indices: list[int] = Field(default_factory=list)
    action: str
    section_id: str | None = None
    chapter_id: str | None = None
    new_chapter_title: str | None = None
    new_section_title: str | None = None
    confidence: float | None = None
    reason: str = ""


class _GrowthOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[_GrowthGroup] = Field(default_factory=list)


@dataclass(frozen=True)
class GrowthGroup:
    utterance_ids: list[str]
    action: str
    section_id: str | None = None
    chapter_id: str | None = None
    new_chapter_title: str | None = None
    new_section_title: str | None = None
    confidence: float | None = None
    reason: str = ""


class DeferredSource(BaseModel):
    text_hash: str
    status: Literal["pending", "needs_user"]
    reason: str
    attempts: int = 0


class GrowthBatch(BaseModel):
    groups: list[GrowthGroup] = Field(default_factory=list)
    sources: dict[str, tuple[str, str | None]]
    section_versions: dict[str, int]
    section_chapters: dict[str, str]
    chapter_updated_at: dict[str, datetime]


class StoryGrowthProgress(BaseModel):
    initial_sections: dict[str, list[MemoirSectionVersionReference]] = Field(default_factory=dict)
    completed_initial_chapters: list[str] = Field(default_factory=list)
    needs_material_chapters: list[str] = Field(default_factory=list)
    structure_conflicts: list[str] = Field(default_factory=list)
    batch: GrowthBatch | None = None
    deferred: dict[str, DeferredSource] = Field(default_factory=dict)
    placed_ids: list[str] = Field(default_factory=list)


def _progress(job: MemoirEditingJob) -> StoryGrowthProgress:
    return StoryGrowthProgress.model_validate((job.result_json or {}).get("growth", {}))


async def _save_progress(db: AsyncSession, job: MemoirEditingJob, progress: StoryGrowthProgress) -> None:
    if not await editing_jobs.update_editing_job_progress(
        db,
        job,
        {**(job.result_json or {}), "growth": progress.model_dump(mode="json")},
    ):
        raise editing_jobs.EditingJobProgressLost


def _clean_text(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _confidence(value: float | None) -> float | None:
    if value is None:
        return None
    return min(1.0, max(0.0, value))


def _decode_reference(reference_map: ModelReferenceMap, value: str | None) -> str | None:
    if not value:
        return None
    try:
        return reference_map.decode(value)
    except ValueError:
        return None


def build_story_growth_prompt(
    *,
    utterances: list[MemoirVoiceUtterance],
    chapters: list[MemoirBookPlanChapter],
    sections: list[MemoirSection],
) -> tuple[str, ModelReferenceMap, ModelReferenceMap]:
    chapter_refs = ModelReferenceMap("C", (chapter.id for chapter in chapters))
    section_refs = ModelReferenceMap("S", (section.id for section in sections))
    chapter_titles = {chapter.id: chapter.title for chapter in chapters}
    chapter_candidates, section_candidates = material_organization_candidates(
        chapters=chapters,
        sections=sections,
    )
    source_payload = [
        {"source_index": index, "text": current_utterance_text(utterance)}
        for index, utterance in enumerate(utterances, start=1)
    ]
    chapter_payload = [
        {
            "chapter_id": chapter_refs.encode(chapter.id),
            "chapter_title": chapter.title,
        }
        for chapter in chapter_candidates
    ]
    section_payload = [
        {
            "section_id": section_refs.encode(section.id),
            "chapter_id": chapter_refs.encode(section.chapter_id),
            "chapter_title": chapter_titles.get(section.chapter_id, ""),
            "section_title": section.title,
            "existing_material_examples": section.existing_material_examples,
        }
        for section in section_candidates
    ]
    prompt = (
        "请整理本次新增的全部讲述。可以把多条讲述作为一组，但每条讲述只能出现一次。\n"
        "优先归入已有小节；已有章节下出现明确的新事件或新主题时才新建小节；"
        "只有出现无法归入任何已有章节的人生阶段或长期主题时才新建章节；"
        "不确定时暂不归类。标题应具体、自然、直接来自讲述含义，不使用“相关经历”等空泛表达。\n\n"
        f"本次新讲述：\n{json.dumps(source_payload, ensure_ascii=False)}\n\n"
        f"已有章节：\n{json.dumps(chapter_payload, ensure_ascii=False)}\n\n"
        f"已有小节：\n{json.dumps(section_payload, ensure_ascii=False)}\n\n"
        "只返回 JSON："
        '{"groups":[{"source_indices":[1],'
        '"action":"assign_existing|create_section|create_chapter|leave_unassigned",'
        '"section_id":null,"chapter_id":null,'
        '"new_chapter_title":null,"new_section_title":null,'
        '"confidence":0.0,"reason":""}]}\n'
        "assign_existing 填 section_id；create_section 填 chapter_id 和 new_section_title；"
        "create_chapter 填 new_chapter_title 和 new_section_title；leave_unassigned 不创建结构。"
    )
    return prompt, chapter_refs, section_refs


def _normalize_groups(
    output: _GrowthOutput,
    *,
    utterances: list[MemoirVoiceUtterance],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> list[GrowthGroup]:
    used: set[int] = set()
    groups: list[GrowthGroup] = []
    for raw in output.groups:
        indices = [index for index in raw.source_indices if 1 <= index <= len(utterances) and index not in used]
        if not indices:
            continue
        action = raw.action if raw.action in ALLOWED_ACTIONS else "leave_unassigned"
        chapter_id = _decode_reference(chapter_refs, raw.chapter_id)
        section_id = _decode_reference(section_refs, raw.section_id)
        new_chapter_title = _clean_text(raw.new_chapter_title, limit=80) or None
        new_section_title = _clean_text(raw.new_section_title, limit=200) or None
        if action == "assign_existing" and section_id is None:
            action = "leave_unassigned"
        if action == "create_section" and (chapter_id is None or not new_section_title):
            action = "leave_unassigned"
        if action == "create_chapter" and (not new_chapter_title or not new_section_title):
            action = "leave_unassigned"
        used.update(indices)
        groups.append(
            GrowthGroup(
                utterance_ids=[utterances[index - 1].id for index in indices],
                action=action,
                section_id=section_id if action == "assign_existing" else None,
                chapter_id=chapter_id if action == "create_section" else None,
                new_chapter_title=(new_chapter_title if action == "create_chapter" else None),
                new_section_title=(new_section_title if action in {"create_section", "create_chapter"} else None),
                confidence=_confidence(raw.confidence),
                reason=_clean_text(raw.reason, limit=300) or "暂时无法确定素材归属",
            )
        )
    missing = [utterance.id for index, utterance in enumerate(utterances, start=1) if index not in used]
    if missing:
        groups.append(GrowthGroup(utterance_ids=missing, action="leave_unassigned"))
    return groups


async def decide_story_growth(
    *,
    utterances: list[MemoirVoiceUtterance],
    chapters: list[MemoirBookPlanChapter],
    sections: list[MemoirSection],
    ai_model: str | None,
) -> list[GrowthGroup]:
    prompt, chapter_refs, section_refs = build_story_growth_prompt(
        utterances=utterances,
        chapters=chapters,
        sections=sections,
    )
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    generated = await generate_structured_with_client(
        get_ai_service(ai_model),
        SYSTEM_PROMPT,
        prompt,
        output_model=_GrowthOutput,
        requested_model=ai_model,
        temperature=0.15,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    return _normalize_groups(
        generated.value,
        utterances=utterances,
        chapter_refs=chapter_refs,
        section_refs=section_refs,
    )


async def _load_session(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str,
) -> MemoirVoiceSession | None:
    return await db.scalar(
        select(MemoirVoiceSession)
        .where(
            MemoirVoiceSession.id == voice_session_id,
            MemoirVoiceSession.project_id == project_id,
        )
        .options(selectinload(MemoirVoiceSession.utterances).selectinload(MemoirVoiceUtterance.chat_message))
    )


async def schedule_story_tree_growth(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str,
    source: str,
) -> MemoirEditingJob | None:
    session = await _load_session(
        db,
        project_id=project_id,
        voice_session_id=voice_session_id,
    )
    if (
        session is None
        or session.session_goal != MemoirSessionGoal.OPEN_NARRATION.value
        or not any(is_ready_story_material(item) for item in session.utterances)
    ):
        return None
    existing = await editing_jobs.load_project_editing_job(
        db, project_id=project_id, job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        dedupe_key=f"organize_session:{voice_session_id}",
    )
    if existing is not None and existing.status == MemoirEditingJobStatus.FAILED.value and source != "retry":
        return existing
    delay = SETTLE_SECONDS if session.status == MemoirVoiceSessionStatus.STOPPED.value else get_settings().memoir_story_settle_seconds
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        payload_json={"voice_session_id": voice_session_id, "source": source},
        run_after=utcnow_naive() + timedelta(seconds=delay),
        dedupe_key=f"organize_session:{voice_session_id}",
        replace_completed=True,
        preserve_progress=True,
        reschedule_running=True,
    )


async def _active_corrections_exist(db: AsyncSession, *, project_id: str) -> bool:
    return await db.scalar(select(MemoirEditingJob.id).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.CORRECT_TRANSCRIPT.value,
        MemoirEditingJob.status.in_(editing_jobs.ACTIVE_EDITING_JOB_STATUSES),
    ).limit(1)) is not None


async def _load_tree(
    db: AsyncSession,
    *,
    project_id: str,
) -> tuple[MemoirBookPlan | None, list[MemoirBookPlanChapter], list[MemoirSection]]:
    plan = await load_plan_with_chapters(db, project_id)
    if plan is None:
        return None, [], []
    chapters = sorted(plan.chapters, key=lambda item: (item.sort_order, item.id))
    sections = list(
        (
            await db.execute(
                select(MemoirSection)
                .where(
                    MemoirSection.project_id == project_id,
                    MemoirSection.chapter_id.in_([chapter.id for chapter in chapters]),
                )
                .options(selectinload(MemoirSection.materials).selectinload(MemoirSectionMaterial.utterance))
                .order_by(MemoirSection.chapter_id, MemoirSection.sort_order)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return plan, chapters, sections


async def _create_initial_tree(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    job: MemoirEditingJob,
    progress: StoryGrowthProgress,
) -> None:
    async def checkpoint_plan() -> None:
        sections = (await db.scalars(select(MemoirSection).where(MemoirSection.project_id == project_id))).all()
        for section in sections:
            progress.initial_sections.setdefault(section.chapter_id, []).append(
                MemoirSectionVersionReference(id=section.id, version=section.version)
            )
        progress.placed_ids = list(
            (
                await db.scalars(
                    select(MemoirSectionMaterial.utterance_id).where(
                        MemoirSectionMaterial.project_id == project_id,
                    )
                )
            ).all()
        )
        # Persist the directory and its unfinished section work in the same transaction.
        job.result_json = {**(job.result_json or {}), "growth": progress.model_dump(mode="json")}
        db.add(job)

    await generate_book_plan(
        db,
        user_id=user_id,
        project_id=project_id,
        req=MemoirBookPlanGenerateRequest(),
        status=MemoirBookPlanStatus.CONFIRMED,
        before_commit=checkpoint_plan,
    )


async def _finish_initial_sections(
    db: AsyncSession, *, user_id: str, job: MemoirEditingJob, progress: StoryGrowthProgress
) -> None:
    for chapter_id, expected in progress.initial_sections.items():
        if chapter_id in progress.completed_initial_chapters:
            continue
        # The book pipeline can save prose while another initial chapter fails.
        # An old structure checkpoint must not replace sections under that prose.
        existing_draft = await db.scalar(
            select(MemoirBookChapterDraftVersion.id).where(
                MemoirBookChapterDraftVersion.project_id == job.project_id,
                MemoirBookChapterDraftVersion.chapter_id == chapter_id,
            ).limit(1)
        )
        if existing_draft is not None:
            progress.completed_initial_chapters.append(chapter_id)
            await _save_progress(db, job, progress)
            continue
        try:
            await regenerate_chapter_sections(
                db,
                user_id=user_id,
                project_id=job.project_id,
                chapter_id=chapter_id,
                req=MemoirSectionRegenerateRequest(expected_sections=expected),
                structure_updated_by=MemoirCreatedBy.AI,
            )
        except SectionRegenerationNeedsMaterial:
            # Legacy plans may contain an empty chapter after duplicate refs were
            # moved to their unique owner. Keep the chapter for future narration
            # and finish its initial work with an explicit needs-material result.
            progress.needs_material_chapters.append(chapter_id)
        except HTTPException as exc:
            if exc.status_code != 404 and not isinstance(exc, SectionRegenerationConflict):
                raise
            progress.structure_conflicts.append(chapter_id)
        progress.completed_initial_chapters.append(chapter_id)
        await _save_progress(db, job, progress)


async def _place_groups(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    batch: GrowthBatch,
    on_placed: Callable[[str], Awaitable[None]],
) -> list[str]:
    placed: list[str] = []
    plan = await load_plan_with_chapters(db, project_id)
    chapter_by_title = {chapter.title: chapter.id for chapter in (plan.chapters if plan else [])}
    for group_index, group in enumerate(batch.groups):
        if group.action == "leave_unassigned":
            continue
        target_section_id = group.section_id
        for utterance_id in group.utterance_ids:
            same_title_chapter_id = (
                chapter_by_title.get(group.new_chapter_title) if group.action == "create_chapter" else None
            )
            if target_section_id:
                request = MemoirSectionMaterialPlacementRequest(target_section_id=target_section_id)
            elif group.action == "create_section" or same_title_chapter_id:
                request = MemoirSectionMaterialPlacementRequest(
                    target_chapter_id=group.chapter_id or same_title_chapter_id,
                    new_section_title=group.new_section_title,
                )
            elif group.action == "create_chapter":
                request = MemoirSectionMaterialPlacementRequest(
                    new_chapter_title=group.new_chapter_title,
                    new_section_title=group.new_section_title,
                )
            else:
                raise RuntimeError("故事树生长结果缺少目标小节")
            try:
                placement = await place_material(
                    db,
                    user_id=user_id,
                    project_id=project_id,
                    utterance_id=utterance_id,
                    req=request,
                    assigned_by=MemoirSectionMaterialAssignedBy.AI,
                    structure_updated_by=MemoirCreatedBy.AI,
                    assignment_reason=group.reason or "story_tree_growth",
                    expected_source=batch.sources[utterance_id],
                    expected_section_version=batch.section_versions.get(target_section_id),
                    expected_chapter_updated_at=batch.chapter_updated_at.get(
                        batch.section_chapters.get(target_section_id) or group.chapter_id
                    ),
                    commit=False,
                )
            except HTTPException as exc:
                if exc.status_code not in {404, 409}:
                    raise
                continue
            target_section_id = placement.section_id
            batch.section_versions[target_section_id] = await db.scalar(
                select(MemoirSection.version).where(MemoirSection.id == target_section_id)
            )
            batch.section_chapters[target_section_id] = placement.chapter_id
            if group.action == "create_chapter":
                chapter_by_title.setdefault(group.new_chapter_title, placement.chapter_id)
            if placement.chapter_id not in batch.chapter_updated_at:
                batch.chapter_updated_at[placement.chapter_id] = await db.scalar(
                    select(MemoirBookPlanChapter.updated_at).where(MemoirBookPlanChapter.id == placement.chapter_id)
                )
            batch.groups[group_index] = replace(group, section_id=target_section_id)
            await on_placed(utterance_id)
            placed.append(utterance_id)
    return placed


async def grow_story_tree(
    db: AsyncSession,
    *,
    job: MemoirEditingJob,
    session: MemoirVoiceSession,
) -> EditingJobResult:
    project = await db.scalar(select(Project).where(Project.id == job.project_id))
    if project is None:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="回忆录不存在")
    before_plan, before_chapters, before_sections = await _load_tree(
        db,
        project_id=job.project_id,
    )
    progress = _progress(job)
    initial_tree = before_plan is None or bool(progress.initial_sections)
    if before_plan is None:
        await _create_initial_tree(db, user_id=project.user_id, project_id=job.project_id, job=job, progress=progress)
    await _finish_initial_sections(db, user_id=project.user_id, job=job, progress=progress)

    existing_material_ids = set(
        (
            await db.scalars(
                select(MemoirSectionMaterial.utterance_id).where(
                    MemoirSectionMaterial.project_id == job.project_id,
                )
            )
        ).all()
    )
    project_sources = (
        await db.scalars(
            select(MemoirVoiceUtterance)
            .join(MemoirVoiceSession)
            .where(
                MemoirVoiceSession.project_id == job.project_id,
                MemoirVoiceSession.session_goal == MemoirSessionGoal.OPEN_NARRATION.value,
            )
            .options(selectinload(MemoirVoiceUtterance.chat_message))
            .order_by(MemoirVoiceUtterance.created_at, MemoirVoiceUtterance.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    deferred = {}
    history = (
        await db.scalars(
            select(MemoirEditingJob)
            .where(
                MemoirEditingJob.project_id == job.project_id,
                MemoirEditingJob.job_type == MemoirEditingJobType.ORGANIZE_MATERIAL.value,
            )
            .order_by(MemoirEditingJob.updated_at)
        )
    ).all()
    for previous in history:
        deferred.update(_progress(previous).deferred)
    utterances = [
        item
        for item in project_sources
        if item.id not in existing_material_ids
        and not item.completion_issue_id
        and is_ready_story_material(item)
        and has_story_content(current_utterance_text(item))
        and not (
            item.id in deferred
            and deferred[item.id].attempts >= MAX_UNASSIGNED_ATTEMPTS
            and deferred[item.id].text_hash == source_text_hash(current_utterance_text(item))
        )
    ]
    if progress.batch is None and utterances:
        _plan, chapters, sections = await _load_tree(db, project_id=job.project_id)
        batch = GrowthBatch(
            sources={
                item.id: (source_text_hash(current_utterance_text(item)), current_transcript_version_id(item))
                for item in utterances
            },
            section_versions={item.id: item.version for item in sections},
            section_chapters={item.id: item.chapter_id for item in sections},
            chapter_updated_at={item.id: item.updated_at for item in chapters},
        )
        ai_model = await load_project_ai_model(db, job.project_id, None)
        await db.commit()
        batch.groups = await decide_story_growth(
            utterances=utterances,
            chapters=chapters,
            sections=sections,
            ai_model=ai_model,
        )
        progress.batch = batch
        await _save_progress(db, job, progress)
    if progress.batch is not None:
        batch = progress.batch

        async def on_placed(utterance_id: str) -> None:
            progress.placed_ids.append(utterance_id)
            await _save_progress(db, job, progress)

        await _place_groups(db, user_id=project.user_id, project_id=job.project_id, batch=batch, on_placed=on_placed)
        assigned_ids = set(
            (
                await db.scalars(
                    select(MemoirSectionMaterial.utterance_id).where(
                        MemoirSectionMaterial.project_id == job.project_id,
                    )
                )
            ).all()
        )
        for group in batch.groups:
            for source_id in group.utterance_ids:
                if source_id in assigned_ids:
                    progress.deferred.pop(source_id, None)
                    continue
                text_hash = batch.sources[source_id][0]
                previous = deferred.get(source_id)
                attempts = previous.attempts if previous and previous.text_hash == text_hash else 0
                if group.action == "leave_unassigned" and group.reason:
                    attempts += 1
                needs_user = attempts >= MAX_UNASSIGNED_ATTEMPTS
                progress.deferred[source_id] = DeferredSource(
                    text_hash=text_hash,
                    status="needs_user" if needs_user else "pending",
                    reason=group.reason if needs_user else "素材尚未归类，等待下一次整理",
                    attempts=attempts,
                )
        progress.batch = None
    placed_ids = list(dict.fromkeys(progress.placed_ids))
    progress.placed_ids = []

    _after_plan, after_chapters, after_sections = await _load_tree(
        db,
        project_id=job.project_id,
    )
    new_chapter_ids = [item.id for item in after_chapters if item.id not in {value.id for value in before_chapters}]
    new_section_ids = [item.id for item in after_sections if item.id not in {value.id for value in before_sections}]
    if placed_ids or new_chapter_ids or new_section_ids:
        summary_parts = []
        if new_chapter_ids:
            summary_parts.append(f"{len(new_chapter_ids)} 个新章节")
        if new_section_ids:
            summary_parts.append(f"{len(new_section_ids)} 个新小节")
        summary = "故事树新长出了" + "、".join(summary_parts) if summary_parts else "故事树已经整理好本次讲述"
        await automation_events.record_or_update_automation_event(
            db,
            project_id=job.project_id,
            event_type=MemoirAutomationEventType.STORY_STRUCTURE_UPDATED,
            summary_text=summary,
            event_signature=f"story_tree_growth:{session.id}:" + semantic_json_signature({
                "chapter_ids": sorted(new_chapter_ids),
                "section_ids": sorted(new_section_ids),
                "utterance_ids": sorted(placed_ids),
            }),
            utterance_ids=placed_ids,
            editing_job_id=job.id,
            metadata_json={
                "voice_session_id": session.id,
                "initial_tree": initial_tree,
                "new_chapter_ids": new_chapter_ids,
                "new_section_ids": new_section_ids,
                "organized_utterance_count": len(placed_ids),
            },
        )
    await _save_progress(db, job, progress)
    await split_large_chapters(db, user_id=project.user_id, project_id=job.project_id)
    return EditingJobResult(
        result_json={
            "voice_session_id": session.id,
            "growth": progress.model_dump(mode="json"),
            "initial_tree": initial_tree,
            "new_chapter_ids": new_chapter_ids,
            "new_section_ids": new_section_ids,
            "organized_utterance_ids": placed_ids,
        }
    )


async def run_story_tree_growth_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    voice_session_id = (job.payload_json or {}).get("voice_session_id")
    if not isinstance(voice_session_id, str):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="故事树生长任务缺少讲述会话",
        )
    session = await _load_session(
        db,
        project_id=job.project_id,
        voice_session_id=voice_session_id,
    )
    if (
        session is None
        or session.session_goal != MemoirSessionGoal.OPEN_NARRATION.value
    ):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="讲述会话尚未完成或不参与故事树生长",
        )
    if await _active_corrections_exist(
        db,
        project_id=job.project_id,
    ):
        job.run_after = utcnow_naive() + timedelta(seconds=SETTLE_SECONDS)
        db.add(job)
        await db.flush()
        return EditingJobResult(
            status=MemoirEditingJobStatus.PENDING,
            result_json={**(job.result_json or {}), "voice_session_id": session.id, "stage": "waiting_for_transcripts"},
        )
    result = await grow_story_tree(db, job=job, session=session)
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=job.project_id)
    return result
