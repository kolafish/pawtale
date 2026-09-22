from __future__ import annotations

import asyncio
from hashlib import sha256
import logging
import mimetypes
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import DATA_DIR, get_settings
from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirImageAssetSourceType,
    MemoirImageAssetStatus,
)
from app.models.memoir import MemoirEditingJob, MemoirImageAsset
from app.models.projects import Project
from app.schemas.memoir_images import (
    MemoirMaterialImageGenerationJobResponse,
    MemoirMaterialImageGenerationRequest,
)
from app.services.memoir import editing_jobs
from app.services.ai_costs import estimate_image_cost
from app.services.memoir.image_assets import AI_GENERATED_IMAGE_DISCLOSURE
from app.services.memoir.share_video_backgrounds import (
    DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE,
    ShareVideoBackgroundGenerationError,
    VolcArkImageGenerationService,
    normalize_share_video_background_style,
    share_video_background_style,
)
from app.services.memoir.share_video_alignment import build_share_video_alignment
from app.services.memoir.voice_transcripts import _load_owned_utterance


logger = logging.getLogger(__name__)

MATERIAL_IMAGE_PROMPT_VERSION = "memoir-material-illustration-v2"
MATERIAL_IMAGE_CONTENT_MAX_CHARS = 500
MATERIAL_IMAGE_DIR = Path("memoir") / "material-images"


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _prompt_content(selected_text: str) -> str:
    normalized = _normalized_text(selected_text)
    characters = list(normalized)
    if len(characters) <= MATERIAL_IMAGE_CONTENT_MAX_CHARS:
        return normalized
    return "".join(characters[:MATERIAL_IMAGE_CONTENT_MAX_CHARS]).rstrip() + "..."


def build_material_image_prompt(selected_text: str, style_id: str | None) -> tuple[str, str, str]:
    normalized_style = normalize_share_video_background_style(
        style_id or DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE
    )
    content = _prompt_content(selected_text)
    style = share_video_background_style(normalized_style)
    prompt = f"""根据以下老人口述内容，创作一张适合放进家庭回忆录正文的横版绘画插图：

{content}

请理解原文所表达的场景、人物关系和整体氛围，并让画面的光线、色彩与情绪自然贴合原文。

绘画风格：
{style.drawing_instruction}

成书插图构图：
- 从原文中选择一个最有代表性、最适合视觉表达的场景。
- 构图完整，主体明确，人物关系和重要物件自然可信，适合在回忆录正文中独立阅读和印刷。
- 不把多个时间、多个地点或多个事件拼贴在同一张图中。
- 不制作多格漫画、时间轴或蒙太奇。

生成边界：
- 这是基于记忆的意象创作，不冒充真实历史照片；人物避免清晰正脸特写和可识别肖像。
- 不要添加口述中没有依据的重大人物、事件或时代符号。
- 不要出现任何文字、字幕、题字、印章、水印、logo 或现代 UI 元素。
- 避免畸形人物、塑料质感、过度锐化、过度饱和、夸张电影光效和泛泛的 AI 插画感。""".strip()
    prompt_hash = sha256(
        "\n".join((MATERIAL_IMAGE_PROMPT_VERSION, normalized_style, prompt)).encode("utf-8")
    ).hexdigest()
    return normalized_style, prompt, prompt_hash


def _source_signature(
    *,
    utterance_id: str,
    transcript_text_hash: str,
    start_char: int,
    end_char: int,
    style_id: str,
) -> str:
    payload = "\u241f".join(
        (
            MemoirImageAssetSourceType.UTTERANCE_ILLUSTRATION.value,
            utterance_id,
            transcript_text_hash,
            str(start_char),
            str(end_char),
            style_id,
            MATERIAL_IMAGE_PROMPT_VERSION,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _selection(transcript_text: str, start_char: int, end_char: int) -> tuple[int, int, str]:
    if start_char < 0 or end_char > len(transcript_text) or end_char <= start_char:
        raise HTTPException(status_code=400, detail="选择的文字范围无效")
    while start_char < end_char and transcript_text[start_char].isspace():
        start_char += 1
    while end_char > start_char and transcript_text[end_char - 1].isspace():
        end_char -= 1
    selected_text = transcript_text[start_char:end_char].strip()
    if not selected_text:
        raise HTTPException(status_code=400, detail="请选择要生成图片的口述内容")
    return start_char, end_char, selected_text


def _asset_title(selected_text: str) -> str:
    normalized = _normalized_text(selected_text)
    for index, character in enumerate(normalized):
        if character in "。！？!?":
            normalized = normalized[: index + 1]
            break
    if len(normalized) > 24:
        normalized = normalized[:24].rstrip() + "..."
    return normalized or "讲述插图"


def _job_response(
    job: MemoirEditingJob,
    asset: MemoirImageAsset,
) -> MemoirMaterialImageGenerationJobResponse:
    result = job.result_json if isinstance(job.result_json, dict) else {}
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    return MemoirMaterialImageGenerationJobResponse(
        job_id=job.id,
        project_id=asset.project_id,
        utterance_id=asset.source_utterance_id or "",
        asset_id=asset.id,
        status=MemoirEditingJobStatus(job.status),
        asset_status=MemoirImageAssetStatus(asset.status),
        stage=str(result.get("stage") or metadata.get("generation_stage") or "queued"),
        selected_text=asset.source_quote,
        start_char=int(metadata.get("start_char") or 0),
        end_char=int(metadata.get("end_char") or 0),
        style_id=asset.style_id,
        error_message=job.error_message if job.status == MemoirEditingJobStatus.FAILED.value else "",
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def _job_for_asset(
    db: AsyncSession,
    asset: MemoirImageAsset,
) -> MemoirEditingJob | None:
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    job_id = str(metadata.get("generation_job_id") or "").strip()
    if not job_id:
        return None
    return await db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.id == job_id,
            MemoirEditingJob.project_id == asset.project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_MATERIAL_IMAGE.value,
        )
    )


async def create_material_image_generation_job(
    db: AsyncSession,
    *,
    utterance_id: str,
    user_id: str,
    req: MemoirMaterialImageGenerationRequest,
    allow_admin: bool = False,
) -> MemoirMaterialImageGenerationJobResponse:
    utterance = await _load_owned_utterance(
        db,
        utterance_id,
        user_id,
        allow_admin=allow_admin,
    )
    alignment = build_share_video_alignment(utterance)
    transcript_text = alignment.transcript_text
    text_source = alignment.text_source
    start_char, end_char, selected_text = _selection(
        transcript_text,
        req.start_char,
        req.end_char,
    )
    try:
        style_id = normalize_share_video_background_style(
            req.style_id or DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    transcript_text_hash = alignment.text_hash
    source_signature = _source_signature(
        utterance_id=utterance.id,
        transcript_text_hash=transcript_text_hash,
        start_char=start_char,
        end_char=end_char,
        style_id=style_id,
    )
    asset = await db.scalar(
        select(MemoirImageAsset).where(
            MemoirImageAsset.project_id == utterance.voice_session.project_id,
            MemoirImageAsset.source_signature == source_signature,
        )
    )
    if asset is None:
        candidate = MemoirImageAsset(
            project_id=utterance.voice_session.project_id,
            user_id=user_id,
            status=MemoirImageAssetStatus.PENDING.value,
            source_type=MemoirImageAssetSourceType.UTTERANCE_ILLUSTRATION.value,
            source_signature=source_signature,
            title=_asset_title(selected_text),
            caption=selected_text[:500],
            alt_text=_asset_title(selected_text),
            style_id=style_id,
            source_utterance_id=utterance.id,
            source_quote=selected_text,
            metadata_json={
                "disclosure": AI_GENERATED_IMAGE_DISCLOSURE,
                "generation_stage": "queued",
                "transcript_text_hash": transcript_text_hash,
                "text_source": text_source,
                "start_char": start_char,
                "end_char": end_char,
            },
        )
        try:
            async with db.begin_nested():
                db.add(candidate)
                await db.flush()
            asset = candidate
        except IntegrityError:
            asset = await db.scalar(
                select(MemoirImageAsset).where(
                    MemoirImageAsset.project_id == utterance.voice_session.project_id,
                    MemoirImageAsset.source_signature == source_signature,
                )
            )
            if asset is None:
                raise

    existing_job = await _job_for_asset(db, asset)
    if (
        existing_job is not None
        and asset.status
        in {
            MemoirImageAssetStatus.PENDING.value,
            MemoirImageAssetStatus.RUNNING.value,
            MemoirImageAssetStatus.READY.value,
        }
    ):
        return _job_response(existing_job, asset)

    asset.status = MemoirImageAssetStatus.PENDING.value
    asset.storage_path = ""
    asset.thumbnail_storage_path = ""
    asset.mime_type = ""
    asset.size_bytes = None
    asset.width = None
    asset.height = None
    asset.provider = ""
    asset.model = ""
    asset.prompt_hash = ""
    asset.prompt_version = ""
    asset.metadata_json = {
        **(asset.metadata_json or {}),
        "generation_stage": "queued",
        "generation_error_type": "",
        "generation_error_message": "",
    }
    flag_modified(asset, "metadata_json")
    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=asset.project_id,
        job_type=MemoirEditingJobType.GENERATE_MATERIAL_IMAGE,
        payload_json={
            "asset_id": asset.id,
            "utterance_id": utterance.id,
        },
        dedupe_key=f"material-image:{asset.id}",
        replace_completed=True,
    )
    asset.metadata_json = {
        **(asset.metadata_json or {}),
        "generation_job_id": job.id,
    }
    flag_modified(asset, "metadata_json")
    await db.commit()
    await db.refresh(job)
    await db.refresh(asset)
    return _job_response(job, asset)


async def _load_owned_generation_job(
    db: AsyncSession,
    *,
    job_id: str,
    user_id: str,
    allow_admin: bool,
) -> tuple[MemoirEditingJob, MemoirImageAsset]:
    filters = [
        MemoirEditingJob.id == job_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_MATERIAL_IMAGE.value,
    ]
    if not allow_admin:
        filters.append(Project.user_id == user_id)
    job = await db.scalar(
        select(MemoirEditingJob)
        .join(Project, Project.id == MemoirEditingJob.project_id)
        .where(*filters)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="图片生成任务不存在")
    asset_id = str((job.payload_json or {}).get("asset_id") or "")
    asset = await db.scalar(
        select(MemoirImageAsset).where(
            MemoirImageAsset.id == asset_id,
            MemoirImageAsset.project_id == job.project_id,
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="图片素材不存在")
    return job, asset


async def get_material_image_generation_job(
    db: AsyncSession,
    *,
    job_id: str,
    user_id: str,
    allow_admin: bool = False,
) -> MemoirMaterialImageGenerationJobResponse:
    job, asset = await _load_owned_generation_job(
        db,
        job_id=job_id,
        user_id=user_id,
        allow_admin=allow_admin,
    )
    return _job_response(job, asset)


def _storage_path(asset: MemoirImageAsset, extension: str) -> str:
    return f"{MATERIAL_IMAGE_DIR.as_posix()}/{asset.project_id}/{asset.id}.{extension}"


def _file_path(storage_path: str) -> Path:
    return DATA_DIR / storage_path


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError):
        return None, None


async def _mark_asset_failed(
    db: AsyncSession,
    *,
    asset: MemoirImageAsset,
    error_type: str,
    error_message: str,
    elapsed_seconds: float,
) -> None:
    asset.status = MemoirImageAssetStatus.FAILED.value
    asset.metadata_json = {
        **(asset.metadata_json or {}),
        "generation_stage": "failed",
        "generation_error_type": error_type,
        "generation_error_message": error_message[:500],
        "generation_elapsed_seconds": round(elapsed_seconds, 3),
    }
    flag_modified(asset, "metadata_json")
    await db.commit()


async def run_material_image_generation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> editing_jobs.EditingJobResult:
    asset_id = str((job.payload_json or {}).get("asset_id") or "")
    asset = await db.scalar(
        select(MemoirImageAsset).where(
            MemoirImageAsset.id == asset_id,
            MemoirImageAsset.project_id == job.project_id,
            MemoirImageAsset.source_type
            == MemoirImageAssetSourceType.UTTERANCE_ILLUSTRATION.value,
        )
    )
    if asset is None:
        return editing_jobs.EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={"stage": "failed", "asset_id": asset_id},
            error_message="图片素材不存在",
        )
    started_at = perf_counter()
    settings = get_settings()
    try:
        style_id, prompt, prompt_hash = build_material_image_prompt(
            asset.source_quote,
            asset.style_id,
        )
        extension = (settings.volc_ark_image_output_format.strip().lower() or "png").lstrip(".")
        storage_path = _storage_path(asset, extension)
        path = _file_path(storage_path)
        if job.attempts > 1 and not (path.exists() and path.stat().st_size > 0):
            error_message = "上次图片生成结果未知，为避免重复付费，请手动重试"
            elapsed_seconds = perf_counter() - started_at
            await _mark_asset_failed(
                db,
                asset=asset,
                error_type="unknown_previous_result",
                error_message=error_message,
                elapsed_seconds=elapsed_seconds,
            )
            return editing_jobs.EditingJobResult(
                status=MemoirEditingJobStatus.FAILED,
                result_json={
                    "stage": "failed",
                    "asset_id": asset.id,
                    "error_type": "unknown_previous_result",
                    "attempts": job.attempts,
                },
                error_message=error_message,
            )
        asset.status = MemoirImageAssetStatus.RUNNING.value
        asset.style_id = style_id
        asset.prompt_hash = prompt_hash
        asset.prompt_version = MATERIAL_IMAGE_PROMPT_VERSION
        asset.provider = VolcArkImageGenerationService.provider_name
        asset.model = settings.volc_ark_image_model.strip()
        asset.metadata_json = {
            **(asset.metadata_json or {}),
            "generation_stage": "generating",
            "generation_requested_model": settings.volc_ark_image_model.strip(),
            "generation_provider": VolcArkImageGenerationService.provider_name,
            "generation_timeout_seconds": settings.volc_ark_image_timeout_seconds,
        }
        flag_modified(asset, "metadata_json")
        await db.commit()

        cache_hit = path.exists() and path.stat().st_size > 0
        resolved_model = asset.model
        usage: dict[str, object] = {}
        estimated_cost: dict[str, object] = {}
        mime_type = mimetypes.guess_type(path.name)[0] or f"image/{extension}"
        if not cache_hit:
            image = await VolcArkImageGenerationService().generate(prompt)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            try:
                temporary_path.write_bytes(image.content)
                temporary_path.replace(path)
            finally:
                temporary_path.unlink(missing_ok=True)
            resolved_model = image.model
            usage = image.usage
            mime_type = image.mime_type or mime_type
            estimated_cost = estimate_image_cost(
                provider=VolcArkImageGenerationService.provider_name,
                model=resolved_model,
                image_count=1,
            ).as_dict()

        width, height = _image_dimensions(path)
        elapsed_seconds = perf_counter() - started_at
        asset.status = MemoirImageAssetStatus.READY.value
        asset.storage_path = storage_path
        asset.mime_type = mime_type
        asset.size_bytes = path.stat().st_size
        asset.width = width
        asset.height = height
        asset.model = resolved_model
        asset.metadata_json = {
            **(asset.metadata_json or {}),
            "generation_stage": "ready",
            "generation_resolved_model": resolved_model,
            "generation_elapsed_seconds": round(elapsed_seconds, 3),
            "generation_cache_hit": cache_hit,
            "generation_usage": usage,
            "generation_estimated_cost": estimated_cost,
            "generation_error_type": "",
            "generation_error_message": "",
        }
        flag_modified(asset, "metadata_json")
        await db.commit()
        return editing_jobs.EditingJobResult(
            result_json={
                "stage": "ready",
                "asset_id": asset.id,
                "provider": asset.provider,
                "requested_model": settings.volc_ark_image_model.strip(),
                "resolved_model": resolved_model,
                "timeout_seconds": settings.volc_ark_image_timeout_seconds,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "cache_hit": cache_hit,
                "estimated_cost": estimated_cost,
            }
        )
    except asyncio.CancelledError:
        await db.rollback()
        elapsed_seconds = perf_counter() - started_at
        await _mark_asset_failed(
            db,
            asset=asset,
            error_type="generation_cancelled",
            error_message="图片生成超时，请重试",
            elapsed_seconds=elapsed_seconds,
        )
        raise
    except ShareVideoBackgroundGenerationError as exc:
        await db.rollback()
        elapsed_seconds = perf_counter() - started_at
        await _mark_asset_failed(
            db,
            asset=asset,
            error_type=type(exc).__name__,
            error_message=str(exc),
            elapsed_seconds=elapsed_seconds,
        )
        return editing_jobs.EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                "stage": "failed",
                "asset_id": asset.id,
                "provider": VolcArkImageGenerationService.provider_name,
                "requested_model": settings.volc_ark_image_model.strip(),
                "timeout_seconds": settings.volc_ark_image_timeout_seconds,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "error_type": type(exc).__name__,
            },
            error_message=str(exc),
        )
    except Exception as exc:
        await db.rollback()
        elapsed_seconds = perf_counter() - started_at
        logger.exception("memoir material image generation failed asset_id=%s", asset.id)
        await _mark_asset_failed(
            db,
            asset=asset,
            error_type=type(exc).__name__,
            error_message="图片生成失败，请重试",
            elapsed_seconds=elapsed_seconds,
        )
        return editing_jobs.EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                "stage": "failed",
                "asset_id": asset.id,
                "provider": VolcArkImageGenerationService.provider_name,
                "requested_model": settings.volc_ark_image_model.strip(),
                "timeout_seconds": settings.volc_ark_image_timeout_seconds,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "error_type": type(exc).__name__,
            },
            error_message="图片生成失败，请重试",
        )
