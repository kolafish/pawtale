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
    MemoirMaterialShareVideo,
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
from app.services.memoir.share_video_rendering import usable_hook_text
from app.services.memoir.transcript_projection import current_transcript_version_id


logger = logging.getLogger(__name__)
ALGORITHM_VERSION = "share-candidate-v2"
MIN_CANDIDATE_MS = 15_000
MAX_CANDIDATE_MS = 45_000
MIN_ALIGNMENT_CONFIDENCE = 0.72
# Keeps one session's numbered sentences within a single model request.
MAX_MODEL_SENTENCES = 400
# A range is recommended only if every score reaches MIN_SCORE and their sum MIN_TOTAL_SCORE.
MIN_SCORE = 3
MIN_TOTAL_SCORE = 11
MAX_REASON_CHARS = 40
CANDIDATE_AGE_LIMIT = timedelta(days=7)
_SENSITIVE_NUMBER = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)")


@dataclass(frozen=True)
class CandidateSentence:
    id: int
    narration: int
    utterance_id: str
    audio_file_id: str
    transcript_version_id: str
    text_hash: str
    transcript_text: str
    start_char: int
    end_char: int
    start_ms: int
    end_ms: int

    @property
    def text(self) -> str:
        return self.transcript_text[self.start_char:self.end_char]


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
    emotion_score: int
    visual_score: int
    standalone_score: int
    reason: str
    hook_text: str

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def recommended(self) -> bool:
        scores = (self.emotion_score, self.visual_score, self.standalone_score)
        return min(scores) >= MIN_SCORE and sum(scores) >= MIN_TOTAL_SCORE


class _CandidatePick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_sentence_id: int | None
    end_sentence_id: int | None = None
    hook_sentence_id: int | None = None
    emotion: int = 0
    visual: int = 0
    standalone: int = 0
    reason: str = ""


def candidate_sentences_for_utterance(
    utterance: MemoirVoiceUtterance, *, narration: int, first_id: int,
) -> list[CandidateSentence]:
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
    sentences: list[CandidateSentence] = []
    for segment in alignment.segments:
        start_char, end_char = int(segment["start_char"]), int(segment["end_char"])
        start_ms, end_ms = share_video_char_range_ms(alignment, start_char, end_char)
        sentences.append(CandidateSentence(
            id=first_id + len(sentences),
            narration=narration,
            utterance_id=utterance.id,
            audio_file_id=utterance.audio_file_id,
            transcript_version_id=transcript_version_id,
            text_hash=alignment.text_hash,
            transcript_text=alignment.transcript_text,
            start_char=start_char,
            end_char=end_char,
            start_ms=start_ms,
            end_ms=end_ms,
        ))
    # Too short to hold any clip, so the model need not read it.
    if not sentences or sentences[-1].end_ms - sentences[0].start_ms < MIN_CANDIDATE_MS:
        return []
    return sentences


def session_candidate_sentences(utterances: list[MemoirVoiceUtterance]) -> list[CandidateSentence]:
    sentences: list[CandidateSentence] = []
    for narration, utterance in enumerate(sorted(utterances, key=lambda item: (item.created_at, item.id)), start=1):
        found = candidate_sentences_for_utterance(utterance, narration=narration, first_id=len(sentences) + 1)
        if len(sentences) + len(found) > MAX_MODEL_SENTENCES:
            break
        sentences.extend(found)
    return sentences


def _clamp_score(value: int) -> int:
    return max(1, min(5, int(value)))


def window_from_pick(sentences: list[CandidateSentence], pick: _CandidatePick) -> tuple[CandidateWindow | None, str]:
    """Check the model's range against the hard limits; the reason code says why a pick is unusable."""
    if pick.start_sentence_id is None:
        return None, "model_declined"
    by_id = {sentence.id: sentence for sentence in sentences}
    first = by_id.get(pick.start_sentence_id)
    last = by_id.get(pick.end_sentence_id if pick.end_sentence_id is not None else pick.start_sentence_id)
    if first is None or last is None or first.utterance_id != last.utterance_id or first.id > last.id:
        return None, "invalid_range"
    start_char, end_char = first.start_char, last.end_char
    start_ms, end_ms = first.start_ms, last.end_ms
    selected_text = first.transcript_text[start_char:end_char]
    visible_chars = sum(not char.isspace() for char in selected_text)
    if (
        not MIN_CANDIDATE_MS <= end_ms - start_ms <= MAX_CANDIDATE_MS
        or not 26 <= visible_chars <= 220
        or _SENSITIVE_NUMBER.search(selected_text)
    ):
        return None, "out_of_bounds"
    hook = by_id.get(pick.hook_sentence_id) if pick.hook_sentence_id is not None else None
    hook_text = usable_hook_text(hook.text, selected_text) if hook and first.id <= hook.id <= last.id else ""
    window = CandidateWindow(
        utterance_id=first.utterance_id,
        audio_file_id=first.audio_file_id,
        transcript_version_id=first.transcript_version_id,
        text_hash=first.text_hash,
        selected_text=selected_text,
        start_char=start_char,
        end_char=end_char,
        start_ms=start_ms,
        end_ms=end_ms,
        emotion_score=_clamp_score(pick.emotion),
        visual_score=_clamp_score(pick.visual),
        standalone_score=_clamp_score(pick.standalone),
        reason=" ".join(pick.reason.split())[:MAX_REASON_CHARS],
        hook_text=hook_text,
    )
    return window, "model_selected" if window.recommended else "low_score"


async def _pick_window(
    sentences: list[CandidateSentence], *, ai_model: str | None,
) -> tuple[CandidateWindow | None, str]:
    policy = ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)
    narrations: dict[int, list[dict[str, object]]] = {}
    for sentence in sentences:
        narrations.setdefault(sentence.narration, []).append({
            "id": sentence.id,
            "seconds": round((sentence.end_ms - sentence.start_ms) / 1000, 1),
            "text": sentence.text,
        })
    prompt = json.dumps(
        {"narrations": [{"sentences": items} for items in narrations.values()]},
        ensure_ascii=False,
    )
    generated = await generate_structured_with_client(
        get_ai_service(ai_model),
        "你在为家庭回忆录挑一段讲述原声，做成 15 到 45 秒的竖屏短视频发给家人看。"
        "输入是这次讲述按句编号的原话，seconds 是每句的时长。\n"
        "挑出最值得做成视频的一段连续句子：\n"
        "- start_sentence_id 和 end_sentence_id 必须在同一个 narration 里，前者不大于后者，合计 15 到 45 秒；\n"
        "- 从一个完整的意思开始，在故事的落点结束；不要从“后来他就……”这类依赖上文的句子开始；\n"
        "- hook_sentence_id 是这段里最打动人、单独放在片头也成立的一句，必须在起止范围内；\n"
        "- emotion（情绪或转折）、visual（画面感，能否画出具体场景）、standalone（不看上下文也能懂）各打 1 到 5 分，"
        "照实打分，不要为了推荐而抬高；\n"
        "- reason 用一句不超过 30 字的话告诉讲述者这段讲了什么、为什么值得做成视频，"
        "例如“讲了父亲没回来的那个中秋”，不要评价讲述者本人。\n"
        "如果都是寒暄、采访提问、重复，没有具体故事，或含手机号、证件号等私密信息，"
        "start_sentence_id 填 null。宁可不推荐，也不要凑数。\n"
        "只返回一个 JSON 对象，字段为 start_sentence_id、end_sentence_id、hook_sentence_id、"
        "emotion、visual、standalone、reason。不要返回 Markdown 或解释。",
        prompt,
        output_model=_CandidatePick,
        requested_model=ai_model,
        temperature=0.1,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    return window_from_pick(sentences, generated.value)


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
        reason=candidate.reason,
        hook_text=candidate.hook_text,
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

    sentences = session_candidate_sentences(session.utterances)
    selected: CandidateWindow | None = None
    reason_code = "no_eligible_window"
    if sentences:
        # Release read locks before the external model call. The selected source is
        # checked again against the current audio and transcript before storage.
        await db.commit()
        ai_model = await load_project_ai_model(db, job.project_id)
        selected, reason_code = await _pick_window(sentences, ai_model=ai_model)

    if selected is not None and selected.recommended:
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
    candidate.reason_code = reason_code
    if selected is None:
        candidate.status = MemoirShareVideoCandidateStatus.NONE.value
        candidate.selected_text = ""
    else:
        # Ranges below the threshold are kept with their scores for later tuning.
        candidate.utterance_id = selected.utterance_id
        candidate.audio_file_id = selected.audio_file_id
        candidate.transcript_version_id = selected.transcript_version_id
        candidate.text_hash = selected.text_hash
        candidate.selected_text = selected.selected_text
        candidate.start_char = selected.start_char
        candidate.end_char = selected.end_char
        candidate.start_ms = selected.start_ms
        candidate.end_ms = selected.end_ms
        candidate.emotion_score = selected.emotion_score
        candidate.visual_score = selected.visual_score
        candidate.standalone_score = selected.standalone_score
        candidate.reason = selected.reason
        candidate.hook_text = selected.hook_text
        candidate.status = (
            MemoirShareVideoCandidateStatus.READY.value
            if selected.recommended
            else MemoirShareVideoCandidateStatus.NONE.value
        )
    candidate.algorithm_version = ALGORITHM_VERSION
    db.add(candidate)
    await db.flush()
    logger.info(
        "share_video_candidate_evaluated project_id=%s session_id=%s status=%s reason=%s sentences=%s",
        job.project_id, session_id, candidate.status, candidate.reason_code, len(sentences),
    )
    return EditingJobResult(result_json={
        "candidate_id": candidate.id,
        "status": candidate.status,
        "reason_code": candidate.reason_code,
        "sentences_considered": len(sentences),
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
    # Remakes use the transcript endpoint and retain the original job. Resolve
    # the latest version of this exact source range instead of its first job.
    latest_job_id = await db.scalar(
        select(MemoirMaterialShareVideo.id)
        .where(
            MemoirMaterialShareVideo.project_id == candidate.project_id,
            MemoirMaterialShareVideo.utterance_id == candidate.utterance_id,
            MemoirMaterialShareVideo.audio_file_id == candidate.audio_file_id,
            MemoirMaterialShareVideo.text_hash == candidate.text_hash,
            MemoirMaterialShareVideo.start_char == candidate.start_char,
            MemoirMaterialShareVideo.end_char == candidate.end_char,
            MemoirMaterialShareVideo.start_ms == candidate.start_ms,
            MemoirMaterialShareVideo.end_ms == candidate.end_ms,
        )
        .order_by(MemoirMaterialShareVideo.created_at.desc(), MemoirMaterialShareVideo.id.desc())
        .limit(1)
    )
    response = _response(candidate)
    if latest_job_id:
        response.status = MemoirShareVideoCandidateStatus.ACCEPTED
        response.video_job_id = latest_job_id
    return response


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
            photo_asset_ids=req.photo_asset_ids,
            hook_text=candidate.hook_text if req.hook_text is None else req.hook_text,
            background_note=req.background_note,
        ),
    )
    candidate.status = MemoirShareVideoCandidateStatus.ACCEPTED.value
    candidate.video_job_id = job.id
    await db.commit()
    return job
