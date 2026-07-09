"""Reprocess memoir voice utterances with offline multi-provider ASR.

Default mode is dry-run and does not call ASR or LLM providers.

Usage:
  PYTHONPATH=backend python backend/scripts/reprocess_memoir_offline_asr.py \
    --project-title 余仲尤的往事 --cutoff-created-at-bj "2026-07-08 16:02:59"

  PYTHONPATH=backend python backend/scripts/reprocess_memoir_offline_asr.py \
    --project-id <project_id> --cutoff-created-at-bj "2026-07-08 16:02:59" \
    --providers volc,stepfun --execute
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

import aiofiles
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session_maker, engine, init_db
from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.domain.projects.types import ProjectType
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.models.memoir import MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.asr import ASRAudioFormat, StepFunASRService
from app.services.memoir import source_cards
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.audio_files import audio_file_path
from app.services.memoir.book_auto_revision import schedule_chapter_auto_revisions
from app.services.memoir.source_disposition import INTERVIEWER_SOURCE_MATERIAL_TYPE, clear_source_route_disposition
from app.services.memoir.streaming_asr import create_volc_streaming_asr_client
from app.services.memoir.utterance_editing_pipeline import schedule_source_routing_for_ready_utterance
from app.services.memoir.voice_transcript_recovery import (
    ASR_RECOVERY_PCM_BYTES_PER_SAMPLE,
    ASR_RECOVERY_PCM_SAMPLE_RATE,
    ASR_RECOVERY_PCM_SEGMENT_BYTES,
    transcribe_audio_path_for_recovery,
)
from app.services.memoir.voice_transcripts import _sync_included_transcript_edit, prune_transcript_versions_for_utterance


BEIJING_UTC_OFFSET = timedelta(hours=8)
VOLC_REPROCESS_PROVIDER = "volc_streaming_asr_reprocess"
STEPFUN_REPROCESS_PROVIDER = "stepfun_asr_reprocess"
MULTI_ASR_REPROCESS_SOURCE = "offline_multi_asr_reprocess"
DEFAULT_PROVIDERS = ("volc", "stepfun")
STEPFUN_HOTWORDS = [
    "余仲尤",
    "四川",
    "新都",
    "新繁",
    "桂湖",
    "桂林",
    "华西",
    "李桂芳",
    "水晶厂",
    "武装队长",
    "县委",
    "人大",
]
SYSTEM_PROMPT = "你是严谨的中文口述史转写校对编辑。"


@dataclass(frozen=True)
class OfflineASRResult:
    provider: str
    text: str
    raw: dict[str, Any] | None = None


ASRRunner = Callable[[AsyncSession, MemoirVoiceUtterance, Sequence[str]], Awaitable[list[OfflineASRResult]]]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--project-id", help="Memoir project id.")
    identity.add_argument("--project-title", help="Exact memoir project title.")
    parser.add_argument(
        "--cutoff-created-at-bj",
        required=True,
        help="Only process utterances created at or before this Beijing time, e.g. 2026-07-08 16:02:59.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Call providers and write transcript updates.")
    mode.add_argument("--dry-run", action="store_true", help="Report candidates only. This is the default.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of candidate utterances.")
    parser.add_argument("--ai-model", help="Optional AI model override for dialect correction.")
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDERS),
        help="Comma-separated ASR providers to use: volc,stepfun.",
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Reprocess utterances even when requested reprocess ASR versions already exist.",
    )
    return parser.parse_args()


def parse_beijing_datetime(value: str) -> datetime:
    normalized = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError("cutoff time must use YYYY-MM-DD HH:MM[:SS]")


def beijing_to_utc_naive(value: datetime) -> datetime:
    return value - BEIJING_UTC_OFFSET


def utc_naive_to_beijing_text(value: datetime | None) -> str:
    if value is None:
        return ""
    return (value + BEIJING_UTC_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_providers(value: Sequence[str] | str) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else list(value)
    providers = tuple(item.strip().lower() for item in raw if item.strip())
    unsupported = sorted(set(providers) - {"volc", "stepfun"})
    if unsupported:
        raise ValueError(f"unsupported providers: {', '.join(unsupported)}")
    return providers or DEFAULT_PROVIDERS


def _reprocess_provider_name(provider: str) -> str:
    if provider == "volc":
        return VOLC_REPROCESS_PROVIDER
    if provider == "stepfun":
        return STEPFUN_REPROCESS_PROVIDER
    raise ValueError(f"unsupported provider: {provider}")


async def resolve_project_id(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    project_title: str | None = None,
) -> str:
    if project_id:
        project = await db.scalar(
            select(Project).where(Project.id == project_id, Project.type == ProjectType.MEMOIR.value)
        )
        if project is None:
            raise ValueError(f"memoir project not found: {project_id}")
        return project.id

    title = (project_title or "").strip()
    projects = (
        await db.execute(
            select(Project).where(Project.title == title, Project.type == ProjectType.MEMOIR.value)
        )
    ).scalars().all()
    if not projects:
        raise ValueError(f"memoir project title not found: {title}")
    if len(projects) > 1:
        ids = ", ".join(project.id for project in projects)
        raise ValueError(f"memoir project title is ambiguous: {title}; ids: {ids}")
    return projects[0].id


async def candidate_utterances(
    db: AsyncSession,
    *,
    project_id: str,
    cutoff_created_at_utc: datetime,
    providers: Sequence[str] = DEFAULT_PROVIDERS,
    force_reprocess: bool = False,
    limit: int | None = None,
) -> list[MemoirVoiceUtterance]:
    user_edited_exists = (
        select(MemoirTranscriptVersion.id)
        .where(
            MemoirTranscriptVersion.source_artifact_id == MemoirVoiceUtterance.id,
            MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
        )
        .exists()
    )
    statement = (
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(
            Project.id == project_id,
            Project.type == ProjectType.MEMOIR.value,
            MemoirVoiceUtterance.created_at <= cutoff_created_at_utc,
            MemoirVoiceUtterance.audio_file_id.is_not(None),
            MemoirVoiceUtterance.source_material_type != INTERVIEWER_SOURCE_MATERIAL_TYPE,
            ~user_edited_exists,
        )
        .options(
            selectinload(MemoirVoiceUtterance.audio_file),
            selectinload(MemoirVoiceUtterance.chat_message),
            selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.messages),
        )
        .order_by(MemoirVoiceUtterance.created_at.desc(), MemoirVoiceUtterance.id.desc())
    )
    rows = list((await db.execute(statement)).scalars().all())
    if force_reprocess:
        return rows[: max(0, limit)] if limit is not None else rows

    requested_reprocess_providers = tuple(_reprocess_provider_name(provider) for provider in providers)
    candidates: list[MemoirVoiceUtterance] = []
    max_candidates = None if limit is None else max(0, limit)
    for utterance in rows:
        if max_candidates is not None and len(candidates) >= max_candidates:
            break
        if await _has_requested_reprocess_versions(
            db,
            utterance_id=utterance.id,
            providers=requested_reprocess_providers,
        ):
            continue
        candidates.append(utterance)
    return candidates


async def load_reprocess_utterance(db: AsyncSession, utterance_id: str) -> MemoirVoiceUtterance | None:
    return await db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(
            selectinload(MemoirVoiceUtterance.audio_file),
            selectinload(MemoirVoiceUtterance.chat_message),
            selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.messages),
        )
    )


async def _has_requested_reprocess_versions(
    db: AsyncSession,
    *,
    utterance_id: str,
    providers: Sequence[str],
) -> bool:
    if not providers:
        return False
    existing = set(
        (
            await db.execute(
                select(MemoirTranscriptVersion.provider).where(
                    MemoirTranscriptVersion.source_artifact_id == utterance_id,
                    MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.ASR_RAW.value,
                    MemoirTranscriptVersion.provider.in_(providers),
                )
            )
        ).scalars().all()
    )
    return all(provider in existing for provider in providers)


def _current_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()


def _candidate_payload(utterance: MemoirVoiceUtterance) -> dict[str, object]:
    audio_file = utterance.audio_file
    return {
        "utterance_id": utterance.id,
        "voice_session_id": utterance.voice_session_id,
        "created_at_bj": utc_naive_to_beijing_text(utterance.created_at),
        "audio_file_id": utterance.audio_file_id,
        "audio_mime_type": audio_file.mime_type if audio_file else None,
        "audio_size_bytes": audio_file.size_bytes if audio_file else None,
        "estimated_seconds": round((audio_file.size_bytes if audio_file else 0) / 32000, 1),
        "current_text_length": len(_current_text(utterance)),
        "is_currently_blank": not bool(_current_text(utterance)),
    }


async def _transcribe_with_volc(utterance: MemoirVoiceUtterance, path: Path) -> OfflineASRResult:
    event = await transcribe_audio_path_for_recovery(
        path,
        asr_client_factory=lambda: create_volc_streaming_asr_client(
            utterance.audio_file.user_id,
            utterance.voice_session_id,
        ),
    )
    return OfflineASRResult(
        provider=VOLC_REPROCESS_PROVIDER,
        text=normalize_memoir_asr_text((event.text or "").strip()),
        raw={
            "provider": "volc_streaming_asr",
            "start_ms": event.start_ms,
            "end_ms": event.end_ms,
            "provider_payload": event.raw,
        },
    )


def _stepfun_audio_format(utterance: MemoirVoiceUtterance) -> ASRAudioFormat:
    audio_file = utterance.audio_file
    mime_type = (audio_file.mime_type if audio_file else "").lower()
    if mime_type in {"audio/pcm", "audio/x-pcm"}:
        return ASRAudioFormat(
            type="pcm",
            codec="pcm_s16le",
            rate=ASR_RECOVERY_PCM_SAMPLE_RATE,
            bits=ASR_RECOVERY_PCM_BYTES_PER_SAMPLE * 8,
            channel=1,
        )
    if mime_type in {"audio/mpeg", "audio/mp3"}:
        return ASRAudioFormat(type="mp3")
    if mime_type in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return ASRAudioFormat(type="wav")
    if mime_type == "audio/ogg":
        return ASRAudioFormat(type="ogg")
    raise ValueError(f"unsupported StepFun ASR audio mime type: {mime_type or 'unknown'}")


async def _read_audio_segment(path: Path, *, start_byte: int, byte_count: int) -> bytes:
    async with aiofiles.open(path, "rb") as file:
        await file.seek(start_byte)
        return await file.read(byte_count)


async def _transcribe_with_stepfun(utterance: MemoirVoiceUtterance, path: Path) -> OfflineASRResult:
    audio_format = _stepfun_audio_format(utterance)
    service = StepFunASRService()
    size_bytes = path.stat().st_size
    if audio_format.type != "pcm" or size_bytes <= ASR_RECOVERY_PCM_SEGMENT_BYTES:
        audio_bytes = await _read_audio_segment(path, start_byte=0, byte_count=size_bytes)
        result = await service.transcribe(audio_bytes, audio_format, hotwords=STEPFUN_HOTWORDS)
        return OfflineASRResult(
            provider=STEPFUN_REPROCESS_PROVIDER,
            text=normalize_memoir_asr_text(result.text.strip()),
            raw={"provider": "stepfun", "model": service.model, "usage": result.usage, "segment_count": 1},
        )

    texts: list[str] = []
    usages: list[dict[str, Any] | None] = []
    start_byte = 0
    segment_index = 0
    while start_byte < size_bytes:
        byte_count = min(ASR_RECOVERY_PCM_SEGMENT_BYTES, size_bytes - start_byte)
        if byte_count % ASR_RECOVERY_PCM_BYTES_PER_SAMPLE:
            byte_count -= byte_count % ASR_RECOVERY_PCM_BYTES_PER_SAMPLE
        if byte_count <= 0:
            break
        segment_index += 1
        audio_bytes = await _read_audio_segment(path, start_byte=start_byte, byte_count=byte_count)
        result = await service.transcribe(audio_bytes, audio_format, hotwords=STEPFUN_HOTWORDS)
        text = normalize_memoir_asr_text(result.text.strip())
        if text:
            texts.append(text)
        usages.append(result.usage)
        start_byte += byte_count

    return OfflineASRResult(
        provider=STEPFUN_REPROCESS_PROVIDER,
        text="\n".join(texts).strip(),
        raw={"provider": "stepfun", "model": service.model, "usage_by_segment": usages, "segment_count": segment_index},
    )


async def default_asr_runner(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    providers: Sequence[str],
) -> list[OfflineASRResult]:
    del db
    path = audio_file_path(utterance.audio_file)
    if not path.exists():
        raise FileNotFoundError(f"audio file not found: {path}")
    results: list[OfflineASRResult] = []
    for provider in providers:
        if provider == "volc":
            results.append(await _transcribe_with_volc(utterance, path))
        elif provider == "stepfun":
            results.append(await _transcribe_with_stepfun(utterance, path))
    return [result for result in results if result.text.strip()]


async def _latest_question_context(db: AsyncSession, utterance: MemoirVoiceUtterance) -> str:
    message = utterance.chat_message
    if message is not None and message.turn_index is not None:
        text = await db.scalar(
            select(MemoirChatMessage.content_text)
            .where(
                MemoirChatMessage.voice_session_id == utterance.voice_session_id,
                MemoirChatMessage.turn_index < message.turn_index,
                MemoirChatMessage.role.in_([
                    MemoirChatMessageRole.ASSISTANT.value,
                    MemoirChatMessageRole.INTERVIEWER.value,
                ]),
                func.trim(MemoirChatMessage.content_text) != "",
            )
            .order_by(MemoirChatMessage.turn_index.desc(), MemoirChatMessage.created_at.desc())
            .limit(1)
        )
        if text:
            return text.strip()

    text = await db.scalar(
        select(MemoirVoiceUtterance.transcript_raw)
        .where(
            MemoirVoiceUtterance.voice_session_id == utterance.voice_session_id,
            MemoirVoiceUtterance.created_at < utterance.created_at,
            MemoirVoiceUtterance.source_material_type == INTERVIEWER_SOURCE_MATERIAL_TYPE,
            func.trim(MemoirVoiceUtterance.transcript_raw) != "",
        )
        .order_by(MemoirVoiceUtterance.created_at.desc())
        .limit(1)
    )
    return (text or "").strip()


def _asr_results_block(results: Sequence[OfflineASRResult]) -> str:
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"ASR {index} provider={result.provider}：")
        lines.append(result.text.strip() or "无")
    return "\n".join(lines)


def _build_multi_asr_cleanup_prompt(
    utterance: MemoirVoiceUtterance,
    *,
    question_context: str,
    asr_results: Sequence[OfflineASRResult],
) -> str:
    original_raw = (utterance.transcript_raw or "").strip() or "无"
    original_edited = (utterance.transcript_edited or "").strip() or "无"
    question = question_context.strip() or "无"
    return f"""下面是一位老年人的中文口述录音转写结果。说话人带明显四川话口音，语速慢、口头语多，可能夹杂家人插话和现场噪音。

请综合多个 ASR 模型结果，对这段口述做忠于原音频的校对。

要求：
- 只根据现有 ASR 文本互相印证，修正方言、口音、同音词、断句和明显误识别；无法确认时保留更可信的原表达。
- 这是老年四川话口音，常见词包括“啥子、咋个、没得、晓得、屋头、婆婆、幺儿、得嘛、噻、喃、耍、不得”。
- 如果某一路 ASR 出现明显无关英文、广告词、歌名或乱码，要结合其他结果判断，不要照抄。
- 可以去掉明显无意义的口头填充词和连续重复，比如“嗯、啊、那个那个、就是就是”。
- 保留人物、地名、时间、亲属称呼、单位和事件细节；不要新增事实，不要总结，不要文学化。
- 如果采访者的追问或家人插话被录进去，只有在它帮助理解回答时才用括号轻标；不要把采访问题当成讲述者正文扩写。
- 直接输出校对后的正文，不要标题、列表、解释或引号。

采访问题或近邻上下文：
{question}

当前系统里原有实时 ASR：
{original_raw}

当前系统里原有自动整理：
{original_edited}

多模型离线 ASR：
{_asr_results_block(asr_results)}
"""


def _strip_llm_wrapping(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] in {"“", "「", '"', "'"} and cleaned[-1] in {"”", "」", '"', "'"}:
        return cleaned[1:-1].strip()
    return cleaned


async def generate_multi_asr_corrected_text(
    utterance: MemoirVoiceUtterance,
    *,
    question_context: str,
    asr_results: Sequence[OfflineASRResult],
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> tuple[str, str]:
    client = ai_client or get_ai_service(ai_model)
    content = await client._call_llm(
        SYSTEM_PROMPT,
        _build_multi_asr_cleanup_prompt(
            utterance,
            question_context=question_context,
            asr_results=asr_results,
        ),
        temperature=0.1,
        max_tokens=1800,
        timeout=120.0,
    )
    text = normalize_memoir_asr_text(_strip_llm_wrapping(str(content))).strip()
    if not text:
        raise ValueError("LLM returned empty corrected transcript")
    return text, client.provider_name


def _primary_asr_result(results: Sequence[OfflineASRResult]) -> OfflineASRResult:
    for provider in (VOLC_REPROCESS_PROVIDER, STEPFUN_REPROCESS_PROVIDER):
        for result in results:
            if result.provider == provider and result.text.strip():
                return result
    for result in results:
        if result.text.strip():
            return result
    raise ValueError("no non-empty ASR result")


async def _replace_reprocess_asr_versions(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    results: Sequence[OfflineASRResult],
) -> dict[str, str]:
    providers = [result.provider for result in results]
    if providers:
        await db.execute(
            delete(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.source_artifact_id == utterance.id,
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.ASR_RAW.value,
                MemoirTranscriptVersion.provider.in_(providers),
            )
        )
    version_ids: dict[str, str] = {}
    for result in results:
        version = MemoirTranscriptVersion(
            voice_session_id=utterance.voice_session_id,
            source_artifact_id=utterance.id,
            version_type=MemoirTranscriptVersionType.ASR_RAW.value,
            content=result.text.strip(),
            provider=result.provider,
            created_by=MemoirCreatedBy.ASR.value,
        )
        db.add(version)
        await db.flush()
        version_ids[result.provider] = version.id
    return version_ids


async def _next_turn_index(db: AsyncSession, voice_session_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirChatMessage.turn_index)).where(MemoirChatMessage.voice_session_id == voice_session_id)
    )
    return int(value or 0) + 1


async def _ensure_user_chat_message(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    *,
    raw_text: str,
    raw_transcript_version_id: str | None,
) -> None:
    if utterance.chat_message is not None:
        return
    voice_session = utterance.voice_session
    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.USER.value,
        content_text=raw_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        audio_file_id=utterance.audio_file_id,
        transcript_version_id=raw_transcript_version_id,
        turn_index=await _next_turn_index(db, voice_session.id),
        metadata_json={
            "source": MULTI_ASR_REPROCESS_SOURCE,
            "speaker_role": "narrator",
            "auto_corrected": False,
        },
    )
    db.add(message)
    await db.flush()
    utterance.chat_message_id = message.id
    utterance.chat_message = message


async def _apply_corrected_reprocess_text(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    *,
    content: str,
    provider: str,
    ai_model: str | None,
) -> None:
    cleaned_content = content.strip()
    if not cleaned_content:
        raise ValueError("dialect-corrected transcript is empty")

    version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED.value,
        content=cleaned_content,
        provider=provider,
        created_by=MemoirCreatedBy.AI.value,
    )
    db.add(version)
    await db.flush()

    utterance.transcript_edited = cleaned_content
    clear_source_route_disposition(utterance)
    if utterance.chat_message:
        utterance.chat_message.content_text = cleaned_content
        utterance.chat_message.content_status = MemoirChatContentStatus.CORRECTED.value
        utterance.chat_message.transcript_version_id = version.id

    await prune_transcript_versions_for_utterance(
        db,
        utterance_id=utterance.id,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
    )
    changed_chapter_ids = await _sync_included_transcript_edit(
        db,
        utterance=utterance,
        content=cleaned_content,
        transcript_version_id=version.id,
    )
    project_id = utterance.voice_session.project_id
    await source_cards.schedule_source_card_generation_job(
        db,
        project_id=project_id,
        utterance_id=utterance.id,
        ai_model=ai_model,
        reason=f"transcript_{MemoirTranscriptVersionType.DIALECT_CORRECTED.value}",
    )
    if changed_chapter_ids:
        await schedule_chapter_auto_revisions(
            db,
            project_id=project_id,
            chapter_ids=changed_chapter_ids,
            ai_model=ai_model,
        )
    else:
        await schedule_source_routing_for_ready_utterance(
            db,
            project_id=project_id,
            utterance=utterance,
            source=f"transcript_{MemoirTranscriptVersionType.DIALECT_CORRECTED.value}",
            ai_model=ai_model,
        )


def _compact_result_payload(results: Sequence[OfflineASRResult]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for result in results:
        raw = result.raw or {}
        payload.append({
            "provider": result.provider,
            "text_length": len(result.text),
            "raw_provider": raw.get("provider"),
            "segment_count": raw.get("segment_count"),
        })
    return payload


async def _apply_reprocessed_transcript(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    *,
    asr_results: Sequence[OfflineASRResult],
    corrected_text: str,
    corrected_provider: str,
    ai_model: str | None,
) -> None:
    primary = _primary_asr_result(asr_results)
    version_ids = await _replace_reprocess_asr_versions(db, utterance, asr_results)
    primary_version_id = version_ids.get(primary.provider)
    previous_text_blank = not bool(_current_text(utterance))

    utterance.transcript_raw = primary.text.strip()
    utterance.provider = primary.provider
    if previous_text_blank:
        utterance.provider_payload_json = {
            "source": MULTI_ASR_REPROCESS_SOURCE,
            "primary_provider": primary.provider,
            "providers": _compact_result_payload(asr_results),
        }
    await _ensure_user_chat_message(
        db,
        utterance,
        raw_text=primary.text.strip(),
        raw_transcript_version_id=primary_version_id,
    )
    await _apply_corrected_reprocess_text(
        db,
        utterance,
        content=corrected_text,
        provider=corrected_provider,
        ai_model=ai_model,
    )


async def run_offline_asr_reprocess(
    db: AsyncSession,
    *,
    project_id: str,
    cutoff_created_at_utc: datetime,
    dry_run: bool = True,
    limit: int | None = None,
    ai_model: str | None = None,
    providers: Sequence[str] | str = DEFAULT_PROVIDERS,
    force_reprocess: bool = False,
    asr_runner: ASRRunner | None = None,
    ai_client: AITextClient | None = None,
) -> dict[str, object]:
    normalized_providers = _normalize_providers(providers)
    candidates = await candidate_utterances(
        db,
        project_id=project_id,
        cutoff_created_at_utc=cutoff_created_at_utc,
        providers=normalized_providers,
        force_reprocess=force_reprocess,
        limit=limit,
    )
    candidate_ids = [utterance.id for utterance in candidates]
    candidate_payloads = [_candidate_payload(utterance) for utterance in candidates]
    result: dict[str, object] = {
        "dry_run": dry_run,
        "project_id": project_id,
        "cutoff_created_at_utc": cutoff_created_at_utc.isoformat(sep=" "),
        "cutoff_created_at_bj": utc_naive_to_beijing_text(cutoff_created_at_utc),
        "providers": list(normalized_providers),
        "force_reprocess": force_reprocess,
        "candidate_count": len(candidates),
        "processed_count": 0,
        "failed_count": 0,
        "failures": [],
        "candidates": candidate_payloads,
    }
    if dry_run:
        await db.rollback()
        db.expunge_all()
        return result

    runner = asr_runner or default_asr_runner
    failures: list[dict[str, str]] = []
    processed_count = 0
    for utterance_id in candidate_ids:
        try:
            utterance = await load_reprocess_utterance(db, utterance_id)
            if utterance is None:
                raise ValueError("candidate utterance no longer exists")
            asr_results = await runner(db, utterance, normalized_providers)
            if not asr_results:
                raise ValueError("all ASR providers returned empty text")
            question_context = await _latest_question_context(db, utterance)
            corrected_text, corrected_provider = await generate_multi_asr_corrected_text(
                utterance,
                question_context=question_context,
                asr_results=asr_results,
                ai_model=ai_model,
                ai_client=ai_client,
            )
            await _apply_reprocessed_transcript(
                db,
                utterance,
                asr_results=asr_results,
                corrected_text=corrected_text,
                corrected_provider=corrected_provider,
                ai_model=ai_model,
            )
            await db.commit()
            processed_count += 1
        except Exception as exc:
            await db.rollback()
            db.expunge_all()
            failures.append({"utterance_id": utterance_id, "error": str(exc)})

    result["processed_count"] = processed_count
    result["failed_count"] = len(failures)
    result["failures"] = failures
    return result


async def _main() -> None:
    args = _parse_args()
    cutoff_bj = parse_beijing_datetime(args.cutoff_created_at_bj)
    cutoff_utc = beijing_to_utc_naive(cutoff_bj)
    try:
        await init_db()
        await migrate_memoir_voice_schema(engine)
        await migrate_memoir_semantic_schema(engine)
        async with async_session_maker() as db:
            project_id = await resolve_project_id(
                db,
                project_id=args.project_id,
                project_title=args.project_title,
            )
            result = await run_offline_asr_reprocess(
                db,
                project_id=project_id,
                cutoff_created_at_utc=cutoff_utc,
                dry_run=not args.execute,
                limit=args.limit,
                ai_model=args.ai_model,
                providers=args.providers,
                force_reprocess=args.force_reprocess,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
