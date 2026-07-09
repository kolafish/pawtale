from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
from pathlib import Path
import shutil
import tempfile

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.config import DATA_DIR, get_settings
from app.database import async_session_maker
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.models.memoir import MemoirMaterialShareVideo
from app.models.projects import Project
from app.schemas.memoir_voice import MemoirShareVideoJobCreateRequest, MemoirShareVideoJobResponse
from app.services.memoir.audio_files import audio_file_path
from app.services.memoir.share_video_backgrounds import (
    DEFAULT_SHARE_VIDEO_BACKGROUND_MODE,
    DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE,
    SHARE_VIDEO_BACKGROUND_MODE_SINGLE,
    SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI,
    ShareVideoBackgroundGenerationError,
    get_or_generate_share_video_background,
    normalize_share_video_background_mode,
    normalize_share_video_background_style,
)
from app.services.memoir.share_video_alignment import (
    MAX_SELECT_MS,
    MIN_SELECT_MS,
    build_share_video_alignment,
    share_video_char_range_ms,
)
from app.services.memoir.share_video_scene_planner import (
    ShareVideoScene,
    plan_share_video_background_scenes,
    scene_plan_to_metadata,
)
from app.services.memoir.voice_transcripts import _load_owned_utterance


MEMOIR_SHARE_VIDEO_DIR = DATA_DIR / "memoir" / "share-videos"
_VIDEO_MIME_TYPE = "video/mp4"
_VIDEO_WIDTH = 1280
_VIDEO_HEIGHT = 720
_BACKGROUND_COLOR = "0xf6f1e8"
_SHARE_VIDEO_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_SHARE_VIDEO_FONT_FILE = "NotoSerifSC-Regular.ttf"
_SHARE_VIDEO_FONT_NAME = "Memoir Noto Serif SC"
_ASS_INK_COLOR = "&H00403A2F"
_ASS_TEA_GOLD_COLOR = "&H003A90B9"
_ASS_MUTED_COLOR = "&H00919FA8"
_ASS_RULE_COLOR = "&H00BBCDD8"
_QUOTE_PAGE_MAX_CHARS = 36
_QUOTE_LINE_MAX_CHARS = 12
_QUOTE_PHRASE_MAX_CHARS = 3
_STALE_SHARE_VIDEO_JOB_SECONDS = 300
_STALE_SHARE_VIDEO_RECOVERY_BACKOFF_SECONDS = 300


@dataclass(frozen=True)
class ShareVideoBackgroundSegment:
    image_path: Path
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class ShareVideoRenderRequest:
    source_audio_path: Path
    source_audio_mime_type: str
    target_path: Path
    selected_text: str
    char_timings: list[dict[str, int]]
    start_ms: int
    end_ms: int
    style: str
    style_options: dict[str, object]
    background_image_path: Path | None = None
    background_segments: list[ShareVideoBackgroundSegment] | None = None


class ShareVideoGenerationError(RuntimeError):
    pass


def _set_job_metadata(job: MemoirMaterialShareVideo, metadata: dict[str, object]) -> None:
    job.metadata_json = dict(metadata)
    flag_modified(job, "metadata_json")


def _storage_path(user_id: str, job_id: str) -> str:
    return f"memoir/share-videos/{user_id}/{job_id}.mp4"


def share_video_file_path(job: MemoirMaterialShareVideo) -> Path:
    storage_path = Path(job.video_storage_path or _storage_path(job.user_id, job.id))
    try:
        relative_path = storage_path.relative_to(Path("memoir") / "share-videos")
    except ValueError:
        return DATA_DIR / storage_path
    return MEMOIR_SHARE_VIDEO_DIR / relative_path


def _trim_selection(text: str, start_char: int, end_char: int) -> tuple[int, int]:
    if start_char < 0 or end_char > len(text) or end_char <= start_char:
        raise HTTPException(status_code=400, detail="选择的文字范围无效")
    while start_char < end_char and text[start_char].isspace():
        start_char += 1
    while end_char > start_char and text[end_char - 1].isspace():
        end_char -= 1
    if end_char <= start_char:
        raise HTTPException(status_code=400, detail="请选择要分享的文字")
    return start_char, end_char


def _job_response(job: MemoirMaterialShareVideo) -> MemoirShareVideoJobResponse:
    metadata = job.metadata_json or {}
    background_style = str(metadata.get("background_style") or DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE)
    background_mode = str(metadata.get("background_mode") or DEFAULT_SHARE_VIDEO_BACKGROUND_MODE)
    background_effective_mode = str(metadata.get("background_effective_mode") or SHARE_VIDEO_BACKGROUND_MODE_SINGLE)
    background_provider = metadata.get("background_provider")
    background_model = metadata.get("background_model")
    background_cache_hit = metadata.get("background_cache_hit")
    background_scene_count = metadata.get("background_scene_count")
    download_url = (
        f"/api/mobile/memoir/share-video/jobs/{job.id}/download"
        if job.status == MemoirShareVideoJobStatus.SUCCEEDED.value and job.video_storage_path
        else None
    )
    return MemoirShareVideoJobResponse(
        id=job.id,
        project_id=job.project_id,
        utterance_id=job.utterance_id,
        audio_file_id=job.audio_file_id,
        status=MemoirShareVideoJobStatus(job.status),
        selected_text=job.selected_text,
        start_char=job.start_char,
        end_char=job.end_char,
        start_ms=job.start_ms,
        end_ms=job.end_ms,
        duration_ms=max(0, job.end_ms - job.start_ms),
        style=job.style,
        background_style=background_style,
        background_mode=background_mode,
        background_effective_mode=background_effective_mode,
        background_scene_count=background_scene_count if isinstance(background_scene_count, int) else 0,
        background_status=str(metadata.get("background_status") or "disabled"),
        background_provider=background_provider if isinstance(background_provider, str) else None,
        background_model=background_model if isinstance(background_model, str) else None,
        background_cache_hit=background_cache_hit if isinstance(background_cache_hit, bool) else None,
        background_error_message=str(metadata.get("background_error_message") or ""),
        video_mime_type=job.video_mime_type,
        video_size_bytes=job.video_size_bytes,
        download_url=download_url,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _parse_metadata_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _should_recover_stale_share_video_job(
    job: MemoirMaterialShareVideo,
    *,
    now: datetime | None = None,
) -> bool:
    if job.status not in {
        MemoirShareVideoJobStatus.PENDING.value,
        MemoirShareVideoJobStatus.RUNNING.value,
    }:
        return False
    if job.video_storage_path:
        return False

    current_time = now or datetime.utcnow()
    last_update = job.updated_at or job.created_at
    if current_time - last_update < timedelta(seconds=_STALE_SHARE_VIDEO_JOB_SECONDS):
        return False

    metadata = job.metadata_json or {}
    recovery_enqueued_at = _parse_metadata_datetime(metadata.get("recovery_enqueued_at"))
    if recovery_enqueued_at and (
        current_time - recovery_enqueued_at
        < timedelta(seconds=_STALE_SHARE_VIDEO_RECOVERY_BACKOFF_SECONDS)
    ):
        return False
    return True


async def _mark_share_video_job_recovery_enqueued(
    db: AsyncSession,
    job: MemoirMaterialShareVideo,
    *,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.utcnow()
    metadata = dict(job.metadata_json or {})
    metadata["recovery_enqueued_at"] = current_time.isoformat()
    metadata["recovery_reason"] = "stale_running"
    _set_job_metadata(job, metadata)
    await db.commit()


def _effective_background_mode(background_mode: str, duration_ms: int) -> str:
    if background_mode == SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI:
        return SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI
    if background_mode == SHARE_VIDEO_BACKGROUND_MODE_SINGLE:
        return SHARE_VIDEO_BACKGROUND_MODE_SINGLE
    return (
        SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI
        if duration_ms >= 20_000
        else SHARE_VIDEO_BACKGROUND_MODE_SINGLE
    )


def _scene_metadata(scene: ShareVideoScene) -> dict[str, object]:
    return {
        "index": scene.index,
        "start_char": scene.start_char,
        "end_char": scene.end_char,
        "start_ms": scene.start_ms,
        "end_ms": scene.end_ms,
        "title": scene.title,
        "visual_summary": scene.visual_summary,
        "image_prompt_content": scene.image_prompt_content,
    }


def _render_background_segments(
    scenes: list[ShareVideoScene],
    image_paths: list[Path],
    duration_ms: int,
) -> list[ShareVideoBackgroundSegment]:
    if not scenes or not image_paths:
        return []
    raw_durations = [
        max(1, scene.end_ms - scene.start_ms)
        for scene in scenes[: len(image_paths)]
    ]
    raw_total = max(1, sum(raw_durations))
    segments: list[ShareVideoBackgroundSegment] = []
    cursor = 0
    for index, (raw_duration, image_path) in enumerate(zip(raw_durations, image_paths, strict=False)):
        if index == len(raw_durations) - 1:
            end_ms = max(cursor + 1, duration_ms)
        else:
            remaining_segments = len(raw_durations) - index - 1
            computed_end_ms = cursor + max(1, round(duration_ms * raw_duration / raw_total))
            end_ms = min(computed_end_ms, max(cursor + 1, duration_ms - remaining_segments))
        segments.append(
            ShareVideoBackgroundSegment(
                image_path=image_path,
                start_ms=cursor,
                end_ms=end_ms,
            )
        )
        cursor = end_ms
    return segments


async def create_share_video_job(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    req: MemoirShareVideoJobCreateRequest,
    *,
    allow_admin: bool = False,
) -> MemoirShareVideoJobResponse:
    utterance = await _load_owned_utterance(db, utterance_id, user_id, allow_admin=allow_admin)
    alignment = build_share_video_alignment(utterance)
    start_char, end_char = _trim_selection(alignment.transcript_text, req.start_char, req.end_char)
    start_ms, end_ms = share_video_char_range_ms(alignment, start_char, end_char)
    try:
        background_style = normalize_share_video_background_style(req.background_style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        background_mode = normalize_share_video_background_mode(req.background_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    duration_ms = end_ms - start_ms
    if duration_ms < MIN_SELECT_MS:
        raise HTTPException(status_code=400, detail=f"分享视频至少需要 {MIN_SELECT_MS // 1000} 秒音频")
    if duration_ms > MAX_SELECT_MS:
        raise HTTPException(status_code=400, detail=f"分享视频不能超过 {MAX_SELECT_MS // 1000} 秒")
    audio_file = utterance.audio_file
    if not audio_file:
        raise HTTPException(status_code=400, detail="素材没有可分享的音频")

    job = MemoirMaterialShareVideo(
        project_id=utterance.voice_session.project_id,
        utterance_id=utterance.id,
        audio_file_id=audio_file.id,
        user_id=user_id,
        status=MemoirShareVideoJobStatus.PENDING.value,
        text_source=alignment.text_source,
        text_hash=alignment.text_hash,
        selected_text=alignment.transcript_text[start_char:end_char],
        start_char=start_char,
        end_char=end_char,
        start_ms=start_ms,
        end_ms=end_ms,
        style=req.style,
        style_json=req.style_options,
        metadata_json={
            "alignment_strategy": alignment.strategy,
            "alignment_confidence": alignment.confidence,
            "char_timings": alignment.char_timings[start_char:end_char],
            "background_style": background_style,
            "background_mode": background_mode,
            "background_effective_mode": _effective_background_mode(background_mode, duration_ms),
            "background_scene_count": 0,
            "background_status": (
                "pending"
                if get_settings().memoir_share_video_ai_background_enabled
                else "disabled"
            ),
        },
        video_mime_type=_VIDEO_MIME_TYPE,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _job_response(job)


async def _load_owned_job(
    db: AsyncSession,
    job_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> MemoirMaterialShareVideo:
    filters = [MemoirMaterialShareVideo.id == job_id]
    if not allow_admin:
        filters.append(or_(MemoirMaterialShareVideo.user_id == user_id, Project.user_id == user_id))
    result = await db.execute(
        select(MemoirMaterialShareVideo)
        .join(Project, MemoirMaterialShareVideo.project_id == Project.id)
        .where(*filters)
        .options(
            selectinload(MemoirMaterialShareVideo.audio_file),
            selectinload(MemoirMaterialShareVideo.utterance),
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="分享视频任务不存在")
    return job


async def get_share_video_job(
    db: AsyncSession,
    job_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
    enqueue_recovery: Callable[[str], None] | None = None,
) -> MemoirShareVideoJobResponse:
    job = await _load_owned_job(db, job_id, user_id, allow_admin=allow_admin)
    if enqueue_recovery and _should_recover_stale_share_video_job(job):
        await _mark_share_video_job_recovery_enqueued(db, job)
        enqueue_recovery(job.id)
    return _job_response(job)


async def load_completed_share_video_job(
    db: AsyncSession,
    job_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> tuple[MemoirMaterialShareVideo, Path]:
    job = await _load_owned_job(db, job_id, user_id, allow_admin=allow_admin)
    if job.status != MemoirShareVideoJobStatus.SUCCEEDED.value or not job.video_storage_path:
        raise HTTPException(status_code=409, detail="分享视频还没有生成完成")
    path = share_video_file_path(job)
    if not path.exists():
        raise HTTPException(status_code=404, detail="分享视频文件不存在")
    return job, path


async def run_share_video_job(job_id: str) -> None:
    async with async_session_maker() as db:
        await run_share_video_job_with_session(db, job_id)


async def run_share_video_job_with_session(db: AsyncSession, job_id: str) -> None:
    job = await db.scalar(
        select(MemoirMaterialShareVideo)
        .where(MemoirMaterialShareVideo.id == job_id)
        .options(
            selectinload(MemoirMaterialShareVideo.audio_file),
            selectinload(MemoirMaterialShareVideo.utterance),
        )
    )
    if not job or job.status == MemoirShareVideoJobStatus.SUCCEEDED.value:
        return
    job.status = MemoirShareVideoJobStatus.RUNNING.value
    job.error_message = ""
    await db.commit()
    try:
        metadata = dict(job.metadata_json or {})
        background_style = normalize_share_video_background_style(
            metadata.get("background_style") if isinstance(metadata.get("background_style"), str) else None
        )
        background_mode = normalize_share_video_background_mode(
            metadata.get("background_mode") if isinstance(metadata.get("background_mode"), str) else None
        )
        duration_ms = max(1, job.end_ms - job.start_ms)
        settings = get_settings()
        effective_background_mode = _effective_background_mode(background_mode, duration_ms)
        volc_ark_api_key = str(getattr(settings, "volc_ark_api_key", "") or "")
        metadata["background_style"] = background_style
        metadata["background_mode"] = background_mode
        metadata["background_effective_mode"] = effective_background_mode
        background_image_path: Path | None = None
        background_segments: list[ShareVideoBackgroundSegment] = []
        if not settings.memoir_share_video_ai_background_enabled or not volc_ark_api_key.strip():
            metadata.update({
                "background_status": "disabled",
                "background_effective_mode": SHARE_VIDEO_BACKGROUND_MODE_SINGLE,
                "background_scene_count": 0,
                "background_error_message": "",
            })
            effective_background_mode = SHARE_VIDEO_BACKGROUND_MODE_SINGLE
            _set_job_metadata(job, metadata)
            await db.commit()
        elif effective_background_mode == SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI:
            char_timings = [
                item for item in metadata.get("char_timings", [])
                if isinstance(item, dict)
            ]
            metadata.update({
                "background_status": "planning",
                "background_error_message": "",
            })
            _set_job_metadata(job, metadata)
            await db.commit()

            plan = await plan_share_video_background_scenes(
                job.selected_text,
                char_timings,
                duration_ms,
            )
            scene_plan = scene_plan_to_metadata(plan)
            metadata.update({
                "background_status": "generating",
                "background_scene_plan": scene_plan,
                "background_scene_count": len(plan.scenes),
                "background_generated_count": 0,
                "background_plan_provider": plan.provider,
                "background_plan_model": plan.model,
                "background_error_message": "",
            })
            _set_job_metadata(job, metadata)
            await db.commit()

            scene_assets: list[dict[str, object]] = []
            valid_paths: list[Path | None] = []
            first_valid_path: Path | None = None
            first_asset_provider: str | None = None
            first_asset_model: str | None = None
            generated_count = 0
            errors: list[str] = []
            for scene in plan.scenes:
                scene_info = _scene_metadata(scene)
                try:
                    asset = await get_or_generate_share_video_background(
                        scene.image_prompt_content,
                        background_style,
                    )
                except ShareVideoBackgroundGenerationError as exc:
                    errors.append(f"{scene.index + 1}: {str(exc)[:120]}")
                    scene_info.update({
                        "background_status": "failed",
                        "background_error_message": str(exc)[:300],
                    })
                    valid_paths.append(None)
                else:
                    if asset is None:
                        scene_info.update({
                            "background_status": "disabled",
                            "background_error_message": "",
                        })
                        valid_paths.append(None)
                    else:
                        generated_count += 1
                        first_valid_path = first_valid_path or asset.path
                        first_asset_provider = first_asset_provider or asset.provider
                        first_asset_model = first_asset_model or asset.model
                        valid_paths.append(asset.path)
                        scene_info.update({
                            "background_status": "ready",
                            "background_storage_path": asset.storage_path,
                            "background_prompt_hash": asset.prompt_hash,
                            "background_prompt_version": asset.prompt_template_version,
                            "background_provider": asset.provider,
                            "background_model": asset.model,
                            "background_cache_hit": asset.cache_hit,
                            "background_error_message": "",
                        })
                scene_assets.append(scene_info)
                metadata.update({
                    "background_generated_count": generated_count,
                    "background_scenes": scene_assets,
                })
                _set_job_metadata(job, metadata)
                await db.commit()

            if first_valid_path is None:
                metadata.update({
                    "background_status": "failed",
                    "background_error_message": "智能多图背景生成失败，已使用经典背景",
                    "background_scenes": scene_assets,
                })
            else:
                filled_paths: list[Path] = []
                previous_path: Path | None = None
                reused_count = 0
                for path in valid_paths:
                    if path is None:
                        reused_count += 1
                        path = previous_path or first_valid_path
                    filled_paths.append(path)
                    previous_path = path
                background_segments = _render_background_segments(
                    plan.scenes,
                    filled_paths,
                    duration_ms,
                )
                metadata.update({
                    "background_status": "partial" if errors or reused_count else "ready",
                    "background_error_message": "；".join(errors)[:500],
                    "background_scenes": scene_assets,
                    "background_reused_scene_count": reused_count,
                    "background_provider": first_asset_provider,
                    "background_model": first_asset_model,
                    "background_cache_hit": None,
                })
            _set_job_metadata(job, metadata)
            await db.commit()
        else:
            try:
                background = await get_or_generate_share_video_background(job.selected_text, background_style)
            except ShareVideoBackgroundGenerationError as exc:
                metadata.update({
                    "background_status": "failed",
                    "background_scene_count": 0,
                    "background_error_message": str(exc)[:500],
                })
            else:
                if background is None:
                    metadata.update({
                        "background_status": "disabled",
                        "background_scene_count": 0,
                        "background_error_message": "",
                    })
                else:
                    background_image_path = background.path
                    metadata.update({
                        "background_status": "ready",
                        "background_scene_count": 1,
                        "background_storage_path": background.storage_path,
                        "background_prompt_hash": background.prompt_hash,
                        "background_prompt_version": background.prompt_template_version,
                        "background_provider": background.provider,
                        "background_model": background.model,
                        "background_cache_hit": background.cache_hit,
                        "background_error_message": "",
                    })
            _set_job_metadata(job, metadata)
            await db.commit()

        target_path = share_video_file_path(job)
        render_request = ShareVideoRenderRequest(
            source_audio_path=audio_file_path(job.audio_file),
            source_audio_mime_type=job.audio_file.mime_type,
            target_path=target_path,
            selected_text=job.selected_text,
            char_timings=[
                item for item in (job.metadata_json or {}).get("char_timings", [])
                if isinstance(item, dict)
            ],
            start_ms=job.start_ms,
            end_ms=job.end_ms,
            style=job.style,
            style_options=job.style_json,
            background_image_path=background_image_path,
            background_segments=background_segments,
        )
        try:
            await render_share_video_clip(render_request)
        except ShareVideoGenerationError as exc:
            if background_image_path is None and not background_segments:
                raise
            metadata = dict(job.metadata_json or {})
            metadata.update(
                {
                    "background_status": "fallback",
                    "background_error_message": f"背景图渲染失败，已回退经典样式：{str(exc)[:400]}",
                }
            )
            _set_job_metadata(job, metadata)
            await db.commit()
            await render_share_video_clip(
                ShareVideoRenderRequest(
                    source_audio_path=render_request.source_audio_path,
                    source_audio_mime_type=render_request.source_audio_mime_type,
                    target_path=render_request.target_path,
                    selected_text=render_request.selected_text,
                    char_timings=render_request.char_timings,
                    start_ms=render_request.start_ms,
                    end_ms=render_request.end_ms,
                    style=render_request.style,
                    style_options=render_request.style_options,
                    background_image_path=None,
                    background_segments=None,
                )
            )
        job.video_storage_path = _storage_path(job.user_id, job.id)
        job.video_size_bytes = target_path.stat().st_size
        job.status = MemoirShareVideoJobStatus.SUCCEEDED.value
        job.completed_at = datetime.utcnow()
    except Exception as exc:
        job.status = MemoirShareVideoJobStatus.FAILED.value
        job.error_message = str(exc)[:1000] or "分享视频生成失败"
    await db.commit()


def _ffmpeg_executable() -> str:
    configured = os.environ.get("FFMPEG_BINARY", "").strip()
    if configured:
        return configured
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
    except Exception as exc:
        raise ShareVideoGenerationError("视频编码器不可用") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


async def _run_ffmpeg(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:]
        raise ShareVideoGenerationError(message[0] if message else "ffmpeg 执行失败")


def _ass_time(ms: int) -> str:
    centiseconds = max(0, round(ms / 10))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "")


def _subtitle_ranges(text: str, *, max_chars: int = 24) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char in "。！？!?；;\n" or index - start + 1 >= max_chars:
            ranges.append((start, index + 1))
            start = index + 1
    if start < len(text):
        ranges.append((start, len(text)))
    return [(start, end) for start, end in ranges if text[start:end].strip()]


def _strip_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _preferred_compact_line_break(text: str, start: int, end: int) -> int | None:
    length = end - start
    if length <= 8:
        return None
    midpoint = start + length / 2
    candidates = [
        index
        for index in range(start + 3, end - 3)
        if text[index] in "与和但而却"
    ]
    if candidates:
        return min(candidates, key=lambda index: abs(index - midpoint))
    return start + round(length / 2)


def _quote_line_ranges(text: str, start: int, end: int) -> list[tuple[int, int]]:
    start, end = _strip_range(text, start, end)
    if end <= start:
        return []
    if end - start <= 16 and "\n" not in text[start:end]:
        compact_break = _preferred_compact_line_break(text, start, end)
        if compact_break:
            return [
                item
                for item in (
                    _strip_range(text, start, compact_break),
                    _strip_range(text, compact_break, end),
                )
                if item[0] < item[1]
            ]

    lines: list[tuple[int, int]] = []
    line_start = start
    last_soft_break: int | None = None
    for index in range(start, end):
        char = text[index]
        if char in "，,、；;":
            last_soft_break = index + 1
        if char == "\n":
            line_end = index
        elif char in "。！？!?" or index - line_start + 1 >= _QUOTE_LINE_MAX_CHARS:
            line_end = last_soft_break if last_soft_break and last_soft_break > line_start else index + 1
        else:
            continue
        line_range = _strip_range(text, line_start, line_end)
        if line_range[0] < line_range[1]:
            lines.append(line_range)
        line_start = index + 1 if char == "\n" else line_end
        last_soft_break = None
    if line_start < end:
        line_range = _strip_range(text, line_start, end)
        if line_range[0] < line_range[1]:
            lines.append(line_range)
    return lines


def _karaoke_phrase_ranges(text: str, start: int, end: int) -> list[tuple[int, int]]:
    phrases: list[tuple[int, int]] = []
    phrase_start = start
    visible_chars = 0
    for index in range(start, end):
        char = text[index]
        visible_chars += 0 if char.isspace() else 1
        should_break = char in "，,、。！？!?；;" or visible_chars >= _QUOTE_PHRASE_MAX_CHARS
        if should_break:
            phrase_range = _strip_range(text, phrase_start, index + 1)
            if phrase_range[0] < phrase_range[1]:
                phrases.append(phrase_range)
            phrase_start = index + 1
            visible_chars = 0
    if phrase_start < end:
        phrase_range = _strip_range(text, phrase_start, end)
        if phrase_range[0] < phrase_range[1]:
            phrases.append(phrase_range)
    return phrases


def _linear_timings(text: str, duration_ms: int) -> list[dict[str, int]]:
    char_count = max(1, len(text))
    return [
        {
            "start_ms": round(duration_ms * index / char_count),
            "end_ms": round(duration_ms * (index + 1) / char_count),
        }
        for index, _char in enumerate(text)
    ]


def _ass_text(text: str) -> str:
    return _ass_escape(text).replace("\n", " ")


def _decorative_subtitle_events(duration_ms: int) -> list[str]:
    end_time = _ass_time(duration_ms)
    return [
        f"Dialogue: 0,0:00:00.00,{end_time},Meta,,0,0,0,,{{\\an8\\pos(640,102)\\fad(160,160)}}回忆录访谈 · 原声摘录\n",
        f"Dialogue: 0,0:00:00.00,{end_time},Rule,,0,0,0,,{{\\an7\\pos(500,144)\\alpha&H55&\\p1}}m 0 0 l 280 0 l 280 2 l 0 2\n",
        f"Dialogue: 0,0:00:00.00,{end_time},Mark,,0,0,0,,{{\\an7\\pos(238,226)\\alpha&H80&}}“\n",
        f"Dialogue: 0,0:00:00.00,{end_time},Meta,,0,0,0,,{{\\an2\\pos(640,618)\\fad(160,160)}}口述片段\n",
        f"Dialogue: 0,0:00:00.00,{end_time},Rule,,0,0,0,,{{\\an7\\pos(500,646)\\alpha&H66&\\p1}}m 0 0 l 280 0 l 280 2 l 0 2\n",
    ]


def _quote_karaoke_text(
    text: str,
    lines: list[tuple[int, int]],
    char_timings: list[dict[str, int]],
) -> str:
    pieces: list[str] = [r"{\an5\pos(640,356)\q2\fad(180,180)}"]
    for line_index, (line_start, line_end) in enumerate(lines):
        if line_index:
            pieces.append(r"\N")
        for phrase_start, phrase_end in _karaoke_phrase_ranges(text, line_start, line_end):
            phrase_duration = (
                int(char_timings[phrase_end - 1]["end_ms"])
                - int(char_timings[phrase_start]["start_ms"])
            )
            pieces.append(
                r"{\kf"
                + str(max(1, round(phrase_duration / 10)))
                + "}"
                + _ass_text(text[phrase_start:phrase_end])
            )
    return "".join(pieces)


def _subtitle_file_content(request: ShareVideoRenderRequest) -> str:
    duration_ms = max(1, request.end_ms - request.start_ms)
    char_timings = request.char_timings
    if len(char_timings) != len(request.selected_text):
        char_timings = _linear_timings(request.selected_text, duration_ms)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Quote,{_SHARE_VIDEO_FONT_NAME},62,{_ASS_TEA_GOLD_COLOR},{_ASS_INK_COLOR},{_ASS_INK_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,120,120,80,1
Style: Meta,{_SHARE_VIDEO_FONT_NAME},28,{_ASS_MUTED_COLOR},{_ASS_MUTED_COLOR},{_ASS_MUTED_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,80,80,80,1
Style: Mark,{_SHARE_VIDEO_FONT_NAME},112,{_ASS_RULE_COLOR},{_ASS_RULE_COLOR},{_ASS_RULE_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Rule,{_SHARE_VIDEO_FONT_NAME},10,{_ASS_RULE_COLOR},{_ASS_RULE_COLOR},{_ASS_RULE_COLOR},&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [header, *_decorative_subtitle_events(duration_ms)]
    for start, end in _subtitle_ranges(request.selected_text, max_chars=_QUOTE_PAGE_MAX_CHARS):
        lines = _quote_line_ranges(request.selected_text, start, end)
        if not lines:
            continue
        start_ms = max(0, int(char_timings[start]["start_ms"]) - request.start_ms)
        end_ms = max(start_ms + 1, int(char_timings[end - 1]["end_ms"]) - request.start_ms)
        events.append(
            f"Dialogue: 1,{_ass_time(start_ms)},{_ass_time(end_ms)},Quote,,0,0,0,,"
            f"{_quote_karaoke_text(request.selected_text, lines, char_timings)}\n"
        )
    return "".join(events)


def _escaped_subtitle_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _share_video_font_dir() -> Path:
    font_path = _SHARE_VIDEO_FONT_DIR / _SHARE_VIDEO_FONT_FILE
    if not font_path.exists():
        raise ShareVideoGenerationError("分享视频中文字体文件缺失")
    return _SHARE_VIDEO_FONT_DIR


def _subtitle_filter_argument(subtitle_path: Path) -> str:
    font_dir = _share_video_font_dir()
    subtitle = _escaped_subtitle_filter_path(subtitle_path)
    fonts = _escaped_subtitle_filter_path(font_dir)
    return f"subtitles=filename='{subtitle}':fontsdir='{fonts}'"


def _video_filter_argument(subtitle_path: Path, *, has_background_image: bool = False) -> str:
    filters: list[str] = []
    if has_background_image:
        filters.append(
            f"scale={_VIDEO_WIDTH}:{_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={_VIDEO_WIDTH}:{_VIDEO_HEIGHT}"
        )
    filters.append(_subtitle_filter_argument(subtitle_path))
    return ",".join(filters)


def _valid_background_segments(
    request: ShareVideoRenderRequest,
) -> list[ShareVideoBackgroundSegment]:
    segments = request.background_segments or []
    return [
        segment
        for segment in segments
        if segment.image_path.exists()
        and segment.image_path.stat().st_size > 0
        and segment.end_ms > segment.start_ms
    ]


def _multi_background_filter_complex(
    segments: list[ShareVideoBackgroundSegment],
    subtitle_path: Path,
) -> str:
    transition_seconds = min(
        0.6,
        max(0.0, min((segment.end_ms - segment.start_ms) / 1000 for segment in segments) / 2),
    )
    parts: list[str] = []
    raw_durations = [
        max(0.1, (segment.end_ms - segment.start_ms) / 1000)
        for segment in segments
    ]
    for index, raw_duration in enumerate(raw_durations):
        duration = raw_duration + (transition_seconds if index < len(raw_durations) - 1 else 0)
        parts.append(
            f"[{index}:v]"
            f"scale={_VIDEO_WIDTH}:{_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={_VIDEO_WIDTH}:{_VIDEO_HEIGHT},setsar=1,"
            f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"fps=30,format=yuv420p,settb=AVTB[v{index}]"
        )

    current_label = "v0"
    if len(segments) > 1 and transition_seconds > 0:
        elapsed = raw_durations[0]
        for index in range(1, len(segments)):
            next_label = f"x{index}"
            parts.append(
                f"[{current_label}][v{index}]"
                f"xfade=transition=fade:duration={transition_seconds:.3f}:offset={elapsed:.3f}"
                f"[{next_label}]"
            )
            current_label = next_label
            elapsed += raw_durations[index]
    elif len(segments) > 1:
        labels = "".join(f"[v{index}]" for index in range(len(segments)))
        parts.append(f"{labels}concat=n={len(segments)}:v=1:a=0[concatbg]")
        current_label = "concatbg"

    parts.append(f"[{current_label}]{_subtitle_filter_argument(subtitle_path)}[vout]")
    return ";".join(parts)


async def render_share_video_clip(request: ShareVideoRenderRequest) -> None:
    ffmpeg = _ffmpeg_executable()
    request.target_path.parent.mkdir(parents=True, exist_ok=True)
    duration_seconds = max(0.1, (request.end_ms - request.start_ms) / 1000)
    with tempfile.TemporaryDirectory(prefix="memoir-share-video-") as tmp:
        tmpdir = Path(tmp)
        subtitle_path = tmpdir / "subtitles.ass"
        clip_audio_path = tmpdir / "clip.wav"
        temp_video_path = tmpdir / "video.mp4"
        subtitle_path.write_text(_subtitle_file_content(request), encoding="utf-8")

        audio_input_args = []
        if request.source_audio_mime_type == "audio/pcm":
            audio_input_args.extend(["-f", "s16le", "-ar", "16000", "-ac", "1"])
        audio_input_args.extend(["-i", str(request.source_audio_path)])
        await _run_ffmpeg([
            ffmpeg,
            "-y",
            *audio_input_args,
            "-ss",
            f"{request.start_ms / 1000:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-c:a",
            "pcm_s16le",
            str(clip_audio_path),
        ])

        background_segments = _valid_background_segments(request)
        if len(background_segments) > 1:
            video_input_args: list[str] = []
            for segment in background_segments:
                duration_seconds_for_input = max(0.1, (segment.end_ms - segment.start_ms) / 1000 + 0.7)
                video_input_args.extend([
                    "-loop",
                    "1",
                    "-t",
                    f"{duration_seconds_for_input:.3f}",
                    "-i",
                    str(segment.image_path),
                ])
            audio_input_index = len(background_segments)
            await _run_ffmpeg([
                ffmpeg,
                "-y",
                *video_input_args,
                "-i",
                str(clip_audio_path),
                "-filter_complex",
                _multi_background_filter_complex(background_segments, subtitle_path),
                "-map",
                "[vout]",
                "-map",
                f"{audio_input_index}:a",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                str(temp_video_path),
            ])
            temp_video_path.replace(request.target_path)
            return

        single_background_image_path = request.background_image_path
        if not single_background_image_path and len(background_segments) == 1:
            single_background_image_path = background_segments[0].image_path
        has_background_image = bool(
            single_background_image_path
            and single_background_image_path.exists()
            and single_background_image_path.stat().st_size > 0
        )
        if has_background_image:
            video_input_args = [
                "-loop",
                "1",
                "-t",
                f"{duration_seconds:.3f}",
                "-i",
                str(single_background_image_path),
            ]
        else:
            video_input_args = [
                "-f",
                "lavfi",
                "-i",
                f"color=c={_BACKGROUND_COLOR}:s={_VIDEO_WIDTH}x{_VIDEO_HEIGHT}:d={duration_seconds:.3f}",
            ]

        await _run_ffmpeg([
            ffmpeg,
            "-y",
            *video_input_args,
            "-i",
            str(clip_audio_path),
            "-vf",
            _video_filter_argument(subtitle_path, has_background_image=has_background_image),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(temp_video_path),
        ])
        temp_video_path.replace(request.target_path)
