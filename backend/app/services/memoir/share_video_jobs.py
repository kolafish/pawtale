from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import shutil
import tempfile

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import DATA_DIR
from app.database import async_session_maker
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.models.memoir import MemoirMaterialShareVideo
from app.models.projects import Project
from app.schemas.memoir_voice import MemoirShareVideoJobCreateRequest, MemoirShareVideoJobResponse
from app.services.memoir.audio_files import audio_file_path
from app.services.memoir.share_video_alignment import (
    MAX_SELECT_MS,
    MIN_SELECT_MS,
    build_share_video_alignment,
    share_video_char_range_ms,
)
from app.services.memoir.voice_transcripts import _load_owned_utterance


MEMOIR_SHARE_VIDEO_DIR = DATA_DIR / "memoir" / "share-videos"
_VIDEO_MIME_TYPE = "video/mp4"
_VIDEO_WIDTH = 1280
_VIDEO_HEIGHT = 720
_BACKGROUND_COLOR = "0xf7f1e8"
_SHARE_VIDEO_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_SHARE_VIDEO_FONT_FILE = "NotoSerifSC-Regular.ttf"
_SHARE_VIDEO_FONT_NAME = "Memoir Noto Serif SC"


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


class ShareVideoGenerationError(RuntimeError):
    pass


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
        video_mime_type=job.video_mime_type,
        video_size_bytes=job.video_size_bytes,
        download_url=download_url,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


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
) -> MemoirShareVideoJobResponse:
    job = await _load_owned_job(db, job_id, user_id, allow_admin=allow_admin)
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
        target_path = share_video_file_path(job)
        await render_share_video_clip(
            ShareVideoRenderRequest(
                source_audio_path=audio_file_path(job.audio_file),
                source_audio_mime_type=job.audio_file.mime_type,
                target_path=target_path,
                selected_text=job.selected_text,
                char_timings=[
                    item for item in job.metadata_json.get("char_timings", [])
                    if isinstance(item, dict)
                ],
                start_ms=job.start_ms,
                end_ms=job.end_ms,
                style=job.style,
                style_options=job.style_json,
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


def _linear_timings(text: str, duration_ms: int) -> list[dict[str, int]]:
    char_count = max(1, len(text))
    return [
        {
            "start_ms": round(duration_ms * index / char_count),
            "end_ms": round(duration_ms * (index + 1) / char_count),
        }
        for index, _char in enumerate(text)
    ]


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
Style: Transcript,{_SHARE_VIDEO_FONT_NAME},58,&H0000A8E8,&H003C3327,&H00F7F1E8,&H55000000,0,0,0,0,100,100,0,0,1,2,0,5,80,80,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [header]
    for start, end in _subtitle_ranges(request.selected_text):
        start_ms = max(0, int(char_timings[start]["start_ms"]) - request.start_ms)
        end_ms = max(start_ms + 1, int(char_timings[end - 1]["end_ms"]) - request.start_ms)
        pieces = [r"{\an5\pos(640,360)\fad(120,120)}"]
        for index in range(start, end):
            char_duration = int(char_timings[index]["end_ms"]) - int(char_timings[index]["start_ms"])
            pieces.append(r"{\kf" + str(max(1, round(char_duration / 10))) + "}" + _ass_escape(request.selected_text[index]))
        events.append(
            f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},Transcript,,0,0,0,,{''.join(pieces)}\n"
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

        await _run_ffmpeg([
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={_BACKGROUND_COLOR}:s={_VIDEO_WIDTH}x{_VIDEO_HEIGHT}:d={duration_seconds:.3f}",
            "-i",
            str(clip_audio_path),
            "-vf",
            _subtitle_filter_argument(subtitle_path),
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
