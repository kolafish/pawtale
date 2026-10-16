from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
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
    MemoirSectionStatus,
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
from app.services.memoir import automation_events, completion_interviews, editing_jobs, narration_people
from app.services.memoir.book_plan_ai import NO_STORY_YET
from app.services.memoir.book_plan_sources import SourceSegment, load_plan_with_chapters, renumber_chapters, set_temporary_chapter_orders
from app.services.memoir.book_plan_write_workflow import generate_book_plan
from app.services.memoir.chapter_structure import load_structure_summaries, review_story_structure
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.material_organization import material_organization_candidates
from app.services.memoir.material_roles import STORY_CONTENT_GUIDANCE, interviewer_lines, is_ready_story_material
from app.services.memoir.project_ai_preferences import load_project_ai_model
from app.services.memoir.section_regeneration import (
    SectionRegenerationConflict,
    SectionRegenerationNeedsMaterial,
    regenerate_chapter_sections,
)
from app.services.memoir.sections import finalize_section_changes, lock_chapter_structure, lock_plan_structure, place_material
from app.services.memoir.semantic_state import semantic_json_signature
from app.services.memoir.transcript_projection import (
    current_transcript_version_id,
    current_utterance_text,
    has_story_content,
    source_text_hash,
)


SYSTEM_PROMPT = (
    "你是一位克制而有整体感的中文回忆录结构编辑。"
    "请把本次新讲述作为一个整体理解。章承载人生阶段或持续主题，小节承载具体经历。只有现有章节无法容纳的新人生阶段或持续主题才新建章；新事件、新地点或新细节本身不是新建章的理由。"
    "已有标题不限定内容归属；归类后由整体结构检查统一调整标题和拆合。"
)
OFFERED_CHAPTER_GUIDANCE = "offered_chapter_id 表示访谈员问的是这一章里的事：回答在补充这一章的内容就放进这一章（已有小节或新小节），讲了别的事就照常安排。"
SETTLE_SECONDS = 3
MAX_UNASSIGNED_ATTEMPTS = 2

class _NewChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_ref: str = Field(pattern=r"^N[1-9][0-9]*$")
    title: str
    insert_before_chapter_id: str | None = None
    time_evidence: str | None = None


class _SourcePlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["story", "not_story", "unassigned"] = "story"
    section_id: str | None = None
    chapter_ref: str | None = None
    new_section_title: str | None = None
    reason: str = ""


class _GrowthOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_chapters: list[_NewChapter] = Field(default_factory=list)
    placements: dict[str, _SourcePlacement] = Field(default_factory=dict)


@dataclass(frozen=True)
class GrowthGroup:
    utterance_ids: list[str]
    action: str
    section_id: str | None = None
    chapter_id: str | None = None
    new_chapter_title: str | None = None
    insert_before_chapter_id: str | None = None
    new_section_title: str | None = None
    confidence: float | None = None
    reason: str = ""


class DeferredSource(BaseModel):
    text_hash: str
    # not_story: answered the interviewer without any story; kept out of the book until its text changes.
    status: Literal["pending", "needs_user", "not_story"]
    reason: str
    attempts: int = 0
    context_signature: str = ""


class RecoveryMaterial(BaseModel):
    material_id: str
    section_id: str
    updated_at: datetime
    draft_version_id: str


class GrowthBatch(BaseModel):
    original_chapter_ids: list[str] = Field(default_factory=list)
    created_chapters: dict[str, str | None] = Field(default_factory=dict)
    context_signature: str = ""
    groups: list[GrowthGroup] = Field(default_factory=list)
    sources: dict[str, tuple[str, str | None]]
    section_versions: dict[str, int]
    section_chapters: dict[str, str]
    chapter_updated_at: dict[str, datetime]
    recovery_materials: dict[str, RecoveryMaterial] = Field(default_factory=dict)


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
    interviewer_lines: dict[str, str],
    offered_chapters: dict[str, str] | None = None,
    chapter_summaries: dict[str, str] | None = None,
    section_summaries: dict[str, str] | None = None,
) -> tuple[str, ModelReferenceMap, ModelReferenceMap]:
    chapter_refs = ModelReferenceMap("C", (chapter.id for chapter in chapters))
    section_refs = ModelReferenceMap("S", (section.id for section in sections))
    chapter_titles = {chapter.id: chapter.title for chapter in chapters}
    chapter_candidates, section_candidates = material_organization_candidates(
        chapters=chapters,
        sections=sections,
    )
    candidate_chapter_ids = {chapter.id for chapter in chapter_candidates}
    offered = {
        message_id: chapter_refs.encode(chapter_id)
        for message_id, chapter_id in (offered_chapters or {}).items()
        if chapter_id in candidate_chapter_ids
    }
    source_payload = [
        {
            "source_index": index,
            **({"interviewer_line": line} if (line := interviewer_lines.get(utterance.chat_message_id or "")) else {}),
            **({"offered_chapter_id": ref} if (ref := offered.get(utterance.chat_message_id or "")) else {}),
            "text": current_utterance_text(utterance),
        }
        for index, utterance in enumerate(utterances, start=1)
    ]
    chapter_payload = [
        {
            "chapter_id": chapter_refs.encode(chapter.id),
            "chapter_title": chapter.title,
            **({"summary": chapter_summaries[chapter.id]} if chapter_summaries and chapter.id in chapter_summaries else {}),
        }
        for chapter in chapter_candidates
    ]
    section_payload = [
        {
            "section_id": section_refs.encode(section.id),
            "chapter_id": chapter_refs.encode(section.chapter_id),
            "chapter_title": chapter_titles.get(section.chapter_id, ""),
            "section_title": section.title,
            **({"summary": section_summaries[section.id]} if section_summaries and section.id in section_summaries else {}),
            "existing_material_examples": section.existing_material_examples,
        }
        for section in section_candidates
    ]
    prompt = (
        '请将本次全部讲述安排到故事目录中。先判断哪些是故事，再安排故事的章节和小节。\n'
        f'1. {STORY_CONTENT_GUIDANCE} 非故事标为 not_story。\n'
        '2. source_index 是不可拆分的一整条原始讲述，不是事件编号；不能复制、分割或自编 source_index。优先保证每条讲述完整且只收录一次。在能按整条讲述分组的前提下，同一事件的断续讲述合组，不同事件分组。如果同一条讲述包含多个事件，必须整体放在一个小节，使用能覆盖这些事件的共同主题标题，不能拆成多个小节重复引用该条，也不能只保留第一个事件。章节对应人生阶段或持续主题；不同原始讲述能分开的事件仍分小节，不因同属一个阶段就全部合并。\n'
        '以摘要和素材判断整章覆盖的人生阶段或持续主题；已有小节只是当前收录的例子，不限制该章容纳新的事件。例如家人主题可以容纳童年家人故事，不因现有小节讲成年生活就另建一章。\n'
        '3. 已有小节适合时填 section_id；已有章节适合而需新小节时填 chapter_ref 和 new_section_title。没有合适章节时先在 new_chapters 中定义 N1、N2 等新章节，再让相应讲述用 chapter_ref 引用它，不需要重复定义章节。\n'
        '4. 只要有明确的亲历事件或感受，就安排已有位置或建立适当的新位置；篇幅短、没有年份或与现有章节不同，均不是暂不归类的理由。不为迁就现有目录强行添加时间或人生阶段。不编造人物关系、时间或事件。\n'
        '5. 仅当原话连发生了什么都无法确定、安排位置必须猜测事实时才标为 unassigned，说明缺少什么。时间不明但事件明确的故事仍应收录，可以追加在末尾。\n'
        '6. 新章可用 insert_before_chapter_id 指定插在某个现有章节前。只有明确的人生先后且现有目录按人生阶段组织时才指定；主题目录或时间不明填 null。不重排已有章节。不同新章节按拟收录先后顺序列出。time_evidence 必须是本次讲述中能支持插入位置的原文片段；没有明确时间证据时 time_evidence 和 insert_before_chapter_id 都填 null。\n'
        '标题概括经历，不把单个年龄或细节当成章节主题。原话对年龄、年份有犹疑时，标题用经历主题命名，避免选一个数字写成定论。标题也不得推断原话没有的年龄和人生阶段；“有一次”“那天”不能证明是早年、童年或退休以后。时间未知时用事件主题命名。\n'
        f'7. placements 必须以本次给出的 source_index 字符串为键，每个键对应一个对象且只有一个归属。不能将一个 source_index 解释成多个事件编号，也不能添加本次未给出的键。new_chapters 只声明本次真正要创建的章节。interviewer_line 只供理解回答，不是讲述者的事实。{OFFERED_CHAPTER_GUIDANCE if offered else ""}\n'
        '\n'
        f"本次新讲述：\n{json.dumps(source_payload, ensure_ascii=False)}\n\n"
        f"已有章节：\n{json.dumps(chapter_payload, ensure_ascii=False)}\n\n"
        f"已有小节：\n{json.dumps(section_payload, ensure_ascii=False)}\n\n"
        f"本次 placements 的全部键（不能增减）：{json.dumps([str(i) for i in range(1, len(utterances) + 1)])}。"
        '同一小节的多条讲述填相同的 section_id，或相同的 chapter_ref 和 new_section_title；不同事件能分开时填不同的小节。每个归属都收录该条讲述全文。\n'
        '只返回 JSON：{"new_chapters":[{"chapter_ref":"N1","title":"新章节标题","insert_before_chapter_id":null,"time_evidence":null}],"placements":{"1":{"kind":"story","section_id":null,"chapter_ref":"N1","new_section_title":"覆盖整条讲述的主题","reason":"归类依据"}}}。'
        '示例仅示意单条讲述；实际 placements 必须包含上面全部键。kind 只能是 story、not_story、unassigned。后两者 section_id、chapter_ref、new_section_title 均填 null。'
        'story 的已有 section_id 和新建小节的 chapter_ref/new_section_title 二者只能选一个。chapter_ref 只能是给出的 C 引用或本次声明的 N 引用；new_chapters 没有新增章时填空数组。'
    )
    return prompt, chapter_refs, section_refs


def _normalize_groups(
    output: _GrowthOutput,
    *,
    utterances: list[MemoirVoiceUtterance],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> list[GrowthGroup]:
    invalid = HTTPException(status_code=422, detail="AI 返回了无效的归类位置，原始讲述已保留，请继续整理")
    if set(output.placements) != {str(index) for index in range(1, len(utterances) + 1)}:
        raise HTTPException(status_code=422, detail="AI 归类结果遗漏或添加了讲述，原始讲述已保留，请继续整理")
    declared = {chapter.chapter_ref: chapter for chapter in output.new_chapters}
    if len(declared) != len(output.new_chapters) or len({_clean_text(item.title, limit=80) for item in output.new_chapters}) != len(declared):
        raise invalid
    used_new_refs = {item.chapter_ref for item in output.placements.values() if item.kind == "story" and item.chapter_ref in declared}
    if used_new_refs != set(declared):
        raise invalid
    anchors = {}
    for ref, chapter in declared.items():
        if not chapter.title.strip():
            raise invalid
        anchor = _decode_reference(chapter_refs, chapter.insert_before_chapter_id)
        if chapter.insert_before_chapter_id:
            source_indices = sorted(int(index) for index, item in output.placements.items() if item.kind == "story" and item.chapter_ref == ref)
            source_text = "".join(current_utterance_text(utterances[index - 1]) for index in source_indices)
            if not anchor or not chapter.time_evidence or chapter.time_evidence not in source_text:
                # Positioning is optional: uncertain evidence appends the chapter without
                # preventing complete, uniquely owned source material from being saved.
                anchor = None
        anchors[ref] = anchor
    groups: dict[tuple, GrowthGroup] = {}
    new_order = {ref: index for index, ref in enumerate(declared)}
    placements = sorted(output.placements.items(), key=lambda pair: (new_order.get(pair[1].chapter_ref, -1), int(pair[0])))
    for index, raw in placements:
        section_id = _decode_reference(section_refs, raw.section_id)
        chapter_id = _decode_reference(chapter_refs, raw.chapter_ref)
        new_chapter = declared.get(raw.chapter_ref)
        title = _clean_text(raw.new_section_title, limit=200) or None
        if raw.kind != "story":
            if raw.section_id or raw.chapter_ref or title or (raw.kind == "unassigned" and not raw.reason.strip()):
                raise invalid
            action = "not_story" if raw.kind == "not_story" else "leave_unassigned"
        elif raw.section_id:
            if not section_id or raw.chapter_ref or title:
                raise invalid
            action = "assign_existing"
        else:
            if not title or not (chapter_id or new_chapter):
                raise invalid
            action = "create_chapter" if new_chapter else "create_section"
        key = (action, raw.section_id, raw.chapter_ref, title, raw.reason if action == "leave_unassigned" else None)
        if key not in groups:
            groups[key] = GrowthGroup(
                utterance_ids=[], action=action, section_id=section_id, chapter_id=chapter_id,
                new_chapter_title=_clean_text(new_chapter.title, limit=80) if new_chapter else None,
                insert_before_chapter_id=anchors.get(raw.chapter_ref), new_section_title=title,
                reason=_clean_text(raw.reason, limit=300),
            )
        groups[key].utterance_ids.append(utterances[int(index) - 1].id)
    return list(groups.values())


async def decide_story_growth(
    *,
    utterances: list[MemoirVoiceUtterance],
    chapters: list[MemoirBookPlanChapter],
    sections: list[MemoirSection],
    interviewer_lines: dict[str, str],
    ai_model: str | None,
    offered_chapters: dict[str, str] | None = None,
    chapter_summaries: dict[str, str] | None = None,
    section_summaries: dict[str, str] | None = None,
) -> list[GrowthGroup]:
    prompt, chapter_refs, section_refs = build_story_growth_prompt(
        utterances=utterances,
        chapters=chapters,
        sections=sections,
        interviewer_lines=interviewer_lines,
        offered_chapters=offered_chapters,
        chapter_summaries=chapter_summaries,
        section_summaries=section_summaries,
    )

    class ValidatedGrowthOutput(_GrowthOutput):
        @model_validator(mode="after")
        def validate_placements(self):
            # Validate against this exact input inside the existing structured
            # generation budget, before any chapter or material is written.
            try:
                _normalize_groups(self, utterances=utterances, chapter_refs=chapter_refs, section_refs=section_refs)
            except HTTPException as exc:
                raise ValueError(str(exc.detail)) from exc
            return self

    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    generated = await generate_structured_with_client(
        get_ai_service(ai_model),
        SYSTEM_PROMPT,
        prompt,
        output_model=ValidatedGrowthOutput,
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
    existing = await db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.ORGANIZE_MATERIAL.value,
        MemoirEditingJob.dedupe_key == f"organize_session:{voice_session_id}",
    ).with_for_update().execution_options(populate_existing=True))
    if existing is not None and existing.status == MemoirEditingJobStatus.FAILED.value and source != "retry":
        return existing
    if (existing is not None and existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
            and (existing.payload_json or {}).get("source") == "retry"):
        source = "retry"
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


async def _deferred_sources(db: AsyncSession, project_id: str) -> dict[str, DeferredSource]:
    history = (await db.scalars(select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.ORGANIZE_MATERIAL.value,
    ).order_by(MemoirEditingJob.updated_at, MemoirEditingJob.id))).all()
    result: dict[str, DeferredSource] = {}
    for previous in history:
        result.update(_progress(previous).deferred)
    return result


async def _person_answer_target(db: AsyncSession, project_id: str, source_id: str | None) -> tuple[str, int] | None:
    if source_id is None:
        return None
    row = (await db.execute(
        select(MemoirSection.id, MemoirSection.version)
        .join(MemoirSectionMaterial, MemoirSectionMaterial.section_id == MemoirSection.id)
        .join(MemoirBookPlanChapter, MemoirBookPlanChapter.id == MemoirSection.chapter_id)
        .where(MemoirSectionMaterial.project_id == project_id,
               MemoirSectionMaterial.utterance_id == source_id,
               MemoirSectionMaterial.include_in_book.is_(True),
               MemoirSection.status == MemoirSectionStatus.ACTIVE.value,
               MemoirSection.include_in_book.is_(True), MemoirBookPlanChapter.include_in_book.is_(True))
    )).one_or_none()
    return (row.id, row.version) if row else None


async def resume_pending_person_answers(db: AsyncSession, *, project_id: str) -> bool:
    """Source edits already queue book work; resume eligible waiting answers before that writing."""
    deferred = await _deferred_sources(db, project_id)
    pending_ids = [key for key, value in deferred.items() if value.status == "pending"]
    if not pending_ids:
        return False
    answers = (await db.scalars(select(MemoirVoiceUtterance).join(MemoirVoiceSession)
        .outerjoin(MemoirSectionMaterial, MemoirSectionMaterial.utterance_id == MemoirVoiceUtterance.id)
        .where(MemoirVoiceSession.project_id == project_id, MemoirVoiceUtterance.id.in_(pending_ids),
               MemoirSectionMaterial.id.is_(None), MemoirVoiceUtterance.completion_issue_id.is_(None))
        .options(selectinload(MemoirVoiceUtterance.chat_message)))).all()
    sources = await narration_people.answer_sources(
        db, project_id=project_id, message_ids=(item.chat_message_id for item in answers),
    )
    sessions = set()
    for answer in answers:
        if (answer.chat_message_id not in sources or not is_ready_story_material(answer)
            or deferred[answer.id].text_hash != source_text_hash(current_utterance_text(answer))
            or narration_people.answer_story_content(answer.chat_message) is False):
            continue
        if narration_people.answer_story_content(answer.chat_message) is None:
            reading_status = await db.scalar(select(MemoirEditingJob.status).where(
                MemoirEditingJob.dedupe_key == f"narrator_profile:{answer.id}",
            ))
            if reading_status in {*editing_jobs.ACTIVE_EDITING_JOB_STATUSES, MemoirEditingJobStatus.FAILED.value}:
                continue
        if await _person_answer_target(db, project_id, sources[answer.chat_message_id]):
            sessions.add(answer.voice_session_id)
    scheduled = False
    for session_id in sorted(sessions):
        child = await schedule_story_tree_growth(db, project_id=project_id,
            voice_session_id=session_id, source="person_answer_source_ready")
        scheduled |= child is not None and child.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
    return scheduled


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
                .options(
                    selectinload(MemoirSection.materials).selectinload(MemoirSectionMaterial.utterance).selectinload(MemoirVoiceUtterance.chat_message),
                    selectinload(MemoirSection.materials).selectinload(MemoirSectionMaterial.transcript_version),
                )
                .execution_options(populate_existing=True)
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
    excluded_source_hashes: dict[str, str],
) -> bool:
    async def checkpoint_not_story(sources: tuple[SourceSegment, ...]) -> None:
        for source in sources:
            progress.deferred[source.utterance_id] = DeferredSource(
                text_hash=source_text_hash(source.text), status="not_story", reason="资料或对话回应，不含故事",
                attempts=MAX_UNASSIGNED_ATTEMPTS,
            )
        await _save_progress(db, job, progress)

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

    result = await generate_book_plan(
        db,
        user_id=user_id,
        project_id=project_id,
        req=MemoirBookPlanGenerateRequest(),
        status=MemoirBookPlanStatus.CONFIRMED,
        before_commit=checkpoint_plan,
        on_not_story=checkpoint_not_story,
        excluded_source_hashes=excluded_source_hashes,
        only_if_missing=True,
    )
    return result.outcome == "created"


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
        if group.action in {"leave_unassigned", "not_story"}:
            continue
        target_section_id = group.section_id
        for utterance_id in group.utterance_ids:
            # A retry only rechecks the content boundary; it never moves existing stories.
            if utterance_id in batch.recovery_materials:
                continue
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
                        batch.section_chapters.get(target_section_id) or group.chapter_id or same_title_chapter_id
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
            if placement.created_chapter:
                batch.created_chapters[placement.chapter_id] = group.insert_before_chapter_id
            if placement.chapter_id not in batch.chapter_updated_at:
                batch.chapter_updated_at[placement.chapter_id] = await db.scalar(
                    select(MemoirBookPlanChapter.updated_at).where(MemoirBookPlanChapter.id == placement.chapter_id)
                )
            batch.groups[group_index] = replace(group, section_id=target_section_id)
            await on_placed(utterance_id)
            placed.append(utterance_id)
    return placed


async def _position_new_chapters(db: AsyncSession, *, project_id: str, batch: GrowthBatch) -> None:
    if not batch.created_chapters or not batch.original_chapter_ids:
        return
    plan = await lock_plan_structure(db, project_id=project_id)
    chapters = list((await db.scalars(select(MemoirBookPlanChapter).where(
        MemoirBookPlanChapter.plan_id == plan.id,
    ).order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
      .with_for_update().execution_options(populate_existing=True))).all())
    # Place only chapters created by this batch. A changed directory belongs to
    # its newer writer; keep our stories at the end instead of undoing that edit.
    if [item.id for item in chapters] != [*batch.original_chapter_ids, *batch.created_chapters]:
        return
    if any(item.updated_at != batch.chapter_updated_at.get(item.id) for item in chapters):
        return
    ordered = [item for item in chapters if item.id not in batch.created_chapters]
    for chapter in chapters:
        if chapter.id not in batch.created_chapters:
            continue
        anchor = batch.created_chapters[chapter.id]
        position = next((index for index, item in enumerate(ordered) if item.id == anchor), len(ordered))
        ordered.insert(position, chapter)
    if [item.id for item in ordered] == [item.id for item in chapters]:
        return
    set_temporary_chapter_orders(chapters)
    await db.flush()
    renumber_chapters(ordered)
    await db.flush()


async def _recovery_materials(
    db: AsyncSession, *, job: MemoirEditingJob, sections: list[MemoirSection],
) -> dict[str, RecoveryMaterial]:
    """Recheck only automatic additions that failed before reaching the saved prose."""
    if (job.payload_json or {}).get("source") != "retry":
        return {}
    from app.services.memoir import automatic_book_draft, chapter_generation

    parent = await db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == job.project_id,
        MemoirEditingJob.dedupe_key == f"update-book-draft:{job.project_id}",
    ))
    if parent is None:
        return {}
    progress = automatic_book_draft.DraftProgress.model_validate(parent.result_json or {})
    result: dict[str, RecoveryMaterial] = {}
    for chapter_id, state in progress.chapters.items():
        if state.status != "failed":
            continue
        chapter = await db.get(MemoirBookPlanChapter, chapter_id)
        if chapter is None or not chapter.include_in_book:
            continue
        latest = await chapter_generation.latest_chapter_draft_version(
            db, project_id=job.project_id, chapter_id=chapter_id,
        )
        if latest is None or latest.created_by == MemoirCreatedBy.USER.value:
            continue
        used_ids = set(latest.used_material_ids_json or [])
        used_ids.update(item["material_id"] for item in (latest.material_snapshot_json or [])
                        if isinstance(item, dict) and item.get("material_id"))
        protected_sections = set()
        for item in latest.section_snapshot_json or []:
            if isinstance(item, dict):
                used_ids.update(item.get("used_material_ids") or [])
                if item.get("user_edited"):
                    protected_sections.add(item.get("section_id"))
        for section in sections:
            if (section.chapter_id != chapter_id or not section.include_in_book
                    or section.status != MemoirSectionStatus.ACTIVE.value or section.id in protected_sections):
                continue
            for material in section.materials:
                if (material.assigned_by == MemoirSectionMaterialAssignedBy.AI.value
                        and material.include_in_book and material.utterance_id not in used_ids
                        and material.created_at > latest.created_at):
                    result[material.utterance_id] = RecoveryMaterial(
                        material_id=material.id, section_id=section.id, updated_at=material.updated_at,
                        draft_version_id=latest.id,
                    )
    return result


async def _remove_recovered_nonstory(
    db: AsyncSession, *, project_id: str, batch: GrowthBatch, source_id: str,
) -> bool:
    from app.services.memoir.chapter_generation import latest_chapter_draft_version

    recovery = batch.recovery_materials[source_id]
    chapter_id = batch.section_chapters[recovery.section_id]
    try:
        await lock_plan_structure(db, project_id=project_id)
        chapter = await lock_chapter_structure(db, project_id=project_id, chapter_id=chapter_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return False
    utterance = await db.scalar(select(MemoirVoiceUtterance).where(
        MemoirVoiceUtterance.id == source_id,
    ).options(selectinload(MemoirVoiceUtterance.chat_message))
      .with_for_update().execution_options(populate_existing=True))
    material = await db.scalar(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.project_id == project_id,
        MemoirSectionMaterial.utterance_id == source_id,
    ).with_for_update().execution_options(populate_existing=True))
    section = await db.get(MemoirSection, recovery.section_id, populate_existing=True)
    latest = await latest_chapter_draft_version(db, project_id=project_id, chapter_id=chapter_id)
    if (material is None or utterance is None or section is None or latest is None
            or material.id != recovery.material_id or material.section_id != recovery.section_id
            or material.updated_at != recovery.updated_at
            or material.transcript_version_id != batch.sources[source_id][1]
            or material.assigned_by != MemoirSectionMaterialAssignedBy.AI.value or not material.include_in_book
            or section.chapter_id != chapter_id or not section.include_in_book or not chapter.include_in_book
            or section.status != MemoirSectionStatus.ACTIVE.value
            or section.version != batch.section_versions[section.id]
            or chapter.updated_at != batch.chapter_updated_at[chapter_id]
            or latest.id != recovery.draft_version_id
            or (source_text_hash(current_utterance_text(utterance)), current_transcript_version_id(utterance))
            != batch.sources[source_id]):
        return False
    # Drop only the mistaken automatic association. The recording/transcript stays,
    # and the hash-bound not_story checkpoint lets a later text edit be reconsidered.
    await db.delete(material)
    section.version += 1
    section.updated_at = utcnow_naive()
    batch.section_versions[section.id] = section.version
    await finalize_section_changes(db, project_id=project_id, section_ids={section.id})
    await db.flush()
    db.expire(section, ["materials"])
    return True


async def grow_story_tree(
    db: AsyncSession,
    *,
    job: MemoirEditingJob,
    session: MemoirVoiceSession,
) -> tuple[EditingJobResult, bool]:
    """Return progress and whether structure review already scheduled the book."""
    project = await db.scalar(select(Project).where(Project.id == job.project_id))
    if project is None:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="回忆录不存在"), False
    before_plan, before_chapters, before_sections = await _load_tree(
        db,
        project_id=job.project_id,
    )
    before_chapter_ids = {chapter.id for chapter in before_chapters}
    before_section_ids = {section.id for section in before_sections}
    if before_plan is not None:
        from app.services.memoir.book_plan_write_workflow import activate_existing_plan_for_automatic_book
        await activate_existing_plan_for_automatic_book(db, project_id=job.project_id)
    progress = _progress(job)
    deferred = await _deferred_sources(db, job.project_id)
    initial_tree = before_plan is None or bool(progress.initial_sections)
    if before_plan is None:
        try:
            created = await _create_initial_tree(
                db, user_id=project.user_id, project_id=job.project_id, job=job, progress=progress,
                excluded_source_hashes={key: value.text_hash for key, value in deferred.items() if value.status == "not_story"},
            )
            if not created:
                # A parallel session initialized this project while our model
                # was running. Continue as incremental growth on that directory.
                before_plan, before_chapters, before_sections = await _load_tree(db, project_id=job.project_id)
                before_chapter_ids = {chapter.id for chapter in before_chapters}
                before_section_ids = {section.id for section in before_sections}
                initial_tree = False
        except HTTPException as exc:
            if exc.detail != NO_STORY_YET:
                raise
            # Not a failure: the next narration in this session schedules growth again.
            return EditingJobResult(
                status=MemoirEditingJobStatus.SKIPPED,
                result_json={"voice_session_id": session.id, "growth": progress.model_dump(mode="json")},
                error_message=NO_STORY_YET,
            ), False
    await _finish_initial_sections(db, user_id=project.user_id, job=job, progress=progress)
    recovery_materials = await _recovery_materials(db, job=job, sections=before_sections)

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
    deferred.update(progress.deferred)
    person_answers = await narration_people.answer_sources(
        db, project_id=job.project_id, message_ids=(item.chat_message_id for item in project_sources),
    )
    # Retry budgets belong to one set of evidence, not to the source forever.
    context_signature = semantic_json_signature({
        "sources": [(item.id, source_text_hash(current_utterance_text(item))) for item in project_sources],
        "chapters": [(item.id, item.title) for item in before_chapters],
        "sections": [(item.id, item.chapter_id, item.title, item.version) for item in before_sections],
    })
    linked_answers = []
    for answer in project_sources:
        if (answer.id in existing_material_ids or not is_ready_story_material(answer)
            or answer.chat_message_id not in person_answers or answer.completion_issue_id):
            continue
        text_hash = source_text_hash(current_utterance_text(answer))
        previous = deferred.get(answer.id)
        if previous and previous.text_hash == text_hash and previous.status == "not_story":
            progress.deferred[answer.id] = previous
            continue
        story_content = narration_people.answer_story_content(answer.chat_message)
        if story_content is None:
            from app.services.memoir.narrator_profile import schedule_narrator_profile
            await schedule_narrator_profile(db, utterance=answer, require_answer_reading=True)
            progress.deferred[answer.id] = DeferredSource(text_hash=text_hash, status="pending",
                reason="等待人物补答的内容核对", context_signature=context_signature)
        elif story_content:
            linked_answers.append(answer)
        else:
            progress.deferred[answer.id] = DeferredSource(text_hash=text_hash, status="not_story",
                reason="人物问题后的回复不含故事事实或感受", attempts=MAX_UNASSIGNED_ATTEMPTS,
                context_signature=context_signature)
    utterances = [
        item
        for item in project_sources
        if (item.id not in existing_material_ids or item.id in recovery_materials)
        and not item.completion_issue_id
        and item.chat_message_id not in person_answers
        and is_ready_story_material(item)
        and (item.id in recovery_materials or has_story_content(current_utterance_text(item)))
        and not (
            item.id in deferred
            and deferred[item.id].text_hash == source_text_hash(current_utterance_text(item))
            and (
                deferred[item.id].status == "not_story"
                or (
                    deferred[item.id].attempts >= MAX_UNASSIGNED_ATTEMPTS
                    and deferred[item.id].context_signature == context_signature
                )
            )
        )
    ]
    if progress.batch is None and utterances:
        _plan, chapters, sections = await _load_tree(db, project_id=job.project_id)
        batch = GrowthBatch(
            original_chapter_ids=[item.id for item in chapters],
            context_signature=context_signature,
            sources={
                item.id: (source_text_hash(current_utterance_text(item)), current_transcript_version_id(item))
                for item in utterances
            },
            section_versions={item.id: item.version for item in sections},
            section_chapters={item.id: item.chapter_id for item in sections},
            chapter_updated_at={item.id: item.updated_at for item in chapters},
            recovery_materials={item.id: recovery_materials[item.id] for item in utterances if item.id in recovery_materials},
        )
        ai_model = await load_project_ai_model(db, job.project_id, None)
        lines = await interviewer_lines(db, (item.chat_message_id for item in utterances))
        offered = await completion_interviews.offered_chapters(db, (item.chat_message_id for item in utterances))
        chapter_summaries, section_summaries = await load_structure_summaries(
            db, project_id=job.project_id, chapters=chapters, sections=sections,
        )
        await db.commit()
        batch.groups = await decide_story_growth(
            utterances=utterances,
            chapters=chapters,
            sections=sections,
            interviewer_lines=lines,
            ai_model=ai_model,
            offered_chapters=offered,
            chapter_summaries=chapter_summaries,
            section_summaries=section_summaries,
        )
        progress.batch = batch
        await _save_progress(db, job, progress)
    if progress.batch is not None:
        batch = progress.batch
        answer_ids = {item.id for item in project_sources if item.chat_message_id in person_answers}
        batch.groups = [replace(group, utterance_ids=[value for value in group.utterance_ids if value not in answer_ids])
                        for group in batch.groups]
        for group in batch.groups:
            if group.action == "not_story":
                for source_id in group.utterance_ids:
                    if source_id in batch.recovery_materials:
                        if await _remove_recovered_nonstory(db, project_id=job.project_id, batch=batch, source_id=source_id):
                            progress.deferred[source_id] = DeferredSource(
                                text_hash=batch.sources[source_id][0], status="not_story", reason=group.reason,
                                attempts=MAX_UNASSIGNED_ATTEMPTS, context_signature=batch.context_signature,
                            )
                            await _save_progress(db, job, progress)

        async def on_placed(utterance_id: str) -> None:
            progress.placed_ids.append(utterance_id)
            await _save_progress(db, job, progress)

        await _place_groups(db, user_id=project.user_id, project_id=job.project_id, batch=batch, on_placed=on_placed)
        await _position_new_chapters(db, project_id=job.project_id, batch=batch)
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
                attempts = previous.attempts if (
                    previous and previous.text_hash == text_hash
                    and previous.context_signature == batch.context_signature
                ) else 0
                if group.action == "not_story":
                    progress.deferred[source_id] = DeferredSource(
                        text_hash=text_hash, status="not_story", reason=group.reason, attempts=MAX_UNASSIGNED_ATTEMPTS,
                        context_signature=batch.context_signature,
                    )
                    continue
                if group.action == "leave_unassigned" and group.reason:
                    attempts += 1
                needs_user = attempts >= MAX_UNASSIGNED_ATTEMPTS
                progress.deferred[source_id] = DeferredSource(
                    text_hash=text_hash,
                    status="needs_user" if needs_user else "pending",
                    reason=group.reason if needs_user else "素材尚未归类，等待下一次整理",
                    attempts=attempts,
                    context_signature=batch.context_signature,
                )
        progress.batch = None
    # A saved person question already identifies its source. Only the original narration goes
    # through classification; its answer follows the source's current placement afterwards.
    for answer in linked_answers:
        source_id = person_answers[answer.chat_message_id]
        target = await _person_answer_target(db, job.project_id, source_id)
        if target is not None:
            try:
                await place_material(
                    db, user_id=project.user_id, project_id=job.project_id, utterance_id=answer.id,
                    req=MemoirSectionMaterialPlacementRequest(target_section_id=target[0]),
                    assigned_by=MemoirSectionMaterialAssignedBy.AI, structure_updated_by=MemoirCreatedBy.AI,
                    assignment_reason="person_question_answer",
                    expected_source=(source_text_hash(current_utterance_text(answer)), current_transcript_version_id(answer)),
                    expected_section_version=target[1], commit=False,
                )
            except HTTPException as exc:
                if exc.status_code not in {404, 409}:
                    raise
            else:
                progress.placed_ids.append(answer.id)
                progress.deferred.pop(answer.id, None)
                await _save_progress(db, job, progress)
                continue
        progress.deferred[answer.id] = DeferredSource(
            text_hash=source_text_hash(current_utterance_text(answer)), status="pending",
            reason="补充回答等待原讲述可收录后加入", context_signature=context_signature,
        )
    # Preserve placement checkpoints before reviewing; report only the final
    # directory, not a transient chapter that the same review absorbed.
    await _save_progress(db, job, progress)
    structure_changed = False
    if session.status == MemoirVoiceSessionStatus.STOPPED.value:
        lease_token = job.lease_token
        structure_changed = await review_story_structure(
            db, user_id=project.user_id, project_id=job.project_id,
            source="retry" if (job.payload_json or {}).get("source") == "retry" else "organized",
        )
        # The review crosses provider/checkpoint boundaries. Re-read task and
        # session state before accepting ownership of the final progress write.
        await db.refresh(job)
        await db.refresh(session)
        if job.status != MemoirEditingJobStatus.RUNNING.value or job.lease_token != lease_token:
            raise editing_jobs.EditingJobProgressLost
    placed_ids = list(dict.fromkeys(progress.placed_ids))
    progress.placed_ids = []

    _after_plan, after_chapters, after_sections = await _load_tree(
        db,
        project_id=job.project_id,
    )
    new_chapter_ids = [item.id for item in after_chapters if item.id not in before_chapter_ids]
    new_section_ids = [item.id for item in after_sections if item.id not in before_section_ids]
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
    return EditingJobResult(
        result_json={
            "voice_session_id": session.id,
            "growth": progress.model_dump(mode="json"),
            "initial_tree": initial_tree,
            "new_chapter_ids": new_chapter_ids,
            "new_section_ids": new_section_ids,
            "organized_utterance_ids": placed_ids,
        }
    ), structure_changed


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
    from app.services.memoir.automatic_book_draft import defer_narration_work
    waiting = await defer_narration_work(db, job)
    if waiting:
        waiting.result_json["stage"] = "waiting_for_narration"
        return waiting
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
    result, structure_changed = await grow_story_tree(db, job=job, session=session)
    if not structure_changed:
        from app.services.memoir.automatic_book_draft import schedule_book_draft
        await schedule_book_draft(db, project_id=job.project_id,
            source="retry" if (job.payload_json or {}).get("source") == "retry" else "organized", settled=True)
    return result
