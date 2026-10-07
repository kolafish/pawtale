from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.clock import utcnow_naive
from app.config import DATA_DIR, get_settings
from app.database import async_session_maker
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.models.memoir import (
    MemoirBookOutput,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
    MemoirShareVideoOutput,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirShareVideoJobCreateRequest,
    MemoirShareVideoJobListResponse,
    MemoirShareVideoJobResponse,
    MemoirShareVideoListItemResponse,
    MemoirShareVideoOutputResponse,
    MemoirShareVideoCountResponse,
)
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_usage_records import AIUsageContext, reset_ai_usage_context, set_ai_usage_context
from app.services import wechat_miniprogram_code
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir import share_video_background_orchestration as background_orchestration
from app.services.memoir import share_video_rendering as rendering
from app.services.memoir.audio_files import audio_file_path
from app.services.memoir.book_output_manuscript import project_narrator_name
from app.services.memoir.job_leases import (
    JobLease,
    JobLeaseLostError,
    lease_deadline,
    new_job_lease,
    run_with_lease_heartbeat,
)
from app.services.memoir.share_video_backgrounds import (
    SHARE_VIDEO_BACKGROUND_MODE_SINGLE,
    normalize_share_video_background_mode,
    normalize_share_video_background_style,
)
from app.services.memoir.share_video_custom_backgrounds import (
    SHARE_VIDEO_BACKGROUND_SOURCE_AI,
    SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC,
    SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM,
    load_custom_share_video_background_asset,
    normalize_share_video_background_source,
)
from app.services.memoir.share_video_alignment import (
    MAX_SELECT_MS,
    MIN_SELECT_MS,
    build_share_video_alignment,
    current_share_video_text_and_hash,
    share_video_char_range_ms,
)
from app.services.memoir.share_video_metadata import ShareVideoJobMetadata
from app.services.memoir.access import load_memoir_project
from app.services.memoir.project_ai_preferences import project_share_video_style, select_project_ai_model
from app.services.memoir.share_video_styles import SHARE_VIDEO_STYLE_STORY_ANIMATION
from app.services.memoir.share_video_story_plan import (
    SHARE_VIDEO_STORY_PLAN_VERSION,
    plan_share_video_story,
    validate_story_plan_source,
)
from app.services.memoir.voice_transcripts import _load_owned_utterance


MEMOIR_SHARE_VIDEO_DIR = DATA_DIR / "memoir" / "share-videos"
SHARE_VIDEO_PIPELINE_VERSION = "memoir-share-video-v4"
# Mini program page that plays a forwarded clip; the code passes share_code as its scene.
SHARE_VIDEO_PLAYER_PAGE = "pages/share-video-player/index"
_VIDEO_MIME_TYPE = "video/mp4"
_COMPLETION_MARKER_SUFFIX = ".complete"
_COMPLETION_RECOVERY_POLL_SECONDS = 1.0
_TITLE_MAX_CHARS = 18
_TITLE_FALLBACK = "回忆片段"
_TITLE_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_TITLE_SENSITIVE_NUMBER = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)")
_TITLE_CLAUSE_BREAK = re.compile(r"[。！？!?；;，,：:\n]")
logger = logging.getLogger(__name__)

_ACTIVE_SHARE_VIDEO_STATUSES = frozenset(
    {
        MemoirShareVideoJobStatus.PENDING.value,
        MemoirShareVideoJobStatus.RUNNING.value,
    }
)


class ShareVideoLeaseLostError(RuntimeError):
    pass


class _ShareVideoTitleChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=24)
    hook_sentence_id: int | None = None


def _clean_share_video_title(value: str) -> str:
    compact = " ".join(value.split())
    safe = "".join(
        char
        for char in _TITLE_UNSAFE_CHARS.sub("", compact)
        if not unicodedata.category(char).startswith("C")
    )
    return safe[:_TITLE_MAX_CHARS].strip(" .，。！？!?；;：:、‘’“”《》\"'")


def _title_contains_sensitive_number(value: str) -> bool:
    return _TITLE_SENSITIVE_NUMBER.search(re.sub(r"[\s-]", "", value)) is not None


def fallback_share_video_title(selected_text: str) -> str:
    first_clause = _TITLE_CLAUSE_BREAK.split(selected_text.strip(), maxsplit=1)[0]
    source = first_clause or selected_text
    if _title_contains_sensitive_number(source):
        return _TITLE_FALLBACK
    return _clean_share_video_title(source) or _TITLE_FALLBACK


def share_video_title(metadata: ShareVideoJobMetadata, selected_text: str) -> str:
    saved_title = _clean_share_video_title(metadata.title)
    if saved_title and not _title_contains_sensitive_number(metadata.title):
        return saved_title
    return fallback_share_video_title(selected_text)


def share_video_download_filename(title: str) -> str:
    safe_title = _clean_share_video_title(title)
    if _title_contains_sensitive_number(title):
        safe_title = ""
    return f"{safe_title or _TITLE_FALLBACK}.mp4"


async def share_video_end_card_source(db: AsyncSession, project_id: str) -> tuple[str, str]:
    """The book a shared clip comes from, named on the video's closing card."""
    project = await db.get(Project, project_id)
    book_title = await db.scalar(select(MemoirBookOutput.title).where(MemoirBookOutput.project_id == project_id))
    if project is None:
        return book_title or "", ""
    return book_title or project.title, project_narrator_name(project)


async def _share_video_mini_program_code(share_code: str) -> bytes | None:
    """A code on the closing card that opens this clip; the video renders without it if WeChat fails."""
    if not wechat_miniprogram_code.miniprogram_code_configured():
        return None
    try:
        return await wechat_miniprogram_code.fetch_miniprogram_code(
            scene=share_code, page=SHARE_VIDEO_PLAYER_PAGE,
        )
    except wechat_miniprogram_code.WeChatMiniProgramCodeError:
        logger.warning("share video mini program code unavailable share_code=%s", share_code, exc_info=True)
        return None


def _hook_sentences(selected_text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.findall(r"[^。！？!?；;\n]+[。！？!?；;…]*", selected_text)
        if sentence.strip()
    ]


async def _suggest_share_video_title_and_hook(
    selected_text: str, *, ai_model: str | None,
) -> tuple[str | None, str]:
    """Title the clip and pick one of its own sentences to open the video."""
    sentences = _hook_sentences(selected_text)
    policy = ai_text_workload_policy(AITextWorkload.REALTIME_STRUCTURED)
    generated = await generate_structured_with_client(
        get_ai_service(ai_model),
        "你为家庭回忆录的分享视频拟短标题，并从原话里挑一句放在视频开头。只根据提供的实际视频片段，忠实于原话，突出具体的人、事或场景。"
        "不要杜撰原文没有的事实，不要写营销文案，不要暴露电话号码或身份证号码。"
        "标题用自然简洁的中文，建议 6 到 12 个字，最多 18 个字。"
        "hook_sentence_id 填 sentences 中最能让人想听下去的一句的编号：情绪最重、或留下悬念的那一句，最好不超过 20 个字；"
        "没有合适的句子时填 null。"
        '只返回 JSON 对象，格式为 {"title":"...","hook_sentence_id":0}；不要 Markdown 或解释。',
        json.dumps({
            "text": selected_text,
            "sentences": [{"id": index, "text": sentence} for index, sentence in enumerate(sentences)],
        }, ensure_ascii=False),
        output_model=_ShareVideoTitleChoice,
        requested_model=ai_model,
        temperature=0.2,
        max_tokens=min(policy.max_tokens, 256),
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    proposed = generated.value.title
    cleaned = "" if _title_contains_sensitive_number(proposed) else _clean_share_video_title(proposed)
    hook_id = generated.value.hook_sentence_id
    hook = sentences[hook_id] if hook_id is not None and 0 <= hook_id < len(sentences) else ""
    if _title_contains_sensitive_number(hook):
        hook = ""
    return (cleaned if len(cleaned) >= 2 else None), rendering.usable_hook_text(hook, selected_text)


def _share_video_options_signature(metadata: ShareVideoJobMetadata) -> tuple[str, ...]:
    if metadata.video_style == SHARE_VIDEO_STYLE_STORY_ANIMATION:
        return (metadata.video_style, metadata.animation_version, metadata.ai_model or "")
    return (
        metadata.video_style,
        metadata.ai_model or "",
        metadata.background_source,
        metadata.background_style,
        metadata.background_mode,
        metadata.custom_background_asset_id or "",
    )


def _completed_share_video_is_reusable(
    job: MemoirMaterialShareVideo,
    metadata: ShareVideoJobMetadata,
) -> bool:
    output = job.output
    if output is None or metadata.pipeline_version != SHARE_VIDEO_PIPELINE_VERSION:
        return False
    if metadata.video_style == SHARE_VIDEO_STYLE_STORY_ANIMATION:
        if (
            metadata.animation_version != SHARE_VIDEO_STORY_PLAN_VERSION
            or metadata.animation_plan is None
            or metadata.animation_plan.version != metadata.animation_version
        ):
            return False
    elif metadata.background_source == SHARE_VIDEO_BACKGROUND_SOURCE_AI and metadata.background_status not in {
        "ready",
        "partial",
    }:
        return False
    try:
        path = share_video_file_path(job, output)
        return path.is_file() and path.stat().st_size == output.size_bytes
    except OSError:
        return False


async def _find_duplicate_or_reusable_share_video_job(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    utterance_id: str,
    audio_file_id: str,
    text_hash: str,
    start_char: int,
    end_char: int,
    start_ms: int,
    end_ms: int,
    metadata: ShareVideoJobMetadata,
) -> MemoirMaterialShareVideo | None:
    candidates = (
        await db.scalars(
            select(MemoirMaterialShareVideo)
            .where(
                MemoirMaterialShareVideo.project_id == project_id,
                MemoirMaterialShareVideo.user_id == user_id,
                MemoirMaterialShareVideo.utterance_id == utterance_id,
                MemoirMaterialShareVideo.audio_file_id == audio_file_id,
                MemoirMaterialShareVideo.text_hash == text_hash,
                MemoirMaterialShareVideo.start_char == start_char,
                MemoirMaterialShareVideo.end_char == end_char,
                MemoirMaterialShareVideo.start_ms == start_ms,
                MemoirMaterialShareVideo.end_ms == end_ms,
                MemoirMaterialShareVideo.status.in_(
                    (*_ACTIVE_SHARE_VIDEO_STATUSES, MemoirShareVideoJobStatus.SUCCEEDED.value)
                ),
            )
            .order_by(MemoirMaterialShareVideo.created_at.desc())
        )
    ).all()
    request_signature = _share_video_options_signature(metadata)
    for candidate in candidates:
        candidate_metadata = ShareVideoJobMetadata.from_json(candidate.metadata_json)
        if _share_video_options_signature(candidate_metadata) != request_signature:
            continue
        if candidate.status in _ACTIVE_SHARE_VIDEO_STATUSES or _completed_share_video_is_reusable(
            candidate, candidate_metadata
        ):
            return candidate
    return None


def _set_job_metadata(job: MemoirMaterialShareVideo, metadata: ShareVideoJobMetadata) -> None:
    job.metadata_json = metadata.to_json()
    flag_modified(job, "metadata_json")


def _storage_path(user_id: str, job_id: str) -> str:
    return f"memoir/share-videos/{user_id}/{job_id}.mp4"


def share_video_file_path(
    job: MemoirMaterialShareVideo,
    output: MemoirShareVideoOutput | None = None,
) -> Path:
    storage_path = Path(
        output.storage_path if output else _storage_path(job.user_id, job.id)
    )
    try:
        relative_path = storage_path.relative_to(Path("memoir") / "share-videos")
    except ValueError:
        return DATA_DIR / storage_path
    return MEMOIR_SHARE_VIDEO_DIR / relative_path


def _completion_marker_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.name}{_COMPLETION_MARKER_SUFFIX}")


def _publish_completed_video(video_path: Path) -> int:
    size_bytes = video_path.stat().st_size
    if size_bytes <= 0:
        raise rendering.ShareVideoGenerationError("生成的视频文件为空")
    marker_path = _completion_marker_path(video_path)
    temporary_marker_path = marker_path.with_name(f"{marker_path.name}.tmp")
    temporary_marker_path.write_text(str(size_bytes), encoding="ascii")
    temporary_marker_path.replace(marker_path)
    return size_bytes


def _published_video_size(video_path: Path) -> int | None:
    marker_path = _completion_marker_path(video_path)
    try:
        expected_size = int(marker_path.read_text(encoding="ascii").strip())
        actual_size = video_path.stat().st_size
    except (OSError, ValueError):
        return None
    if expected_size <= 0 or actual_size != expected_size:
        return None
    return actual_size


def _remove_video_completion_marker(video_path: Path) -> None:
    try:
        _completion_marker_path(video_path).unlink(missing_ok=True)
    except OSError:
        logger.exception("failed to remove share video completion marker path=%s", video_path)


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
    metadata = ShareVideoJobMetadata.from_json(job.metadata_json)
    output = job.output
    succeeded = job.status == MemoirShareVideoJobStatus.SUCCEEDED.value
    if succeeded != (output is not None):
        raise RuntimeError(
            f"share video job/output invariant violated for job {job.id}"
        )
    return MemoirShareVideoJobResponse(
        id=job.id,
        project_id=job.project_id,
        utterance_id=job.utterance_id,
        audio_file_id=job.audio_file_id,
        status=MemoirShareVideoJobStatus(job.status),
        title=share_video_title(metadata, job.selected_text),
        selected_text=job.selected_text,
        start_char=job.start_char,
        end_char=job.end_char,
        start_ms=job.start_ms,
        end_ms=job.end_ms,
        duration_ms=max(0, job.end_ms - job.start_ms),
        video_style=metadata.video_style,
        background_source=metadata.background_source,
        background_style=metadata.background_style,
        background_mode=metadata.background_mode,
        background_effective_mode=metadata.background_effective_mode,
        custom_background_asset_id=metadata.custom_background_asset_id,
        background_scene_count=metadata.background_scene_count,
        background_status=metadata.background_status,
        background_provider=metadata.background_provider,
        background_model=metadata.background_model,
        background_cache_hit=metadata.background_cache_hit,
        background_error_message=metadata.background_error_message,
        output=(
            MemoirShareVideoOutputResponse(
                mime_type=output.mime_type,
                size_bytes=output.size_bytes,
                download_url=f"/api/mobile/memoir/share-video/jobs/{job.id}/download",
                share_code=job.share_code,
            )
            if output is not None
            else None
        ),
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
    settings = get_settings()
    ai_background_enabled = bool(
        settings.memoir_share_video_ai_background_enabled
        and settings.volc_ark_api_key.strip()
    )
    selected_ai_model = select_project_ai_model(
        utterance.voice_session.project,
        req.ai_model,
    )
    project = utterance.voice_session.project
    legacy_background_source = req.video_style is None and req.background_source is not None
    legacy_default = (
        req.video_style is None
        and req.background_source is None
        and "share_video_style" not in (project.metadata_json or {})
    )
    video_style = (
        ("uploaded" if req.background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM else "illustrated")
        if legacy_background_source
        else req.video_style or project_share_video_style(project)
    )
    story_animation = video_style == SHARE_VIDEO_STYLE_STORY_ANIMATION
    alignment = build_share_video_alignment(utterance)
    start_char, end_char = _trim_selection(alignment.transcript_text, req.start_char, req.end_char)
    start_ms, end_ms = share_video_char_range_ms(alignment, start_char, end_char)
    if legacy_background_source:
        selected_background_source = req.background_source
    elif legacy_default:
        selected_background_source = (
            SHARE_VIDEO_BACKGROUND_SOURCE_AI if ai_background_enabled else SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC
        )
    elif story_animation:
        selected_background_source = SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC
    elif video_style == "uploaded":
        selected_background_source = SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM
    else:
        selected_background_source = SHARE_VIDEO_BACKGROUND_SOURCE_AI
    try:
        background_source = normalize_share_video_background_source(selected_background_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_AI and not ai_background_enabled:
        raise HTTPException(status_code=400, detail="回忆画卷当前不可用，请选择自选画面或往事小剧场")
    try:
        background_style = normalize_share_video_background_style(None if story_animation else req.background_style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        background_mode = normalize_share_video_background_mode(None if story_animation else req.background_mode)
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
    custom_background_asset_id: str | None = None
    if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM:
        if not req.custom_background_asset_id:
            raise HTTPException(status_code=400, detail="请选择自定义背景图")
        custom_asset, _custom_path = await load_custom_share_video_background_asset(
            db,
            user_id=user_id,
            project_id=utterance.voice_session.project_id,
            asset_id=req.custom_background_asset_id,
            allow_admin=allow_admin,
        )
        custom_background_asset_id = custom_asset.id
    elif req.custom_background_asset_id and not story_animation:
        raise HTTPException(status_code=400, detail="只有自定义背景图模式可以选择背景图片")

    effective_background_mode = (
        SHARE_VIDEO_BACKGROUND_MODE_SINGLE
        if background_source in {SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM, SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC}
        else background_orchestration.effective_background_mode(background_mode, duration_ms)
    )

    metadata = ShareVideoJobMetadata(
        pipeline_version=SHARE_VIDEO_PIPELINE_VERSION,
        title=fallback_share_video_title(alignment.transcript_text[start_char:end_char]),
        alignment_strategy=alignment.strategy,
        alignment_confidence=alignment.confidence,
        char_timings=alignment.char_timings[start_char:end_char],
        ai_model=selected_ai_model,
        video_style=video_style,
        animation_version=SHARE_VIDEO_STORY_PLAN_VERSION if story_animation else "",
        background_source=background_source,
        background_style=background_style,
        background_mode=background_mode,
        background_effective_mode=effective_background_mode,
        custom_background_asset_id=custom_background_asset_id,
        background_scene_count=1 if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM else 0,
        background_status="disabled" if story_animation else background_orchestration.initial_background_status(
            background_source,
            ai_background_enabled=ai_background_enabled,
        ),
    )

    # The utterance row serializes identical requests so two quick submissions
    # cannot both pass the active-job check and start duplicate video work.
    await db.execute(
        select(MemoirVoiceUtterance.id)
        .where(MemoirVoiceUtterance.id == utterance.id)
        .with_for_update()
    )
    duplicate = await _find_duplicate_or_reusable_share_video_job(
        db,
        project_id=utterance.voice_session.project_id,
        user_id=user_id,
        utterance_id=utterance.id,
        audio_file_id=audio_file.id,
        text_hash=alignment.text_hash,
        start_char=start_char,
        end_char=end_char,
        start_ms=start_ms,
        end_ms=end_ms,
        metadata=metadata,
    )
    if duplicate is not None:
        return _job_response(duplicate)

    job = MemoirMaterialShareVideo(
        project_id=utterance.voice_session.project_id,
        utterance_id=utterance.id,
        audio_file_id=audio_file.id,
        user_id=user_id,
        status=MemoirShareVideoJobStatus.PENDING.value,
        run_after=utcnow_naive(),
        text_source=alignment.text_source,
        text_hash=alignment.text_hash,
        selected_text=alignment.transcript_text[start_char:end_char],
        start_char=start_char,
        end_char=end_char,
        start_ms=start_ms,
        end_ms=end_ms,
        metadata_json=metadata.to_json(),
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


async def delete_share_video_job(
    db: AsyncSession,
    job_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> None:
    job = await _load_owned_job(db, job_id, user_id, allow_admin=allow_admin)
    if job.status != MemoirShareVideoJobStatus.SUCCEEDED.value or not job.output:
        raise HTTPException(status_code=409, detail="只能删除已完成的视频")
    video_path = share_video_file_path(job, job.output)
    # Illustrations remain reusable in the image library after deleting a video.
    await db.execute(
        update(MemoirImageAsset)
        .where(MemoirImageAsset.share_video_job_id == job.id)
        .values(share_video_job_id=None)
    )
    await db.delete(job)
    await db.commit()
    # Never remove the file before the database deletion has succeeded.
    try:
        video_path.unlink(missing_ok=True)
        video_path.with_suffix(".thumbnail.jpg").unlink(missing_ok=True)
    except OSError:
        logger.exception("failed to remove deleted share video file job_id=%s", job_id)
    _remove_video_completion_marker(video_path)


async def list_share_video_jobs(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    limit: int,
    offset: int,
    allow_admin: bool = False,
) -> MemoirShareVideoJobListResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    filters = (
        MemoirMaterialShareVideo.project_id == project_id,
        MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.SUCCEEDED.value,
    )
    completed_ids = (
        select(MemoirMaterialShareVideo.id)
        .join(MemoirShareVideoOutput, MemoirShareVideoOutput.job_id == MemoirMaterialShareVideo.id)
        .where(*filters)
        .order_by(
            func.coalesce(
                MemoirMaterialShareVideo.completed_at,
                MemoirMaterialShareVideo.created_at,
            ).desc(),
            MemoirMaterialShareVideo.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .correlate(None)
    )
    completed_total = (
        select(func.count(MemoirMaterialShareVideo.id))
        .join(MemoirShareVideoOutput, MemoirShareVideoOutput.job_id == MemoirMaterialShareVideo.id)
        .where(*filters)
        .correlate(None)
    )
    active_statuses = (
        MemoirShareVideoJobStatus.PENDING.value,
        MemoirShareVideoJobStatus.RUNNING.value,
    )
    # One snapshot prevents completion between reads from hiding a task in both lists.
    rows = (
        await db.execute(
            select(MemoirMaterialShareVideo, completed_total.scalar_subquery())
            .where(
                MemoirMaterialShareVideo.project_id == project_id,
                or_(
                    MemoirMaterialShareVideo.status.in_((*active_statuses, MemoirShareVideoJobStatus.FAILED.value)),
                    MemoirMaterialShareVideo.id.in_(completed_ids),
                ),
            )
            .order_by(
                func.coalesce(
                    MemoirMaterialShareVideo.completed_at,
                    MemoirMaterialShareVideo.created_at,
                ).desc(),
                MemoirMaterialShareVideo.id.desc(),
            )
        )
    ).all()
    total = rows[0][1] if rows else await db.scalar(completed_total)
    return MemoirShareVideoJobListResponse(
        items=[
            MemoirShareVideoListItemResponse(
                id=job.id,
                utterance_id=job.utterance_id,
                title=share_video_title(ShareVideoJobMetadata.from_json(job.metadata_json), job.selected_text),
                selected_text=job.selected_text,
                duration_ms=max(0, job.end_ms - job.start_ms),
                video_style=ShareVideoJobMetadata.from_json(job.metadata_json).video_style,
                created_at=job.created_at,
                completed_at=job.completed_at,
            )
            for job, _ in rows
            if job.status == MemoirShareVideoJobStatus.SUCCEEDED.value
        ],
        active_jobs=[_job_response(job) for job, _ in rows if job.status in active_statuses],
        failed_jobs=[
            _job_response(job) for job, _ in rows
            if job.status == MemoirShareVideoJobStatus.FAILED.value
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def list_completed_share_video_counts(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
) -> list[MemoirShareVideoCountResponse]:
    jobs = (
        await db.scalars(
            select(MemoirMaterialShareVideo)
            .join(
                MemoirShareVideoOutput,
                MemoirShareVideoOutput.job_id == MemoirMaterialShareVideo.id,
            )
            .where(
                MemoirMaterialShareVideo.project_id == project_id,
                MemoirMaterialShareVideo.user_id == user_id,
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.SUCCEEDED.value,
            )
            .order_by(
                MemoirMaterialShareVideo.utterance_id,
                MemoirMaterialShareVideo.created_at.desc(),
                MemoirMaterialShareVideo.id,
            )
            .options(
                selectinload(MemoirMaterialShareVideo.utterance)
                .selectinload(MemoirVoiceUtterance.audio_file)
            )
        )
    ).all()
    job_ids_by_utterance: dict[str, list[str]] = {}
    job_titles_by_utterance: dict[str, dict[str, str]] = {}
    current_job_ids_by_utterance: dict[str, list[str]] = {}
    current_text_by_utterance: dict[str, tuple[str, str] | None] = {}
    for job in jobs:
        job_ids_by_utterance.setdefault(job.utterance_id, []).append(job.id)
        job_titles_by_utterance.setdefault(job.utterance_id, {})[job.id] = share_video_title(
            ShareVideoJobMetadata.from_json(job.metadata_json), job.selected_text,
        )
        if job.utterance_id not in current_text_by_utterance:
            current_text_by_utterance[job.utterance_id] = current_share_video_text_and_hash(job.utterance)
        current = current_text_by_utterance[job.utterance_id]
        if current is None or job.text_hash != current[1]:
            continue
        if job.audio_file_id != job.utterance.audio_file_id:
            continue
        if job.selected_text != current[0][job.start_char:job.end_char]:
            continue
        current_job_ids_by_utterance.setdefault(job.utterance_id, []).append(job.id)
    return [
        MemoirShareVideoCountResponse(
            utterance_id=utterance_id,
            count=len(job_ids),
            job_ids=job_ids,
            job_titles=job_titles_by_utterance[utterance_id],
            current_job_ids=current_job_ids_by_utterance.get(utterance_id, []),
        )
        for utterance_id, job_ids in job_ids_by_utterance.items()
    ]


async def load_completed_share_video_job(
    db: AsyncSession,
    job_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> tuple[MemoirShareVideoOutput, Path, str]:
    job = await _load_owned_job(db, job_id, user_id, allow_admin=allow_admin)
    if job.status != MemoirShareVideoJobStatus.SUCCEEDED.value or job.output is None:
        raise HTTPException(status_code=409, detail="分享视频还没有生成完成")
    return completed_share_video_file(job)


def completed_share_video_file(job: MemoirMaterialShareVideo) -> tuple[MemoirShareVideoOutput, Path, str]:
    output = job.output
    if output is None:
        raise HTTPException(status_code=409, detail="分享视频还没有生成完成")
    path = share_video_file_path(job, output)
    if not path.exists():
        raise HTTPException(status_code=404, detail="分享视频文件不存在")
    return output, path, share_video_title(ShareVideoJobMetadata.from_json(job.metadata_json), job.selected_text)


def _claimable_share_video_job(now: datetime, max_attempts: int):
    attempts_available = MemoirMaterialShareVideo.attempts < max(1, max_attempts)
    return and_(
        attempts_available,
        or_(
            and_(
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.PENDING.value,
                or_(
                    MemoirMaterialShareVideo.run_after.is_(None),
                    MemoirMaterialShareVideo.run_after <= now,
                ),
            ),
            and_(
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.RUNNING.value,
                or_(
                    MemoirMaterialShareVideo.lease_expires_at.is_(None),
                    MemoirMaterialShareVideo.lease_expires_at <= now,
                ),
            ),
        ),
    )


def _leased_share_video_job(lease: JobLease):
    return and_(
        MemoirMaterialShareVideo.id == lease.job_id,
        MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.RUNNING.value,
        MemoirMaterialShareVideo.lease_token == lease.token,
    )


async def _fail_exhausted_share_video_jobs(
    db: AsyncSession,
    *,
    now: datetime,
    max_attempts: int,
) -> None:
    use_skip_locked = db.get_bind().dialect.name == "postgresql"
    statement = select(MemoirMaterialShareVideo).where(
        MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.RUNNING.value,
        MemoirMaterialShareVideo.attempts >= max(1, max_attempts),
        or_(
            MemoirMaterialShareVideo.lease_expires_at.is_(None),
            MemoirMaterialShareVideo.lease_expires_at <= now,
        ),
    )
    if use_skip_locked:
        statement = statement.with_for_update(
            skip_locked=True,
            of=MemoirMaterialShareVideo,
        )
    exhausted = (
        await db.scalars(statement)
    ).all()
    if not use_skip_locked:
        await db.commit()
    for job in exhausted:
        result = await db.execute(
            update(MemoirMaterialShareVideo)
            .where(
                MemoirMaterialShareVideo.id == job.id,
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.RUNNING.value,
                MemoirMaterialShareVideo.attempts >= max(1, max_attempts),
                or_(
                    MemoirMaterialShareVideo.lease_expires_at.is_(None),
                    MemoirMaterialShareVideo.lease_expires_at <= now,
                ),
            )
            .values(
                status=MemoirShareVideoJobStatus.FAILED.value,
                locked_at=None,
                lease_token=None,
                lease_expires_at=None,
                error_message="分享视频任务执行中断，已达到最大重试次数",
                completed_at=now,
            )
        )
        if result.rowcount == 1:
            try:
                target_path = share_video_file_path(job)
                target_path.unlink(missing_ok=True)
                _completion_marker_path(target_path).unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to remove abandoned share video job_id=%s", job.id)
    await db.commit()


async def _claim_share_video_job_ids(
    db: AsyncSession,
    job_ids: list[str],
    *,
    now: datetime,
    lease_seconds: int,
    max_attempts: int,
) -> list[JobLease]:
    claimable = _claimable_share_video_job(now, max_attempts)
    leases: list[JobLease] = []
    for job_id in job_ids:
        lease = new_job_lease(job_id)
        result = await db.execute(
            update(MemoirMaterialShareVideo)
            .where(MemoirMaterialShareVideo.id == job_id, claimable)
            .values(
                status=MemoirShareVideoJobStatus.RUNNING.value,
                run_after=None,
                locked_at=now,
                lease_token=lease.token,
                lease_expires_at=lease_deadline(now, lease_seconds),
                attempts=MemoirMaterialShareVideo.attempts + 1,
                error_message="",
                completed_at=None,
            )
        )
        if result.rowcount == 1:
            leases.append(lease)
    await db.commit()
    return leases


async def claim_due_share_video_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int | None = None,
    lease_seconds: int | None = None,
    max_attempts: int | None = None,
) -> list[JobLease]:
    settings = get_settings()
    claimed_at = now or utcnow_naive()
    batch_size = limit or max(1, settings.memoir_share_video_batch_size)
    lease_duration = lease_seconds or max(1, settings.memoir_share_video_lease_seconds)
    attempt_limit = max_attempts or max(1, settings.memoir_share_video_max_attempts)
    await _fail_exhausted_share_video_jobs(
        db,
        now=claimed_at,
        max_attempts=attempt_limit,
    )
    use_skip_locked = db.get_bind().dialect.name == "postgresql"
    statement = (
        select(MemoirMaterialShareVideo.id)
        .where(_claimable_share_video_job(claimed_at, attempt_limit))
        .order_by(
            MemoirMaterialShareVideo.run_after,
            MemoirMaterialShareVideo.created_at,
        )
        .limit(batch_size)
    )
    if use_skip_locked:
        statement = statement.with_for_update(skip_locked=True)
    candidate_ids = (await db.scalars(statement)).all()
    if not use_skip_locked:
        await db.commit()
    return await _claim_share_video_job_ids(
        db,
        candidate_ids,
        now=claimed_at,
        lease_seconds=lease_duration,
        max_attempts=attempt_limit,
    )


async def claim_share_video_job(
    db: AsyncSession,
    job_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int | None = None,
    max_attempts: int | None = None,
) -> JobLease | None:
    settings = get_settings()
    claimed_at = now or utcnow_naive()
    lease_duration = lease_seconds or max(1, settings.memoir_share_video_lease_seconds)
    attempt_limit = max_attempts or max(1, settings.memoir_share_video_max_attempts)
    await db.commit()
    leases = await _claim_share_video_job_ids(
        db,
        [job_id],
        now=claimed_at,
        lease_seconds=lease_duration,
        max_attempts=attempt_limit,
    )
    return leases[0] if leases else None


async def renew_share_video_job_lease(
    db: AsyncSession,
    lease: JobLease,
    *,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> bool:
    settings = get_settings()
    renewed_at = now or utcnow_naive()
    lease_duration = lease_seconds or max(1, settings.memoir_share_video_lease_seconds)
    result = await db.execute(
        update(MemoirMaterialShareVideo)
        .where(_leased_share_video_job(lease))
        .values(lease_expires_at=lease_deadline(renewed_at, lease_duration))
    )
    await db.commit()
    return result.rowcount == 1


async def _commit_share_video_progress(
    db: AsyncSession,
    lease: JobLease,
    *,
    lease_seconds: int,
) -> None:
    with db.no_autoflush:
        result = await db.execute(
            update(MemoirMaterialShareVideo)
            .where(_leased_share_video_job(lease))
            .values(lease_expires_at=lease_deadline(utcnow_naive(), lease_seconds))
        )
    if result.rowcount != 1:
        await db.rollback()
        raise ShareVideoLeaseLostError("share video job lease lost")
    await db.commit()


async def _complete_share_video_job(
    db: AsyncSession,
    lease: JobLease,
    *,
    storage_path: str,
    size_bytes: int,
) -> bool:
    completed_at = utcnow_naive()
    result = await db.execute(
        update(MemoirMaterialShareVideo)
        .where(_leased_share_video_job(lease))
        .values(
            status=MemoirShareVideoJobStatus.SUCCEEDED.value,
            run_after=None,
            locked_at=None,
            lease_token=None,
            lease_expires_at=None,
            error_message="",
            completed_at=completed_at,
        )
        .returning(MemoirMaterialShareVideo.project_id)
    )
    project_id = result.scalar_one_or_none()
    if project_id is None:
        await db.rollback()
        return False
    db.add(
        MemoirShareVideoOutput(
            job_id=lease.job_id,
            project_id=project_id,
            storage_path=storage_path,
            mime_type=_VIDEO_MIME_TYPE,
            size_bytes=size_bytes,
            created_at=completed_at,
        )
    )
    await db.commit()
    return True


async def _recover_published_share_video(
    session_maker: async_sessionmaker[AsyncSession],
    lease: JobLease,
) -> bool:
    async with session_maker() as db:
        job = await db.get(MemoirMaterialShareVideo, lease.job_id)
        if job is None:
            return False
        if job.status == MemoirShareVideoJobStatus.SUCCEEDED.value and job.output is not None:
            _remove_video_completion_marker(share_video_file_path(job, job.output))
            return True
        if (
            job.status != MemoirShareVideoJobStatus.RUNNING.value
            or job.lease_token != lease.token
        ):
            return False
        target_path = share_video_file_path(job)
        size_bytes = _published_video_size(target_path)
        if size_bytes is None:
            return False
        completed = await _complete_share_video_job(
            db,
            lease,
            storage_path=_storage_path(job.user_id, job.id),
            size_bytes=size_bytes,
        )
    if completed:
        _remove_video_completion_marker(target_path)
        logger.warning("recovered published share video job_id=%s", lease.job_id)
    return completed


async def _wait_for_published_share_video(
    session_maker: async_sessionmaker[AsyncSession],
    lease: JobLease,
    *,
    poll_seconds: float,
) -> None:
    async with session_maker() as db:
        job = await db.scalar(
            select(MemoirMaterialShareVideo).where(_leased_share_video_job(lease))
        )
        target_path = share_video_file_path(job) if job is not None else None
    if target_path is None:
        await asyncio.Event().wait()
    interval = max(0.01, poll_seconds)
    while _published_video_size(target_path) is None:
        await asyncio.sleep(interval)


async def _fail_share_video_job(
    db: AsyncSession,
    lease: JobLease,
    *,
    target_path: Path,
    error: Exception,
    max_attempts: int,
    retry_seconds: int,
    metadata: ShareVideoJobMetadata | None = None,
) -> None:
    job = await db.scalar(
        select(MemoirMaterialShareVideo).where(_leased_share_video_job(lease))
    )
    if job is None:
        return
    failed_at = utcnow_naive()
    should_retry = job.attempts < max(1, max_attempts)
    retry_delay = min(300, max(1, retry_seconds) * (2 ** max(0, job.attempts - 1)))
    next_attempt_at = failed_at + timedelta(seconds=retry_delay) if should_retry else None
    status = (
        MemoirShareVideoJobStatus.PENDING.value
        if should_retry
        else MemoirShareVideoJobStatus.FAILED.value
    )
    error_message = " ".join(str(error or "").split())[:1000] or "分享视频生成失败"
    with db.no_autoflush:
        result = await db.execute(
            update(MemoirMaterialShareVideo)
            .where(_leased_share_video_job(lease))
            .values(
                status=status,
                run_after=next_attempt_at,
                locked_at=None,
                lease_token=None,
                lease_expires_at=None,
                error_message=error_message,
                **({"metadata_json": metadata.to_json()} if metadata else {}),
                completed_at=None if should_retry else failed_at,
            )
        )
    if result.rowcount != 1:
        await db.rollback()
        return
    try:
        target_path.unlink(missing_ok=True)
        _completion_marker_path(target_path).unlink(missing_ok=True)
    except OSError:
        logger.exception("failed to remove partial share video job_id=%s", lease.job_id)
    await db.commit()


async def run_share_video_job_with_session(
    db: AsyncSession,
    lease: JobLease,
    *,
    lease_seconds: int | None = None,
    max_attempts: int | None = None,
    retry_seconds: int | None = None,
) -> bool:
    settings = get_settings()
    lease_duration = lease_seconds or max(1, settings.memoir_share_video_lease_seconds)
    attempt_limit = max_attempts or max(1, settings.memoir_share_video_max_attempts)
    retry_delay = retry_seconds or max(1, settings.memoir_share_video_retry_seconds)
    job = await db.scalar(
        select(MemoirMaterialShareVideo)
        .where(_leased_share_video_job(lease))
        .options(
            selectinload(MemoirMaterialShareVideo.audio_file),
            selectinload(MemoirMaterialShareVideo.utterance),
        )
    )
    if job is None:
        return False
    target_path = share_video_file_path(job)
    try:
        metadata = ShareVideoJobMetadata.from_json(job.metadata_json)
        usage_context = AIUsageContext(
            operation_id=str(uuid4()),
            operation="memoir.share_video_generation",
            user_id=job.user_id,
            project_id=job.project_id,
        )

        if not metadata.title_attempted:
            metadata.title = share_video_title(metadata, job.selected_text)
            context_token = set_ai_usage_context(
                user_id=job.user_id,
                project_id=job.project_id,
                operation="memoir.share_video_title",
                operation_id=usage_context.operation_id,
            )
            try:
                suggested_title, metadata.hook_text = await _suggest_share_video_title_and_hook(
                    job.selected_text, ai_model=metadata.ai_model,
                )
                if suggested_title:
                    metadata.title = suggested_title
            except Exception:
                logger.exception("share video title generation failed job_id=%s", job.id)
            finally:
                reset_ai_usage_context(context_token)
            metadata.title_attempted = True
            _set_job_metadata(job, metadata)
            await _commit_share_video_progress(db, lease, lease_seconds=lease_duration)

        async def persist_background_progress(updated_metadata: ShareVideoJobMetadata) -> None:
            _set_job_metadata(job, updated_metadata)
            await _commit_share_video_progress(db, lease, lease_seconds=lease_duration)

        background_image_path = None
        background_segments = None
        if metadata.video_style == SHARE_VIDEO_STYLE_STORY_ANIMATION:
            duration_ms = job.end_ms - job.start_ms
            if metadata.animation_plan is None:
                relative_timings = [
                    {
                        "start_ms": max(0, min(duration_ms, timing.start_ms - job.start_ms)),
                        "end_ms": max(0, min(duration_ms, timing.end_ms - job.start_ms)),
                    }
                    for timing in metadata.char_timings
                ]
                metadata.animation_plan = await plan_share_video_story(
                    job.selected_text,
                    relative_timings,
                    duration_ms,
                    ai_model=metadata.ai_model,
                    usage_context=usage_context,
                )
                _set_job_metadata(job, metadata)
                await _commit_share_video_progress(db, lease, lease_seconds=lease_duration)
            validate_story_plan_source(metadata.animation_plan, job.selected_text, duration_ms)
        else:
            prepared_background = await background_orchestration.prepare_share_video_background(
                db,
                job=job,
                metadata=metadata,
                persist_progress=persist_background_progress,
                ai_background_enabled=settings.memoir_share_video_ai_background_enabled,
                volc_ark_api_key=str(getattr(settings, "volc_ark_api_key", "") or ""),
                usage_context=usage_context,
            )
            background_image_path = prepared_background.image_path
            background_segments = prepared_background.segments
        target_path = share_video_file_path(job)
        book_title, narrator_name = await share_video_end_card_source(db, job.project_id)
        mini_program_code = await _share_video_mini_program_code(job.share_code)
        render_request = rendering.ShareVideoRenderRequest(
            source_audio_path=audio_file_path(job.audio_file),
            source_audio_mime_type=job.audio_file.mime_type,
            target_path=target_path,
            selected_text=job.selected_text,
            char_timings=metadata.char_timing_dicts(),
            start_ms=job.start_ms,
            end_ms=job.end_ms,
            background_image_path=background_image_path,
            background_segments=background_segments,
            video_style=metadata.video_style,
            animation_plan=metadata.animation_plan,
            hook_text=metadata.hook_text,
            book_title=book_title,
            narrator_name=narrator_name,
            mini_program_code=mini_program_code,
        )
        try:
            await rendering.render_share_video_clip(render_request)
        except rendering.ShareVideoGenerationError as exc:
            if metadata.video_style == SHARE_VIDEO_STYLE_STORY_ANIMATION or (
                background_image_path is None and not background_segments
            ):
                raise
            metadata.background_status = "fallback"
            metadata.background_error_message = f"背景图渲染失败，已回退经典样式：{str(exc)[:400]}"
            _set_job_metadata(job, metadata)
            await _commit_share_video_progress(db, lease, lease_seconds=lease_duration)
            await rendering.render_share_video_clip(
                replace(render_request, background_image_path=None, background_segments=None, video_style="illustrated"),
            )
        size_bytes = _publish_completed_video(target_path)
        completed = await _complete_share_video_job(
            db,
            lease,
            storage_path=_storage_path(job.user_id, job.id),
            size_bytes=size_bytes,
        )
        if completed:
            _remove_video_completion_marker(target_path)
        return completed
    except ShareVideoLeaseLostError:
        await db.rollback()
        logger.warning("share video job lease lost job_id=%s", lease.job_id)
        return False
    except Exception as exc:
        await db.rollback()
        logger.exception("share video job failed job_id=%s", lease.job_id)
        await _fail_share_video_job(
            db,
            lease,
            target_path=target_path,
            error=exc,
            max_attempts=attempt_limit,
            retry_seconds=retry_delay,
        )
        return False


async def run_share_video_job(
    lease: JobLease,
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    completion_recovery_poll_seconds: float = _COMPLETION_RECOVERY_POLL_SECONDS,
    timeout_seconds: float | None = None,
) -> bool:
    settings = get_settings()
    lease_seconds = max(1, settings.memoir_share_video_lease_seconds)
    heartbeat_seconds = min(
        max(1, settings.memoir_share_video_heartbeat_seconds),
        max(1, lease_seconds // 3),
    )
    deadline_seconds = max(
        0.01,
        timeout_seconds
        if timeout_seconds is not None
        else settings.memoir_share_video_job_deadline_seconds,
    )

    async def renew() -> bool:
        try:
            async with session_maker() as heartbeat_db:
                return await renew_share_video_job_lease(
                    heartbeat_db,
                    lease,
                    lease_seconds=lease_seconds,
                )
        except Exception:
            logger.exception("share video job lease heartbeat failed job_id=%s", lease.job_id)
            return False

    async def work() -> bool:
        async with session_maker() as db:
            return await run_share_video_job_with_session(
                db,
                lease,
                lease_seconds=lease_seconds,
                max_attempts=max(1, settings.memoir_share_video_max_attempts),
                retry_seconds=max(1, settings.memoir_share_video_retry_seconds),
            )

    try:
        if await _recover_published_share_video(session_maker, lease):
            return True
    except Exception:
        logger.exception("share video preflight completion recovery failed job_id=%s", lease.job_id)

    work_task = asyncio.create_task(run_with_lease_heartbeat(
        work,
        renew,
        interval_seconds=heartbeat_seconds,
    ))
    recovery_task = asyncio.create_task(_wait_for_published_share_video(
        session_maker,
        lease,
        poll_seconds=completion_recovery_poll_seconds,
    ))
    try:
        done, _pending = await asyncio.wait(
            {work_task, recovery_task},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=deadline_seconds,
        )
        if not done:
            work_task.cancel()
            recovery_task.cancel()
            await asyncio.gather(work_task, recovery_task, return_exceptions=True)
            if await _recover_published_share_video(session_maker, lease):
                return True
            error_message = (
                f"分享视频生成超过 {max(1, round(deadline_seconds / 60))} 分钟，"
                "已停止，请重新生成。"
            )
            logger.warning(
                "share video job deadline exceeded job_id=%s timeout_seconds=%s",
                lease.job_id,
                deadline_seconds,
            )
            async with session_maker() as db:
                job = await db.scalar(
                    select(MemoirMaterialShareVideo).where(
                        _leased_share_video_job(lease)
                    )
                )
                if job is not None:
                    metadata = ShareVideoJobMetadata.from_json(job.metadata_json)
                    if metadata.background_status in {"pending", "planning", "generating"}:
                        metadata.background_status = "failed"
                        metadata.background_error_message = error_message
                    metadata.error_type = "job_deadline_exceeded"
                    metadata.timeout_seconds = deadline_seconds
                    metadata.elapsed_seconds = deadline_seconds
                    await _fail_share_video_job(
                        db,
                        lease,
                        target_path=share_video_file_path(job),
                        error=TimeoutError(error_message),
                        max_attempts=1,
                        retry_seconds=max(1, settings.memoir_share_video_retry_seconds),
                        metadata=metadata,
                    )
            return False
        if recovery_task in done:
            await recovery_task
            work_task.cancel()
            await asyncio.gather(work_task, return_exceptions=True)
            return await _recover_published_share_video(session_maker, lease)
        return await work_task
    except JobLeaseLostError:
        logger.warning("share video job lease lost job_id=%s", lease.job_id)
        return False
    except Exception:
        logger.exception("share video job worker failed job_id=%s", lease.job_id)
        return False
    finally:
        recovery_task.cancel()
        if not work_task.done():
            work_task.cancel()
        await asyncio.gather(recovery_task, work_task, return_exceptions=True)


async def run_due_share_video_jobs(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    limit: int | None = None,
) -> int:
    async with session_maker() as db:
        leases = await claim_due_share_video_jobs(db, limit=limit)
    results = await asyncio.gather(
        *(run_share_video_job(lease, session_maker=session_maker) for lease in leases)
    )
    return sum(results)


async def share_video_worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    poll_seconds = max(1, settings.memoir_share_video_poll_seconds)
    while not stop_event.is_set():
        try:
            await run_due_share_video_jobs()
        except Exception:
            logger.exception("share video worker tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
