from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import re

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirShareVideoCandidateStatus,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import (
    MemoirEditingJob,
    MemoirShareVideoCandidate,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_voice import (
    MemoirShareVideoCandidateCreateVideoRequest,
    MemoirShareVideoCandidateResponse,
    MemoirShareVideoJobCreateRequest,
    MemoirShareVideoJobResponse,
)
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.access import load_memoir_project
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.material_roles import is_narrator_material
from app.services.memoir.project_ai_preferences import load_project_ai_model
from app.services.memoir.share_video_alignment import (
    build_share_video_alignment,
    share_video_char_range_ms,
)
from app.services.memoir.share_video_jobs import create_share_video_job
from app.services.memoir.transcript_projection import current_transcript_version_id


logger = logging.getLogger(__name__)
ALGORITHM_VERSION = "share-candidate-v1"
MIN_CANDIDATE_MS = 15_000
MAX_CANDIDATE_MS = 45_000
TARGET_CANDIDATE_MS = 30_000
MIN_ALIGNMENT_CONFIDENCE = 0.72
MAX_MODEL_WINDOWS = 12
MAX_WINDOWS_PER_UTTERANCE = 3
CANDIDATE_AGE_LIMIT = timedelta(days=7)
_SENSITIVE_NUMBER = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)")


@dataclass(frozen=True)
class CandidateWindow:
    utterance_id: str
    audio_file_id: str
    transcript_version_id: str
    text_hash: str
    selected_text: str
    start_char: int
    end_char: int
    start_ms: int
    end_ms: int
    score: float

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class _CandidateChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: int | None


def _trim_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def candidate_windows_for_utterance(utterance: MemoirVoiceUtterance) -> list[CandidateWindow]:
    if (
        not is_narrator_material(utterance)
        or utterance.completion_issue_id is not None
        or not (utterance.transcript_edited or "").strip()
        or not utterance.audio_file_id
    ):
        return []
    transcript_version_id = current_transcript_version_id(utterance)
    if not transcript_version_id:
        return []
    try:
        alignment = build_share_video_alignment(utterance)
    except HTTPException:
        return []
    if (alignment.confidence or 0) < MIN_ALIGNMENT_CONFIDENCE:
        return []
    text = alignment.transcript_text
    windows: list[CandidateWindow] = []
    for first_index, first in enumerate(alignment.segments):
        best: CandidateWindow | None = None
        for last in alignment.segments[first_index:]:
            start_char, end_char = _trim_range(text, int(first["start_char"]), int(last["end_char"]))
            if end_char <= start_char:
                continue
            start_ms, end_ms = share_video_char_range_ms(alignment, start_char, end_char)
            duration_ms = end_ms - start_ms
            if duration_ms > MAX_CANDIDATE_MS:
                break
            if duration_ms < MIN_CANDIDATE_MS:
                continue
            selected_text = text[start_char:end_char]
            visible_chars = sum(not char.isspace() for char in selected_text)
            if not 26 <= visible_chars <= 220 or _SENSITIVE_NUMBER.search(selected_text):
                continue
            score = 6 * min(visible_chars, 120) / 120 - abs(duration_ms - TARGET_CANDIDATE_MS) / 5000
            window = CandidateWindow(
                utterance_id=utterance.id,
                audio_file_id=utterance.audio_file_id,
                transcript_version_id=transcript_version_id,
                text_hash=alignment.text_hash,
                selected_text=selected_text,
                start_char=start_char,
                end_char=end_char,
                start_ms=start_ms,
                end_ms=end_ms,
                score=score,
            )
            if best is None or window.score > best.score:
                best = window
        if best is not None:
            windows.append(best)
    chosen: list[CandidateWindow] = []
    for window in sorted(windows, key=lambda item: item.score, reverse=True):
        if any(
            max(0, min(window.end_ms, item.end_ms) - max(window.start_ms, item.start_ms))
            > min(window.duration_ms, item.duration_ms) / 2
            for item in chosen
        ):
            continue
        chosen.append(window)
        if len(chosen) >= MAX_WINDOWS_PER_UTTERANCE:
            break
    return sorted(chosen, key=lambda item: item.start_ms)


async def _choose_window(windows: list[CandidateWindow], *, ai_model: str | None) -> int | None:
    policy = ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)
    prompt = json.dumps({
        "candidates": [
            {"id": index, "duration_seconds": round(window.duration_ms / 1000), "text": window.selected_text}
            for index, window in enumerate(windows, start=1)
        ]
    }, ensure_ascii=False)
    generated = await generate_structured_with_client(
        get_ai_service(ai_model),
        "你在为家庭回忆录筛选一段讲述原声。只选一条独立看也能理解、具体生动、保留讲述者真实口吻、适合家人观看的片段。"
        "若内容只是寒暄、采访提问、重复、语义不完整、缺少具体故事，或明显包含私密身份信息，candidate_id 就填 null。"
        "不要改写内容；candidate_id 只能填候选列表中的编号。宁可不推荐，也不要凑数。"
        '只返回一个 JSON 对象，且只能包含 candidate_id 字段：推荐时如 {"candidate_id":1}，不推荐时为 {"candidate_id":null}。'
        "不要返回裸编号、裸 null、Markdown 或解释。",
        prompt,
        output_model=_CandidateChoice,
        requested_model=ai_model,
        temperature=0.1,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    choice = generated.value.candidate_id
    return choice - 1 if choice is not None and 1 <= choice <= len(windows) else None


async def _load_session(db: AsyncSession, project_id: str, session_id: str) -> MemoirVoiceSession | None:
    return await db.scalar(
        select(MemoirVoiceSession)
        .where(MemoirVoiceSession.id == session_id, MemoirVoiceSession.project_id == project_id)
        .options(
            selectinload(MemoirVoiceSession.utterances).selectinload(MemoirVoiceUtterance.audio_file),
            selectinload(MemoirVoiceSession.utterances).selectinload(MemoirVoiceUtterance.chat_message),
        )
        .execution_options(populate_existing=True)
    )


async def _active_session_corrections(db: AsyncSession, session: MemoirVoiceSession) -> bool:
    keys = [f"correct_transcript:{utterance.id}" for utterance in session.utterances]
    if not keys:
        return False
    return await db.scalar(select(MemoirEditingJob.id).where(
        MemoirEditingJob.project_id == session.project_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.CORRECT_TRANSCRIPT.value,
        MemoirEditingJob.dedupe_key.in_(keys),
        MemoirEditingJob.status.in_((
            MemoirEditingJobStatus.PENDING.value,
            MemoirEditingJobStatus.RUNNING.value,
        )),
    ).limit(1)) is not None


def _candidate_current(candidate: MemoirShareVideoCandidate, utterance: MemoirVoiceUtterance | None) -> bool:
    if utterance is None or not is_narrator_material(utterance) or not utterance.transcript_edited:
        return False
    if (
        not candidate.transcript_version_id or not candidate.text_hash
        or candidate.start_char is None or candidate.end_char is None
        or candidate.start_ms is None or candidate.end_ms is None
    ):
        return False
    if utterance.audio_file_id != candidate.audio_file_id:
        return False
    if current_transcript_version_id(utterance) != candidate.transcript_version_id:
        return False
    try:
        alignment = build_share_video_alignment(utterance)
        start_ms, end_ms = share_video_char_range_ms(alignment, candidate.start_char, candidate.end_char)
    except (HTTPException, TypeError):
        return False
    return (
        alignment.text_hash == candidate.text_hash
        and alignment.transcript_text[candidate.start_char:candidate.end_char] == candidate.selected_text
        and (start_ms, end_ms) == (candidate.start_ms, candidate.end_ms)
    )


def _response(candidate: MemoirShareVideoCandidate) -> MemoirShareVideoCandidateResponse:
    return MemoirShareVideoCandidateResponse(
        id=candidate.id,
        project_id=candidate.project_id,
        voice_session_id=candidate.voice_session_id,
        utterance_id=candidate.utterance_id,
        audio_file_id=candidate.audio_file_id,
        transcript_version_id=candidate.transcript_version_id,
        text_hash=candidate.text_hash,
        selected_text=candidate.selected_text,
        start_char=candidate.start_char,
        end_char=candidate.end_char,
        start_ms=candidate.start_ms,
        end_ms=candidate.end_ms,
        duration_ms=(candidate.end_ms - candidate.start_ms) if candidate.end_ms is not None and candidate.start_ms is not None else None,
        status=candidate.status,
        video_job_id=candidate.video_job_id,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


async def run_share_video_candidate_job(db: AsyncSession, job: MemoirEditingJob) -> EditingJobResult:
    session_id = str((job.payload_json or {}).get("voice_session_id") or "")
    session = await _load_session(db, job.project_id, session_id)
    if session is None or session.status != MemoirVoiceSessionStatus.STOPPED.value:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="session unavailable")
    existing = await db.scalar(select(MemoirShareVideoCandidate).where(
        MemoirShareVideoCandidate.voice_session_id == session_id,
    ))
    if existing is not None and existing.status in {
        MemoirShareVideoCandidateStatus.DISMISSED.value,
        MemoirShareVideoCandidateStatus.ACCEPTED.value,
        MemoirShareVideoCandidateStatus.STALE.value,
    }:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="candidate already handled")
    if await _active_session_corrections(db, session):
        job.run_after = utcnow_naive() + timedelta(seconds=5)
        db.add(job)
        await db.flush()
        return EditingJobResult(
            status=MemoirEditingJobStatus.PENDING,
            result_json={"stage": "waiting_for_transcripts", "voice_session_id": session_id},
        )

    windows = [
        window
        for utterance in sorted(session.utterances, key=lambda item: (item.created_at, item.id))
        for window in candidate_windows_for_utterance(utterance)
    ]
    windows = sorted(windows, key=lambda item: item.score, reverse=True)[:MAX_MODEL_WINDOWS]
    if windows:
        # Release read locks before the external model call. The selected source is
        # checked again against the current audio and transcript before storage.
        await db.commit()
        ai_model = await load_project_ai_model(db, job.project_id)
        choice = await _choose_window(windows, ai_model=ai_model)
        selected = windows[choice] if choice is not None else None
    else:
        selected = None

    if selected is not None:
        probe = MemoirShareVideoCandidate(
            project_id=job.project_id,
            voice_session_id=session_id,
            utterance_id=selected.utterance_id,
            audio_file_id=selected.audio_file_id,
            transcript_version_id=selected.transcript_version_id,
            text_hash=selected.text_hash,
            selected_text=selected.selected_text,
            start_char=selected.start_char,
            end_char=selected.end_char,
            start_ms=selected.start_ms,
            end_ms=selected.end_ms,
        )
        utterance = await _load_current_utterance(db, probe, for_update=True)
        if not _candidate_current(probe, utterance):
            job.run_after = utcnow_naive() + timedelta(seconds=5)
            db.add(job)
            await db.flush()
            return EditingJobResult(status=MemoirEditingJobStatus.PENDING, result_json={"stage": "source_changed"})

    existing = await db.scalar(
        select(MemoirShareVideoCandidate)
        .where(MemoirShareVideoCandidate.voice_session_id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None and existing.status in {
        MemoirShareVideoCandidateStatus.DISMISSED.value,
        MemoirShareVideoCandidateStatus.ACCEPTED.value,
        MemoirShareVideoCandidateStatus.STALE.value,
    }:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="candidate already handled")
    candidate = existing or MemoirShareVideoCandidate(project_id=job.project_id, voice_session_id=session_id)
    if selected is None:
        candidate.status = MemoirShareVideoCandidateStatus.NONE.value
        candidate.reason_code = "no_eligible_window" if not windows else "model_declined"
        candidate.selected_text = ""
    else:
        candidate.utterance_id = selected.utterance_id
        candidate.audio_file_id = selected.audio_file_id
        candidate.transcript_version_id = selected.transcript_version_id
        candidate.text_hash = selected.text_hash
        candidate.selected_text = selected.selected_text
        candidate.start_char = selected.start_char
        candidate.end_char = selected.end_char
        candidate.start_ms = selected.start_ms
        candidate.end_ms = selected.end_ms
        candidate.status = MemoirShareVideoCandidateStatus.READY.value
        candidate.reason_code = "model_selected"
    candidate.algorithm_version = ALGORITHM_VERSION
    db.add(candidate)
    await db.flush()
    logger.info(
        "share_video_candidate_evaluated project_id=%s session_id=%s status=%s reason=%s windows=%s",
        job.project_id, session_id, candidate.status, candidate.reason_code, len(windows),
    )
    return EditingJobResult(result_json={
        "candidate_id": candidate.id,
        "status": candidate.status,
        "reason_code": candidate.reason_code,
        "windows_considered": len(windows),
    })


async def _owned_candidate(
    db: AsyncSession, *, user_id: str, project_id: str, candidate_id: str,
) -> MemoirShareVideoCandidate:
    await load_memoir_project(db, project_id, user_id)
    candidate = await db.scalar(select(MemoirShareVideoCandidate).where(
        MemoirShareVideoCandidate.id == candidate_id,
        MemoirShareVideoCandidate.project_id == project_id,
    ))
    if candidate is None:
        raise HTTPException(status_code=404, detail="分享片段不存在")
    return candidate


async def _load_current_utterance(
    db: AsyncSession, candidate: MemoirShareVideoCandidate, *, for_update: bool = False,
) -> MemoirVoiceUtterance | None:
    if not candidate.utterance_id:
        return None
    statement = (
        select(MemoirVoiceUtterance)
        .where(
            MemoirVoiceUtterance.id == candidate.utterance_id,
            MemoirVoiceUtterance.voice_session_id == candidate.voice_session_id,
        )
        .options(
            selectinload(MemoirVoiceUtterance.audio_file),
            selectinload(MemoirVoiceUtterance.chat_message),
        )
        .execution_options(populate_existing=True)
    )
    if for_update:
        statement = statement.with_for_update(of=MemoirVoiceUtterance)
    return await db.scalar(statement)


async def get_latest_share_video_candidate(
    db: AsyncSession, *, user_id: str, project_id: str,
) -> MemoirShareVideoCandidateResponse | None:
    await load_memoir_project(db, project_id, user_id)
    candidate = await db.scalar(
        select(MemoirShareVideoCandidate)
        .where(MemoirShareVideoCandidate.project_id == project_id)
        .order_by(MemoirShareVideoCandidate.created_at.desc(), MemoirShareVideoCandidate.id.desc())
        .limit(1)
    )
    if (
        candidate is None
        or candidate.created_at < utcnow_naive() - CANDIDATE_AGE_LIMIT
        or candidate.status not in {
            MemoirShareVideoCandidateStatus.READY.value,
            MemoirShareVideoCandidateStatus.ACCEPTED.value,
        }
    ):
        return None
    if not _candidate_current(candidate, await _load_current_utterance(db, candidate)):
        candidate.status = MemoirShareVideoCandidateStatus.STALE.value
        await db.commit()
        return None
    return _response(candidate)


async def dismiss_share_video_candidate(
    db: AsyncSession, *, user_id: str, project_id: str, candidate_id: str,
) -> MemoirShareVideoCandidateResponse:
    candidate = await _owned_candidate(db, user_id=user_id, project_id=project_id, candidate_id=candidate_id)
    candidate.status = MemoirShareVideoCandidateStatus.DISMISSED.value
    await db.commit()
    return _response(candidate)


async def create_candidate_share_video(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    candidate_id: str,
    req: MemoirShareVideoCandidateCreateVideoRequest,
) -> MemoirShareVideoJobResponse:
    candidate = await _owned_candidate(db, user_id=user_id, project_id=project_id, candidate_id=candidate_id)
    if candidate.status not in {
        MemoirShareVideoCandidateStatus.READY.value,
        MemoirShareVideoCandidateStatus.ACCEPTED.value,
    } or candidate.created_at < utcnow_naive() - CANDIDATE_AGE_LIMIT:
        raise HTTPException(status_code=409, detail="分享片段已失效")
    utterance = await _load_current_utterance(db, candidate, for_update=True)
    if not _candidate_current(candidate, utterance):
        candidate.status = MemoirShareVideoCandidateStatus.STALE.value
        await db.commit()
        raise HTTPException(status_code=409, detail="讲述内容已更新，请重新选择分享片段")
    job = await create_share_video_job(
        db,
        candidate.utterance_id,
        user_id,
        MemoirShareVideoJobCreateRequest(
            start_char=candidate.start_char,
            end_char=candidate.end_char,
            ai_model=req.ai_model,
            video_style=req.video_style,
            background_source=req.background_source,
            background_style=req.background_style,
            background_mode=req.background_mode,
            custom_background_asset_id=req.custom_background_asset_id,
        ),
    )
    candidate.status = MemoirShareVideoCandidateStatus.ACCEPTED.value
    candidate.video_job_id = job.id
    await db.commit()
    return job
