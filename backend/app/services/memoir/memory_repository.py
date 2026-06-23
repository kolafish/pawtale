from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir import MemoirMemoryStatus, MemoirMemoryType
from app.models.memoir import (
    MemoirAnswer,
    MemoirEventMemory,
    MemoirMemoryEvidence,
    MemoirOpenQuestion,
    MemoirPersonMemory,
    MemoirPlaceMemory,
    MemoirQuestion,
    MemoirSession,
    MemoirThemeMemory,
    MemoirTranscriptVersion,
)
from app.schemas.memoir_memory import (
    MemoirEventMemoryCreate,
    MemoirEventMemoryResponse,
    MemoirMemoryUpdateRequest,
    MemoirMemoryEvidenceCreate,
    MemoirMemoryEvidenceResponse,
    MemoirOpenQuestionCreate,
    MemoirOpenQuestionResponse,
    MemoirOpenQuestionUpdateRequest,
    MemoirPersonMemoryCreate,
    MemoirPersonMemoryResponse,
    MemoirPlaceMemoryCreate,
    MemoirPlaceMemoryResponse,
    MemoirThemeMemoryCreate,
    MemoirThemeMemoryResponse,
)
from app.services.memoir.access import load_answer, load_memoir_project, load_question, load_session


MemoryModel = TypeVar(
    "MemoryModel",
    MemoirPersonMemory,
    MemoirEventMemory,
    MemoirPlaceMemory,
    MemoirThemeMemory,
    MemoirOpenQuestion,
)

_MEMORY_MODEL_BY_TYPE = {
    MemoirMemoryType.PERSON: MemoirPersonMemory,
    MemoirMemoryType.EVENT: MemoirEventMemory,
    MemoirMemoryType.PLACE: MemoirPlaceMemory,
    MemoirMemoryType.THEME: MemoirThemeMemory,
    MemoirMemoryType.OPEN_QUESTION: MemoirOpenQuestion,
}


async def _ensure_project(db: AsyncSession, project_id: str, user_id: str) -> None:
    await load_memoir_project(db, project_id, user_id)


async def _ensure_optional_session(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    session_id: str | None,
) -> MemoirSession | None:
    if not session_id:
        return None
    session = await load_session(db, session_id, user_id)
    if session.project_id != project_id:
        raise HTTPException(status_code=400, detail="访谈不属于该回忆录项目")
    return session


async def _ensure_optional_question(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    question_id: str | None,
) -> MemoirQuestion | None:
    if not question_id:
        return None
    question = await load_question(db, question_id, user_id)
    if question.session.project_id != project_id:
        raise HTTPException(status_code=400, detail="问题不属于该回忆录项目")
    return question


async def _ensure_optional_answer(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    answer_id: str | None,
) -> MemoirAnswer | None:
    if not answer_id:
        return None
    answer = await load_answer(db, answer_id, user_id)
    if answer.question.session.project_id != project_id:
        raise HTTPException(status_code=400, detail="回答不属于该回忆录项目")
    return answer


async def _ensure_optional_transcript_version(
    db: AsyncSession,
    project_id: str,
    transcript_version_id: str | None,
) -> tuple[MemoirTranscriptVersion, MemoirAnswer, MemoirQuestion] | None:
    if not transcript_version_id:
        return None
    result = await db.execute(
        select(MemoirTranscriptVersion, MemoirAnswer, MemoirQuestion, MemoirSession.project_id)
        .join(MemoirAnswer, MemoirTranscriptVersion.answer_id == MemoirAnswer.id)
        .join(MemoirQuestion, MemoirAnswer.question_id == MemoirQuestion.id)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .where(MemoirTranscriptVersion.id == transcript_version_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="转写版本不存在")
    version, answer, question, version_project_id = row
    if version_project_id != project_id:
        raise HTTPException(status_code=400, detail="转写版本不属于该回忆录项目")
    return version, answer, question


async def _load_memory(
    db: AsyncSession,
    project_id: str,
    memory_type: MemoirMemoryType,
    memory_id: str,
) -> MemoryModel:
    model = _MEMORY_MODEL_BY_TYPE[memory_type]
    memory = await db.scalar(
        select(model).where(model.id == memory_id, model.project_id == project_id)
    )
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return memory


async def create_person_memory(
    db: AsyncSession,
    user_id: str,
    req: MemoirPersonMemoryCreate,
) -> MemoirPersonMemoryResponse:
    await _ensure_project(db, req.project_id, user_id)
    await _ensure_optional_session(db, req.project_id, user_id, req.first_seen_session_id)
    memory = MemoirPersonMemory(
        project_id=req.project_id,
        canonical_name=req.canonical_name.strip(),
        aliases_json=req.aliases_json,
        relationship=req.relationship,
        first_seen_session_id=req.first_seen_session_id,
        description=req.description,
        confidence=req.confidence,
        status=req.status.value,
        created_by=req.created_by.value,
    )
    db.add(memory)
    await db.flush()
    return MemoirPersonMemoryResponse.model_validate(memory)


async def create_event_memory(
    db: AsyncSession,
    user_id: str,
    req: MemoirEventMemoryCreate,
) -> MemoirEventMemoryResponse:
    await _ensure_project(db, req.project_id, user_id)
    memory = MemoirEventMemory(
        project_id=req.project_id,
        title=req.title.strip(),
        life_stage=req.life_stage,
        time_text=req.time_text,
        start_date=req.start_date,
        end_date=req.end_date,
        place_text=req.place_text,
        cause=req.cause,
        process=req.process,
        result=req.result,
        emotion=req.emotion,
        importance=req.importance,
        confidence=req.confidence,
        status=req.status.value,
        created_by=req.created_by.value,
    )
    db.add(memory)
    await db.flush()
    return MemoirEventMemoryResponse.model_validate(memory)


async def create_place_memory(
    db: AsyncSession,
    user_id: str,
    req: MemoirPlaceMemoryCreate,
) -> MemoirPlaceMemoryResponse:
    await _ensure_project(db, req.project_id, user_id)
    memory = MemoirPlaceMemory(
        project_id=req.project_id,
        name=req.name.strip(),
        elder_alias=req.elder_alias,
        modern_name=req.modern_name,
        place_type=req.place_type,
        description=req.description,
        confidence=req.confidence,
        status=req.status.value,
        created_by=req.created_by.value,
    )
    db.add(memory)
    await db.flush()
    return MemoirPlaceMemoryResponse.model_validate(memory)


async def create_theme_memory(
    db: AsyncSession,
    user_id: str,
    req: MemoirThemeMemoryCreate,
) -> MemoirThemeMemoryResponse:
    await _ensure_project(db, req.project_id, user_id)
    memory = MemoirThemeMemory(
        project_id=req.project_id,
        name=req.name.strip(),
        description=req.description,
        strength=req.strength,
        mention_count=req.mention_count,
        confidence=req.confidence,
        status=req.status.value,
        created_by=req.created_by.value,
    )
    db.add(memory)
    await db.flush()
    return MemoirThemeMemoryResponse.model_validate(memory)


async def create_open_question(
    db: AsyncSession,
    user_id: str,
    req: MemoirOpenQuestionCreate,
) -> MemoirOpenQuestionResponse:
    await _ensure_project(db, req.project_id, user_id)
    await _ensure_optional_session(db, req.project_id, user_id, req.session_id)
    if req.target_memory_type and req.target_memory_id:
        await _load_memory(db, req.project_id, req.target_memory_type, req.target_memory_id)
    memory = MemoirOpenQuestion(
        project_id=req.project_id,
        session_id=req.session_id,
        target_memory_type=req.target_memory_type.value if req.target_memory_type else None,
        target_memory_id=req.target_memory_id,
        question=req.question.strip(),
        question_type=req.question_type.value,
        reason=req.reason,
        sensitivity=req.sensitivity.value,
        priority=req.priority,
        status=req.status.value,
        created_by=req.created_by.value,
    )
    db.add(memory)
    await db.flush()
    return MemoirOpenQuestionResponse.model_validate(memory)


async def add_memory_evidence(
    db: AsyncSession,
    user_id: str,
    req: MemoirMemoryEvidenceCreate,
) -> MemoirMemoryEvidenceResponse:
    await _ensure_project(db, req.project_id, user_id)
    await _load_memory(db, req.project_id, req.memory_type, req.memory_id)
    session = await _ensure_optional_session(db, req.project_id, user_id, req.session_id)
    question = await _ensure_optional_question(db, req.project_id, user_id, req.question_id)
    answer = await _ensure_optional_answer(db, req.project_id, user_id, req.answer_id)
    transcript_context = await _ensure_optional_transcript_version(
        db,
        req.project_id,
        req.transcript_version_id,
    )

    if session and question and question.session_id != session.id:
        raise HTTPException(status_code=400, detail="问题不属于该访谈")
    if session and answer and answer.question.session_id != session.id:
        raise HTTPException(status_code=400, detail="回答不属于该访谈")
    if question and answer and answer.question_id != question.id:
        raise HTTPException(status_code=400, detail="回答不属于该问题")
    if transcript_context:
        transcript, transcript_answer, transcript_question = transcript_context
        if session and transcript_question.session_id != session.id:
            raise HTTPException(status_code=400, detail="转写版本不属于该访谈")
        if question and transcript_answer.question_id != question.id:
            raise HTTPException(status_code=400, detail="转写版本不属于该问题")
        if answer and transcript.answer_id != answer.id:
            raise HTTPException(status_code=400, detail="转写版本不属于该回答")

    existing = await db.scalar(
        select(MemoirMemoryEvidence).where(
            MemoirMemoryEvidence.project_id == req.project_id,
            MemoirMemoryEvidence.memory_type == req.memory_type.value,
            MemoirMemoryEvidence.memory_id == req.memory_id,
            MemoirMemoryEvidence.session_id == req.session_id,
            MemoirMemoryEvidence.question_id == req.question_id,
            MemoirMemoryEvidence.answer_id == req.answer_id,
            MemoirMemoryEvidence.transcript_version_id == req.transcript_version_id,
            MemoirMemoryEvidence.quote == req.quote,
        )
    )
    if existing:
        return MemoirMemoryEvidenceResponse.model_validate(existing)

    evidence = MemoirMemoryEvidence(
        project_id=req.project_id,
        memory_type=req.memory_type.value,
        memory_id=req.memory_id,
        session_id=req.session_id,
        question_id=req.question_id,
        answer_id=req.answer_id,
        transcript_version_id=req.transcript_version_id,
        quote=req.quote,
        start_offset=req.start_offset,
        end_offset=req.end_offset,
        confidence=req.confidence,
    )
    db.add(evidence)
    await db.flush()
    return MemoirMemoryEvidenceResponse.model_validate(evidence)


async def update_memory_status(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    memory_type: MemoirMemoryType,
    memory_id: str,
    status: MemoirMemoryStatus,
) -> object:
    await _ensure_project(db, project_id, user_id)
    if memory_type == MemoirMemoryType.OPEN_QUESTION:
        raise HTTPException(status_code=400, detail="待确认问题不使用结构化记忆状态")
    memory = await _load_memory(db, project_id, memory_type, memory_id)
    memory.status = status.value
    await db.flush()
    return memory


def _memory_response(memory_type: MemoirMemoryType, memory: object) -> object:
    response_by_type = {
        MemoirMemoryType.PERSON: MemoirPersonMemoryResponse,
        MemoirMemoryType.EVENT: MemoirEventMemoryResponse,
        MemoirMemoryType.PLACE: MemoirPlaceMemoryResponse,
        MemoirMemoryType.THEME: MemoirThemeMemoryResponse,
        MemoirMemoryType.OPEN_QUESTION: MemoirOpenQuestionResponse,
    }
    return response_by_type[memory_type].model_validate(memory)


async def update_memory(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    memory_type: MemoirMemoryType,
    memory_id: str,
    req: MemoirMemoryUpdateRequest,
) -> object:
    await _ensure_project(db, project_id, user_id)
    if memory_type == MemoirMemoryType.OPEN_QUESTION:
        raise HTTPException(status_code=400, detail="待确认问题请使用 open questions 接口")
    memory = await _load_memory(db, project_id, memory_type, memory_id)
    if req.status is not None:
        memory.status = req.status.value

    fields_by_type = {
        MemoirMemoryType.PERSON: ("canonical_name", "aliases_json", "relationship", "description", "confidence"),
        MemoirMemoryType.EVENT: (
            "title",
            "life_stage",
            "time_text",
            "place_text",
            "cause",
            "process",
            "result",
            "emotion",
            "importance",
            "confidence",
        ),
        MemoirMemoryType.PLACE: (
            "name",
            "elder_alias",
            "modern_name",
            "place_type",
            "description",
            "confidence",
        ),
        MemoirMemoryType.THEME: ("name", "description", "strength", "mention_count", "confidence"),
    }
    for field in fields_by_type[memory_type]:
        value = getattr(req, field)
        if value is None:
            continue
        setattr(memory, field, value.strip() if isinstance(value, str) else value)

    await db.flush()
    return _memory_response(memory_type, memory)


async def list_person_memories(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirPersonMemoryResponse]:
    await _ensure_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirPersonMemory)
        .where(MemoirPersonMemory.project_id == project_id)
        .order_by(MemoirPersonMemory.created_at)
    )
    return [MemoirPersonMemoryResponse.model_validate(row) for row in result.scalars()]


async def list_event_memories(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirEventMemoryResponse]:
    await _ensure_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirEventMemory)
        .where(MemoirEventMemory.project_id == project_id)
        .order_by(MemoirEventMemory.created_at)
    )
    return [MemoirEventMemoryResponse.model_validate(row) for row in result.scalars()]


async def list_place_memories(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirPlaceMemoryResponse]:
    await _ensure_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirPlaceMemory)
        .where(MemoirPlaceMemory.project_id == project_id)
        .order_by(MemoirPlaceMemory.created_at)
    )
    return [MemoirPlaceMemoryResponse.model_validate(row) for row in result.scalars()]


async def list_theme_memories(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirThemeMemoryResponse]:
    await _ensure_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirThemeMemory)
        .where(MemoirThemeMemory.project_id == project_id)
        .order_by(MemoirThemeMemory.created_at)
    )
    return [MemoirThemeMemoryResponse.model_validate(row) for row in result.scalars()]


async def list_open_questions(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirOpenQuestionResponse]:
    await _ensure_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirOpenQuestion)
        .where(MemoirOpenQuestion.project_id == project_id)
        .order_by(MemoirOpenQuestion.priority.desc(), MemoirOpenQuestion.created_at)
    )
    return [MemoirOpenQuestionResponse.model_validate(row) for row in result.scalars()]


async def update_open_question(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    question_id: str,
    req: MemoirOpenQuestionUpdateRequest,
) -> MemoirOpenQuestionResponse:
    await _ensure_project(db, project_id, user_id)
    open_question = await db.scalar(
        select(MemoirOpenQuestion).where(
            MemoirOpenQuestion.project_id == project_id,
            MemoirOpenQuestion.id == question_id,
        )
    )
    if not open_question:
        raise HTTPException(status_code=404, detail="待确认问题不存在")
    if req.question is not None:
        value = req.question.strip()
        if not value:
            raise HTTPException(status_code=400, detail="问题不能为空")
        open_question.question = value
    if req.reason is not None:
        open_question.reason = req.reason.strip()
    if req.sensitivity is not None:
        open_question.sensitivity = req.sensitivity.value
    if req.priority is not None:
        open_question.priority = req.priority
    if req.status is not None:
        open_question.status = req.status.value
    await db.flush()
    return MemoirOpenQuestionResponse.model_validate(open_question)
