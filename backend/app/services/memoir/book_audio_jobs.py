from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import AudioFile, MemoirBookOutput, MemoirBookOutputVersion, MemoirEditingJob
from app.schemas.memoir_book import (
    MemoirAudioPlanRequest,
    MemoirBookAudioPlanRequest,
    MemoirBookAudioPlanResponse,
    MemoirBookAudioResponse,
    MemoirBookAudioSegmentResponse,
)
from app.services.memoir import book_audio
from app.services.memoir.access import load_memoir_project
from app.services.memoir.audio_files import audio_file_path
from app.services.memoir.editing_jobs import (
    EditingJobProgressLost,
    EditingJobResult,
    claim_editing_job,
    enqueue_editing_job,
    run_editing_job,
)
from app.services.memoir.voice_tts_types import TTSClient


@dataclass(frozen=True)
class AudioSource:
    version_id: str
    text: str
    legacy_metadata: dict
    legacy_cache_key: str


class AudioProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    provider: str
    model: str
    voice: str | None
    speed_ratio: float
    mime_type: str
    sample_rate: int | None


class AudioChunkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    user_id: str
    text: str
    profile: AudioProfile


class AudioChunkResult(BaseModel):
    schema_version: Literal[1] = 1
    provider_started: bool = False
    audio: MemoirBookAudioResponse | None = None


def audio_profile(client: TTSClient, voice: str | None, speed_ratio: float | None) -> AudioProfile:
    resolve_voice = getattr(client, "effective_voice", None)
    effective_voice = resolve_voice(voice) if resolve_voice else (voice or client.default_voice)
    output_format = getattr(client, "response_format", None)
    return AudioProfile(
        provider=client.provider_name,
        model=client.model_name,
        voice=effective_voice or None,
        speed_ratio=book_audio.effective_tts_speed_ratio(speed_ratio),
        mime_type=output_format.mime_type if output_format else "audio/mpeg",
        sample_rate=getattr(client, "sample_rate", None),
    )


def profile_key(profile: AudioProfile) -> str:
    return book_audio.source_text_hash(json.dumps(profile.model_dump(), sort_keys=True))


def chunk_key(project_id: str, user_id: str, profile: AudioProfile, text: str) -> str:
    digest = hashlib.sha256(json.dumps(
        [user_id, profile_key(profile), text], ensure_ascii=False,
    ).encode()).hexdigest()
    return f"book-audio:{project_id}:{digest}"


def split_book_audio_text(text: str) -> list[book_audio.TTSChunk]:
    """Paragraph boundaries keep an edit from shifting chunks in later paragraphs."""
    content = text.strip()
    if not content:
        raise HTTPException(400, "朗读内容为空")
    result: list[book_audio.TTSChunk] = []
    for paragraph in re.finditer(r"\S[\s\S]*?(?=\n[ \t]*\n|\Z)", content):
        for chunk in book_audio.split_tts_text(paragraph.group(), max_chars=180):
            result.append(book_audio.TTSChunk(
                text=chunk.text,
                text_start=paragraph.start() + chunk.text_start,
                text_end=paragraph.start() + chunk.text_end,
            ))
    return result


async def _usable_audio(
    db: AsyncSession, job: MemoirEditingJob | None, user_id: str,
    *, files: dict[str, AudioFile] | None = None,
) -> MemoirBookAudioResponse | None:
    if job is None or not (job.result_json or {}).get("audio"):
        return None
    result = AudioChunkResult.model_validate(job.result_json).audio
    if result is None or not result.audio_file_ids:
        return None
    if files is None:
        files = {f.id: f for f in (await db.scalars(select(AudioFile).where(
            AudioFile.id.in_(result.audio_file_ids), AudioFile.user_id == user_id,
        ))).all()}
    if any(file_id not in files or not audio_file_path(files[file_id]).is_file() for file_id in result.audio_file_ids):
        return None
    return result


async def _legacy_audio_plan(
    db: AsyncSession, *, project_id: str, user_id: str, source: AudioSource,
    profile: AudioProfile, req: MemoirAudioPlanRequest,
) -> MemoirBookAudioPlanResponse | None:
    """Keep an existing complete recording for identical narration text.

    Old caches did not record the resolved default voice/sample rate, so they
    cannot safely populate the cross-version chunk cache.
    """
    text_hash = book_audio.source_text_hash(source.text.strip())
    signature = book_audio.source_text_hash(f"legacy:{text_hash}:{profile_key(profile)}")
    key = f"book-audio-legacy:{project_id}:{signature}"
    job = await db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == key))
    cached = await _usable_audio(db, job, user_id)
    imported = await db.scalar(select(MemoirEditingJob.id).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.dedupe_key.like(f"book-audio-legacy:{project_id}:%"),
        MemoirEditingJob.payload_json["text"].as_string() == source.text.strip(),
    ).limit(1)) if job is None else None
    if job is None and imported is None:
        cached = book_audio.cached_audio_response(
            source.legacy_metadata, cache_key=source.legacy_cache_key,
            text_hash=text_hash, voice=req.voice, speed_ratio=profile.speed_ratio,
            force_regenerate=req.force_regenerate,
        )
        if cached and (cached.provider, cached.model, cached.mime_type) != (
            profile.provider, profile.model, profile.mime_type,
        ):
            cached = None
        if cached:
            job = await enqueue_editing_job(
                db, project_id=project_id, job_type=MemoirEditingJobType.GENERATE_BOOK_AUDIO_CHUNK,
                dedupe_key=key,
                payload_json=AudioChunkPayload(
                    version_id=source.version_id, user_id=user_id, text=source.text.strip(), profile=profile,
                ).model_dump(mode="json"),
            )
            job.status = MemoirEditingJobStatus.SUCCEEDED.value
            job.result_json = AudioChunkResult(audio=cached).model_dump(mode="json")
            await db.flush()
            cached = await _usable_audio(db, job, user_id)
    chunks = book_audio.split_tts_text(source.text)
    if cached is None or len(cached.audio_file_ids) != len(chunks):
        return None
    if req.start_index >= len(chunks):
        raise HTTPException(400, "朗读片段超出书稿范围")
    if req.profile_key and req.profile_key != signature:
        raise HTTPException(409, "音频清单已变化，请重新发起朗读")
    segments = []
    elapsed_ms = 0
    durations = cached.audio_file_durations_seconds
    for index, chunk in enumerate(chunks):
        duration = durations[index] if len(durations) == len(chunks) else None
        alignment = [a.model_copy(update={
            "start_ms": max(0, a.start_ms - elapsed_ms),
            "end_ms": max(0, a.end_ms - elapsed_ms),
            "text_start": a.text_start - chunk.text_start,
            "text_end": a.text_end - chunk.text_start,
        }) for a in cached.alignment if chunk.text_start <= a.text_start < chunk.text_end]
        segments.append(MemoirBookAudioSegmentResponse(
            index=index, text_start=chunk.text_start, text_end=chunk.text_end, status="succeeded",
            audio_file_id=cached.audio_file_ids[index], duration_seconds=duration, alignment=alignment,
        ))
        elapsed_ms += round((duration or 0) * 1000)
    await db.commit()
    return MemoirBookAudioPlanResponse(
        version_id=source.version_id, source_text_hash=text_hash, profile_key=signature,
        voice=profile.voice, speed_ratio=profile.speed_ratio, segments=segments,
        ready_count=len(segments), poll_after_ms=max(250, round(get_settings().book_audio_poll_seconds * 1000)),
    )


async def run_book_audio_chunk_job(db: AsyncSession, job: MemoirEditingJob) -> EditingJobResult:
    payload = AudioChunkPayload.model_validate(job.payload_json)
    cached = await _usable_audio(db, job, payload.user_id)
    if cached:
        return EditingJobResult(result_json=AudioChunkResult(audio=cached).model_dump(mode="json"))
    if AudioChunkResult.model_validate(job.result_json or {}).provider_started:
        raise HTTPException(503, "上次朗读生成被中断，请重新发起朗读后重试")
    client = book_audio.get_tts_client()
    if audio_profile(client, payload.profile.voice, payload.profile.speed_ratio) != payload.profile:
        raise HTTPException(409, "朗读配置已变化，请重新发起朗读")
    job_id, lease_token = job.id, job.lease_token
    leased = await db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.id == job_id,
        MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
        MemoirEditingJob.lease_token == lease_token,
    ).with_for_update())
    if leased is None:
        raise EditingJobProgressLost
    leased.result_json = AudioChunkResult(provider_started=True).model_dump(mode="json")
    await db.commit()  # Provider calls must not hold a database transaction.
    synthesis = await book_audio.synthesize_text_audio(
        db, user_id=payload.user_id, text=payload.text,
        voice=payload.profile.voice, speed_ratio=payload.profile.speed_ratio,
        tts_client=client,
    )
    result = AudioChunkResult(audio=book_audio.audio_response(synthesis)).model_dump(mode="json")
    try:
        leased = await db.scalar(select(MemoirEditingJob).where(
            MemoirEditingJob.id == job_id,
            MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            MemoirEditingJob.lease_token == lease_token,
        ).with_for_update())
        if leased is None:
            raise EditingJobProgressLost
        leased.result_json = result
        await db.commit()  # Audio and its cache index become visible together.
    except BaseException:
        await db.rollback()
        await book_audio.discard_staged_audio_files(synthesis.staged_audio)
        raise
    return EditingJobResult(result_json=result)


async def _book_audio_source(
    db: AsyncSession, *, user_id: str, project_id: str,
    req: MemoirBookAudioPlanRequest, allow_admin: bool = False,
) -> AudioSource:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    version = await db.scalar(select(MemoirBookOutputVersion).where(
        MemoirBookOutputVersion.project_id == project_id,
        MemoirBookOutputVersion.id == req.version_id,
    ))
    if version is None:
        raise HTTPException(404, "书稿版本不存在")
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    return AudioSource(version.id, version.content.strip(), output.metadata_json or {} if output else {}, "book_tts")


async def prepare_book_audio(
    db: AsyncSession, *, user_id: str, project_id: str,
    req: MemoirBookAudioPlanRequest, allow_admin: bool = False,
) -> MemoirBookAudioPlanResponse:
    source = await _book_audio_source(db, user_id=user_id, project_id=project_id, req=req, allow_admin=allow_admin)
    return await prepare_audio_plan(db, user_id=user_id, project_id=project_id, source=source, req=req, allow_admin=allow_admin)


async def prepare_audio_plan(
    db: AsyncSession, *, user_id: str, project_id: str, source: AudioSource,
    req: MemoirAudioPlanRequest, allow_admin: bool = False,
) -> MemoirBookAudioPlanResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    owner_id = project.user_id
    if owner_id is None:
        raise HTTPException(409, "书稿缺少所属用户")
    text = source.text.strip()
    if req.source_text_hash and req.source_text_hash != book_audio.source_text_hash(text):
        raise HTTPException(409, "朗读内容已变化，请重新发起朗读")
    profile = audio_profile(book_audio.get_tts_client(), req.voice, req.speed_ratio)
    signature = profile_key(profile)
    chunks = split_book_audio_text(text)
    keys = [chunk_key(project_id, owner_id, profile, chunk.text) for chunk in chunks]
    jobs = {job.dedupe_key: job for job in (await db.scalars(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key.in_(keys))
        .execution_options(populate_existing=True),
    )).all()}
    cached_ids = {
        file_id for job in jobs.values() if (job.result_json or {}).get("audio")
        for file_id in AudioChunkResult.model_validate(job.result_json).audio.audio_file_ids
    }
    files = {f.id: f for f in (await db.scalars(select(AudioFile).where(
        AudioFile.id.in_(cached_ids), AudioFile.user_id == owner_id,
    ))).all()} if cached_ids else {}
    cached_by_key = {key: await _usable_audio(db, job, owner_id, files=files) for key, job in jobs.items()}
    # Prefer a complete regenerated playlist for new requests. A playing legacy
    # playlist keeps its own signature and audio, even after regeneration.
    wants_legacy = req.profile_key is not None and req.profile_key != signature
    if not req.force_regenerate and (
        wants_legacy or (req.profile_key is None and not all(cached_by_key.get(key) for key in keys))
    ):
        legacy = await _legacy_audio_plan(
            db, project_id=project_id, user_id=owner_id, source=source, profile=profile, req=req,
        )
        if legacy:
            return legacy
    if req.profile_key and req.profile_key != signature:
        raise HTTPException(409, "朗读配置已变化，请重新发起朗读")
    if req.start_index >= len(chunks):
        raise HTTPException(400, "朗读片段超出书稿范围")
    segments: list[MemoirBookAudioSegmentResponse] = []
    settings = get_settings()
    window_end = req.start_index + max(1, min(3, settings.book_audio_prefetch_chunks))
    force = req.force_regenerate and req.profile_key is None
    for index, (chunk, key) in enumerate(zip(chunks, keys)):
        job = jobs.get(key)
        cached = None if force else cached_by_key.get(key)
        if cached is None and (force or req.start_index <= index < window_end):
            job = await enqueue_editing_job(
                db, project_id=project_id,
                job_type=MemoirEditingJobType.GENERATE_BOOK_AUDIO_CHUNK,
                dedupe_key=key,
                payload_json=AudioChunkPayload(
                    version_id=source.version_id, user_id=owner_id, text=chunk.text, profile=profile,
                ).model_dump(mode="json"),
                retry_failed=req.retry_failed,
                replace_completed=bool(job and job.status == MemoirEditingJobStatus.SUCCEEDED.value),
            )
            jobs[key] = job
        status = "succeeded" if cached else (job.status if job else "idle")
        if cached is None and status == MemoirEditingJobStatus.SUCCEEDED.value:
            status = "idle"
        if status == MemoirEditingJobStatus.SKIPPED.value:
            status = "failed"
        segments.append(MemoirBookAudioSegmentResponse(
            index=index, text_start=chunk.text_start, text_end=chunk.text_end,
            status=status, job_id=job.id if job else None,
            audio_file_id=cached.audio_file_ids[0] if cached else None,
            duration_seconds=cached.duration_seconds if cached else None,
            alignment=cached.alignment if cached else [],
            error_message=job.error_message if job and status == "failed" else "",
        ))
    await db.commit()
    return MemoirBookAudioPlanResponse(
        version_id=source.version_id, source_text_hash=book_audio.source_text_hash(text),
        profile_key=signature, voice=profile.voice, speed_ratio=profile.speed_ratio,
        segments=segments, ready_count=sum(s.status == "succeeded" for s in segments),
        poll_after_ms=max(250, round(settings.book_audio_poll_seconds * 1000)),
    )


async def complete_audio_plan(
    db: AsyncSession, *, user_id: str, project_id: str, source: AudioSource,
    req: MemoirAudioPlanRequest,
    allow_admin: bool = False,
) -> MemoirBookAudioResponse:
    """The shipped client still expects a complete response; share the same cache/jobs."""
    plan = await prepare_audio_plan(
        db, user_id=user_id, project_id=project_id, source=source, req=req,
        allow_admin=allow_admin,
    )
    for index in range(len(plan.segments)):
        current_req = req.model_copy(update={
            "start_index": index, "profile_key": plan.profile_key, "retry_failed": False,
        })
        if plan.segments[index].status == "idle":
            plan = await prepare_audio_plan(
                db, user_id=user_id, project_id=project_id, source=source, req=current_req,
                allow_admin=allow_admin,
            )
        while plan.segments[index].status != "succeeded":
            segment = plan.segments[index]
            if segment.status == "failed":
                raise HTTPException(503, segment.error_message or "朗读生成失败，请重试")
            if segment.job_id:
                lease = await claim_editing_job(db, segment.job_id)
                if lease:
                    await run_editing_job(db, lease)
                else:
                    await db.commit()
                    await asyncio.sleep(plan.poll_after_ms / 1000)
            plan = await prepare_audio_plan(
                db, user_id=user_id, project_id=project_id, source=source, req=current_req,
                allow_admin=allow_admin,
            )
    # Rebuild offsets from the current playlist; cached alignments remain chunk-local.
    alignment = []
    elapsed_ms = 0
    for segment in plan.segments:
        alignment.extend(item.model_copy(update={
            "start_ms": item.start_ms + elapsed_ms,
            "end_ms": item.end_ms + elapsed_ms,
            "text_start": item.text_start + segment.text_start,
            "text_end": item.text_end + segment.text_start,
        }) for item in segment.alignment)
        elapsed_ms += round((segment.duration_seconds or 0) * 1000)
    profile = audio_profile(book_audio.get_tts_client(), plan.voice, plan.speed_ratio)
    return MemoirBookAudioResponse(
        audio_file_ids=[s.audio_file_id for s in plan.segments if s.audio_file_id],
        audio_file_durations_seconds=[s.duration_seconds for s in plan.segments],
        provider=profile.provider, model=profile.model, mime_type=profile.mime_type,
        chunk_count=len(plan.segments), source_text_hash=plan.source_text_hash,
        duration_seconds=elapsed_ms / 1000 or None,
        alignment_provider=profile.provider if alignment else None, alignment=alignment,
    )


async def complete_book_audio(
    db: AsyncSession, *, user_id: str, project_id: str,
    req: MemoirBookAudioPlanRequest, allow_admin: bool = False,
) -> MemoirBookAudioResponse:
    source = await _book_audio_source(db, user_id=user_id, project_id=project_id, req=req, allow_admin=allow_admin)
    return await complete_audio_plan(db, user_id=user_id, project_id=project_id, source=source, req=req, allow_admin=allow_admin)
