from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir import (
    MemoirCoverageStatus,
    MemoirCreatedBy,
    MemoirInterviewStage,
    MemoirMemoryType,
    MemoirOpenQuestionStatus,
    MemoirQuestionCandidateStatus,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirQuestionType,
    MemoirSensitivity,
    MemoirSessionStatus,
)
from app.models.memoir import (
    MemoirCoverageTopic,
    MemoirEventMemory,
    MemoirOpenQuestion,
    MemoirQuestion,
    MemoirQuestionCandidate,
    MemoirSession,
)
from app.schemas.memoir import MemoirQuestionCandidateResponse, MemoirQuestionResponse
from app.services.memoir.access import load_memoir_project, load_session
from app.services.memoir.coverage import calculate_coverage_snapshots


def _clean(text: str | None) -> str:
    return (text or "").strip()


def _candidate_response(candidate: MemoirQuestionCandidate) -> MemoirQuestionCandidateResponse:
    return MemoirQuestionCandidateResponse.model_validate(candidate)


async def _existing_candidate_questions(
    db: AsyncSession,
    project_id: str,
) -> set[str]:
    result = await db.execute(
        select(MemoirQuestionCandidate.question).where(
            MemoirQuestionCandidate.project_id == project_id,
            MemoirQuestionCandidate.status.in_([
                MemoirQuestionCandidateStatus.CANDIDATE.value,
                MemoirQuestionCandidateStatus.SELECTED.value,
            ]),
        )
    )
    return {question for question in result.scalars()}


async def _add_candidate(
    db: AsyncSession,
    *,
    project_id: str,
    session_id: str | None,
    question: str,
    question_type: MemoirQuestionType,
    target_memory_type: str | None = None,
    target_memory_id: str | None = None,
    coverage_topic_id: str | None = None,
    reason: str,
    sensitivity: MemoirSensitivity = MemoirSensitivity.LOW,
    stage: MemoirInterviewStage = MemoirInterviewStage.DEPTH,
    priority: float,
    existing_questions: set[str],
) -> MemoirQuestionCandidate | None:
    prompt = _clean(question)
    if not prompt or prompt in existing_questions:
        return None
    candidate = MemoirQuestionCandidate(
        project_id=project_id,
        session_id=session_id,
        question=prompt,
        question_type=question_type.value,
        target_memory_type=target_memory_type,
        target_memory_id=target_memory_id,
        coverage_topic_id=coverage_topic_id,
        reason=reason,
        sensitivity=sensitivity.value,
        stage=stage.value,
        priority=max(0.0, min(1.0, priority)),
        status=MemoirQuestionCandidateStatus.CANDIDATE.value,
        created_by=MemoirCreatedBy.AI.value,
    )
    db.add(candidate)
    existing_questions.add(prompt)
    return candidate


async def _open_question_candidates(
    db: AsyncSession,
    project_id: str,
    session_id: str | None,
    existing_questions: set[str],
) -> list[MemoirQuestionCandidate]:
    result = await db.execute(
        select(MemoirOpenQuestion)
        .where(
            MemoirOpenQuestion.project_id == project_id,
            MemoirOpenQuestion.status == MemoirOpenQuestionStatus.OPEN.value,
        )
        .order_by(MemoirOpenQuestion.priority.desc(), MemoirOpenQuestion.created_at)
    )
    candidates: list[MemoirQuestionCandidate] = []
    for open_question in result.scalars():
        sensitivity = MemoirSensitivity(open_question.sensitivity)
        priority = open_question.priority if open_question.priority is not None else 0.72
        if sensitivity == MemoirSensitivity.HIGH:
            priority = min(priority, 0.62)
        candidate = await _add_candidate(
            db,
            project_id=project_id,
            session_id=session_id,
            question=open_question.question,
            question_type=MemoirQuestionType(open_question.question_type),
            target_memory_type=MemoirMemoryType.OPEN_QUESTION.value,
            target_memory_id=open_question.id,
            reason=open_question.reason or "待确认问题需要继续追问",
            sensitivity=sensitivity,
            stage=MemoirInterviewStage.DEPTH,
            priority=priority,
            existing_questions=existing_questions,
        )
        if candidate:
            candidates.append(candidate)
    return candidates


async def _event_gap_candidates(
    db: AsyncSession,
    project_id: str,
    session_id: str | None,
    existing_questions: set[str],
) -> list[MemoirQuestionCandidate]:
    result = await db.execute(
        select(MemoirEventMemory)
        .where(MemoirEventMemory.project_id == project_id)
        .order_by(MemoirEventMemory.importance.desc(), MemoirEventMemory.created_at)
    )
    candidates: list[MemoirQuestionCandidate] = []
    for event in result.scalars():
        title = event.title
        if not _clean(event.time_text) and not event.start_date:
            candidate = await _add_candidate(
                db,
                project_id=project_id,
                session_id=session_id,
                question=f"你刚才说的「{title}」，大概是在什么时候？",
                question_type=MemoirQuestionType.CLARIFICATION,
                target_memory_type=MemoirMemoryType.EVENT.value,
                target_memory_id=event.id,
                reason="关键事件缺少时间信息",
                priority=0.78,
                existing_questions=existing_questions,
            )
            if candidate:
                candidates.append(candidate)
        if not _clean(event.place_text):
            candidate = await _add_candidate(
                db,
                project_id=project_id,
                session_id=session_id,
                question=f"「{title}」这件事主要发生在哪里？",
                question_type=MemoirQuestionType.CLARIFICATION,
                target_memory_type=MemoirMemoryType.EVENT.value,
                target_memory_id=event.id,
                reason="关键事件缺少地点信息",
                priority=0.76,
                existing_questions=existing_questions,
            )
            if candidate:
                candidates.append(candidate)
        if not _clean(event.process):
            candidate = await _add_candidate(
                db,
                project_id=project_id,
                session_id=session_id,
                question=f"「{title}」当时具体是怎么发生的？",
                question_type=MemoirQuestionType.DEPTH,
                target_memory_type=MemoirMemoryType.EVENT.value,
                target_memory_id=event.id,
                reason="关键事件缺少经过细节",
                priority=0.7,
                existing_questions=existing_questions,
            )
            if candidate:
                candidates.append(candidate)
        if not _clean(event.emotion):
            candidate = await _add_candidate(
                db,
                project_id=project_id,
                session_id=session_id,
                question=f"现在回头看「{title}」，你心里最明显的感觉是什么？",
                question_type=MemoirQuestionType.EMOTION,
                target_memory_type=MemoirMemoryType.EVENT.value,
                target_memory_id=event.id,
                reason="关键事件缺少回望情绪",
                priority=0.64,
                existing_questions=existing_questions,
            )
            if candidate:
                candidates.append(candidate)
    return candidates


async def _coverage_gap_candidates(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    session_id: str | None,
    existing_questions: set[str],
) -> list[MemoirQuestionCandidate]:
    snapshots = await calculate_coverage_snapshots(db, user_id, project_id)
    has_any_signal = any(
        snapshot.answered_count or snapshot.memory_count or snapshot.open_question_count
        for snapshot in snapshots
    )
    if not has_any_signal:
        return []

    candidates: list[MemoirQuestionCandidate] = []
    for snapshot in snapshots:
        if snapshot.status == MemoirCoverageStatus.GREEN:
            continue
        topic = await db.scalar(
            select(MemoirCoverageTopic).where(MemoirCoverageTopic.id == snapshot.topic_id)
        )
        if not topic:
            continue
        priority = 0.45 + (100.0 - snapshot.coverage_score) / 400.0
        candidate = await _add_candidate(
            db,
            project_id=project_id,
            session_id=session_id,
            question=f"关于「{topic.name}」，你愿意多讲一件现在还记得的事吗？",
            question_type=MemoirQuestionType.DEPTH,
            coverage_topic_id=topic.id,
            reason="人生覆盖度较低，需要补足基础材料",
            sensitivity=MemoirSensitivity.LOW,
            stage=MemoirInterviewStage.SKELETON,
            priority=priority,
            existing_questions=existing_questions,
        )
        if candidate:
            candidates.append(candidate)
    return candidates


async def generate_question_candidates(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    session_id: str | None = None,
) -> list[MemoirQuestionCandidateResponse]:
    await load_memoir_project(db, project_id, user_id)
    if session_id:
        session = await load_session(db, session_id, user_id)
        if session.project_id != project_id:
            return []

    existing_questions = await _existing_candidate_questions(db, project_id)
    candidates: list[MemoirQuestionCandidate] = []
    candidates.extend(
        await _open_question_candidates(db, project_id, session_id, existing_questions)
    )
    candidates.extend(
        await _event_gap_candidates(db, project_id, session_id, existing_questions)
    )
    candidates.extend(
        await _coverage_gap_candidates(db, user_id, project_id, session_id, existing_questions)
    )
    await db.flush()
    return [_candidate_response(candidate) for candidate in candidates]


async def _candidate_for_user(
    db: AsyncSession,
    user_id: str,
    candidate_id: str,
) -> MemoirQuestionCandidate:
    result = await db.execute(
        select(MemoirQuestionCandidate)
        .where(MemoirQuestionCandidate.id == candidate_id)
    )
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="问题候选不存在")
    await load_memoir_project(db, candidate.project_id, user_id)
    return candidate


async def best_question_candidate(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    session_id: str,
) -> MemoirQuestionCandidate | None:
    await generate_question_candidates(db, user_id, project_id, session_id)
    result = await db.execute(
        select(MemoirQuestionCandidate)
        .where(
            MemoirQuestionCandidate.project_id == project_id,
            MemoirQuestionCandidate.session_id == session_id,
            MemoirQuestionCandidate.status == MemoirQuestionCandidateStatus.CANDIDATE.value,
        )
    )
    candidates = list(result.scalars())
    if not candidates:
        return None

    non_high = [
        candidate
        for candidate in candidates
        if candidate.sensitivity != MemoirSensitivity.HIGH.value
    ]
    eligible = non_high or candidates
    return max(eligible, key=lambda candidate: (candidate.priority, candidate.created_at))


async def list_question_candidates(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    session_id: str | None = None,
) -> list[MemoirQuestionCandidateResponse]:
    await load_memoir_project(db, project_id, user_id)
    filters = [
        MemoirQuestionCandidate.project_id == project_id,
        MemoirQuestionCandidate.status == MemoirQuestionCandidateStatus.CANDIDATE.value,
    ]
    if session_id:
        filters.append(MemoirQuestionCandidate.session_id == session_id)
    result = await db.execute(
        select(MemoirQuestionCandidate)
        .where(*filters)
        .order_by(MemoirQuestionCandidate.priority.desc(), MemoirQuestionCandidate.created_at)
    )
    return [_candidate_response(candidate) for candidate in result.scalars()]


async def select_candidate_as_question(
    db: AsyncSession,
    user_id: str,
    candidate_id: str,
) -> MemoirQuestionResponse:
    candidate = await _candidate_for_user(db, user_id, candidate_id)
    if candidate.status != MemoirQuestionCandidateStatus.CANDIDATE.value:
        raise HTTPException(status_code=409, detail="问题候选当前不可选择")
    if not candidate.session_id:
        raise HTTPException(status_code=400, detail="问题候选未关联访谈")

    existing_question = await db.scalar(
        select(MemoirQuestion).where(
            MemoirQuestion.session_id == candidate.session_id,
            MemoirQuestion.prompt == candidate.question,
            MemoirQuestion.status == MemoirQuestionStatus.PENDING.value,
        )
    )
    if existing_question:
        question = existing_question
    else:
        max_sort_order = await db.scalar(
            select(func.max(MemoirQuestion.sort_order)).where(
                MemoirQuestion.session_id == candidate.session_id
            )
        )
        question = MemoirQuestion(
            session_id=candidate.session_id,
            prompt=candidate.question,
            source=MemoirQuestionSource.AI_GENERATED.value,
            sort_order=(max_sort_order or 0) + 1,
            status=MemoirQuestionStatus.PENDING.value,
        )
        db.add(question)
        await db.flush()

    candidate.status = MemoirQuestionCandidateStatus.SELECTED.value
    if candidate.target_memory_type == MemoirMemoryType.OPEN_QUESTION.value and candidate.target_memory_id:
        open_question = await db.scalar(
            select(MemoirOpenQuestion).where(MemoirOpenQuestion.id == candidate.target_memory_id)
        )
        if open_question:
            open_question.status = MemoirOpenQuestionStatus.ASKED.value

    session = await load_session(db, candidate.session_id, user_id)
    session.current_question_id = question.id
    session.status = MemoirSessionStatus.ACTIVE.value
    await db.commit()
    return MemoirQuestionResponse.model_validate(question)


async def get_or_create_planned_question(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    session_id: str,
) -> MemoirQuestionResponse | None:
    existing = await db.scalar(
        select(MemoirQuestion).where(
            MemoirQuestion.session_id == session_id,
            MemoirQuestion.source == MemoirQuestionSource.AI_GENERATED.value,
            MemoirQuestion.status == MemoirQuestionStatus.PENDING.value,
        )
    )
    if existing:
        return MemoirQuestionResponse.model_validate(existing)

    candidate = await best_question_candidate(db, user_id, project_id, session_id)
    if not candidate:
        return None
    return await select_candidate_as_question(db, user_id, candidate.id)
