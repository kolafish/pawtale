from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirMaterialShareVideo
from app.services.memoir import share_video_rendering as rendering
from app.services.memoir.image_assets import (
    RegisteredShareVideoBackground,
    register_share_video_background_asset,
)
from app.services.memoir.share_video_backgrounds import (
    SHARE_VIDEO_BACKGROUND_MODE_SINGLE,
    SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI,
    ShareVideoBackgroundGenerationError,
    get_or_generate_share_video_background,
    normalize_share_video_background_mode,
    normalize_share_video_background_style,
)
from app.services.memoir.share_video_custom_backgrounds import (
    SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC,
    SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM,
    load_custom_share_video_background_asset,
    normalize_share_video_background_source,
)
from app.services.memoir.share_video_scene_planner import (
    ShareVideoScene,
    plan_share_video_background_scenes,
    scene_plan_to_metadata,
)
from app.services.memoir.share_video_metadata import (
    BackgroundStatus,
    ShareVideoJobMetadata,
    ShareVideoSceneAssetMetadata,
    ShareVideoScenePlanMetadata,
)
from app.services.ai_usage_records import AIUsageContext


ProgressCallback = Callable[[ShareVideoJobMetadata], Awaitable[None]]


@dataclass(frozen=True)
class PreparedShareVideoBackground:
    image_path: Path | None
    segments: list[rendering.ShareVideoBackgroundSegment]


def effective_background_mode(background_mode: str, duration_ms: int) -> str:
    if background_mode == SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI:
        return SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI
    if background_mode == SHARE_VIDEO_BACKGROUND_MODE_SINGLE:
        return SHARE_VIDEO_BACKGROUND_MODE_SINGLE
    return (
        SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI
        if duration_ms >= 20_000
        else SHARE_VIDEO_BACKGROUND_MODE_SINGLE
    )


def initial_background_status(
    background_source: str,
    *,
    ai_background_enabled: bool,
) -> BackgroundStatus:
    if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM:
        return "ready"
    if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC:
        return "classic"
    return "pending" if ai_background_enabled else "disabled"


def _scene_metadata(scene: ShareVideoScene) -> dict[str, object]:
    return {
        "index": scene.index,
        "start_char": scene.start_char,
        "end_char": scene.end_char,
        "start_ms": scene.start_ms,
        "end_ms": scene.end_ms,
        "title": scene.title,
        "sentence_ids": list(scene.sentence_ids),
        "evidence_quote": scene.evidence_quote,
    }


def _scene_source_text(selected_text: str, scene: ShareVideoScene) -> str:
    return selected_text[scene.start_char:scene.end_char].strip()


def _render_background_segments(
    scenes: list[ShareVideoScene],
    image_paths: list[Path],
    duration_ms: int,
) -> list[rendering.ShareVideoBackgroundSegment]:
    if not scenes or not image_paths:
        return []
    raw_durations = [
        max(1, scene.end_ms - scene.start_ms)
        for scene in scenes[: len(image_paths)]
    ]
    raw_total = max(1, sum(raw_durations))
    segments: list[rendering.ShareVideoBackgroundSegment] = []
    cursor = 0
    for index, (raw_duration, image_path) in enumerate(zip(raw_durations, image_paths, strict=False)):
        if index == len(raw_durations) - 1:
            end_ms = max(cursor + 1, duration_ms)
        else:
            remaining_segments = len(raw_durations) - index - 1
            computed_end_ms = cursor + max(1, round(duration_ms * raw_duration / raw_total))
            end_ms = min(computed_end_ms, max(cursor + 1, duration_ms - remaining_segments))
        segments.append(
            rendering.ShareVideoBackgroundSegment(
                image_path=image_path,
                start_ms=cursor,
                end_ms=end_ms,
            )
        )
        cursor = end_ms
    return segments


async def prepare_share_video_background(
    db: AsyncSession,
    *,
    job: MemoirMaterialShareVideo,
    metadata: ShareVideoJobMetadata,
    persist_progress: ProgressCallback,
    ai_background_enabled: bool,
    volc_ark_api_key: str,
    usage_context: AIUsageContext | None = None,
) -> PreparedShareVideoBackground:
    background_source = normalize_share_video_background_source(metadata.background_source)
    background_style = normalize_share_video_background_style(metadata.background_style)
    background_mode = normalize_share_video_background_mode(metadata.background_mode)
    duration_ms = max(1, job.end_ms - job.start_ms)
    effective_mode = effective_background_mode(background_mode, duration_ms)
    if background_source in {SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM, SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC}:
        effective_mode = SHARE_VIDEO_BACKGROUND_MODE_SINGLE
    metadata.background_source = background_source
    metadata.background_style = background_style
    metadata.background_mode = background_mode
    metadata.background_effective_mode = effective_mode
    background_image_path: Path | None = None
    background_segments: list[rendering.ShareVideoBackgroundSegment] = []

    if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM:
        custom_background_asset_id = metadata.custom_background_asset_id
        if not custom_background_asset_id:
            raise rendering.ShareVideoGenerationError("自定义背景图不存在")
        custom_asset, background_image_path = await load_custom_share_video_background_asset(
            db,
            user_id=job.user_id,
            project_id=job.project_id,
            asset_id=custom_background_asset_id,
            allow_admin=True,
        )
        metadata.background_status = "ready"
        metadata.background_effective_mode = SHARE_VIDEO_BACKGROUND_MODE_SINGLE
        metadata.background_scene_count = 1
        metadata.background_storage_path = custom_asset.storage_path
        metadata.background_provider = custom_asset.provider
        metadata.background_model = custom_asset.model
        metadata.background_cache_hit = None
        metadata.background_error_message = ""
        await persist_progress(metadata)
    elif background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC:
        metadata.background_status = "classic"
        metadata.background_effective_mode = SHARE_VIDEO_BACKGROUND_MODE_SINGLE
        metadata.background_scene_count = 0
        metadata.background_error_message = ""
        await persist_progress(metadata)
    elif not ai_background_enabled or not volc_ark_api_key.strip():
        metadata.background_status = "disabled"
        metadata.background_effective_mode = SHARE_VIDEO_BACKGROUND_MODE_SINGLE
        metadata.background_scene_count = 0
        metadata.background_error_message = ""
        await persist_progress(metadata)
    elif effective_mode == SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI:
        char_timings = metadata.char_timing_dicts()
        metadata.background_status = "planning"
        metadata.background_error_message = ""
        await persist_progress(metadata)

        plan = await plan_share_video_background_scenes(
            job.selected_text,
            char_timings,
            duration_ms,
            ai_model=metadata.ai_model,
        )
        metadata.background_status = "generating"
        metadata.background_scene_plan = ShareVideoScenePlanMetadata.model_validate(
            scene_plan_to_metadata(plan)
        )
        metadata.background_scene_count = len(plan.scenes)
        metadata.background_generated_count = 0
        metadata.background_plan_provider = plan.provider
        metadata.background_plan_model = plan.model
        metadata.background_error_message = ""
        await persist_progress(metadata)

        scene_assets: list[ShareVideoSceneAssetMetadata] = []
        valid_paths: list[Path | None] = []
        first_valid_path: Path | None = None
        first_asset_provider: str | None = None
        first_asset_model: str | None = None
        generated_count = 0
        errors: list[str] = []
        for scene in plan.scenes:
            scene_info = ShareVideoSceneAssetMetadata.model_validate(_scene_metadata(scene))
            scene_source_text = _scene_source_text(job.selected_text, scene)
            try:
                asset = await get_or_generate_share_video_background(
                    scene_source_text,
                    background_style,
                    usage_context=usage_context,
                )
            except ShareVideoBackgroundGenerationError as exc:
                errors.append(f"{scene.index + 1}: {str(exc)[:120]}")
                scene_info.background_status = "failed"
                scene_info.background_error_message = str(exc)[:300]
                valid_paths.append(None)
            else:
                if asset is None:
                    scene_info.background_status = "disabled"
                    scene_info.background_error_message = ""
                    valid_paths.append(None)
                else:
                    generated_count += 1
                    first_valid_path = first_valid_path or asset.path
                    first_asset_provider = first_asset_provider or asset.provider
                    first_asset_model = first_asset_model or asset.model
                    valid_paths.append(asset.path)
                    await register_share_video_background_asset(
                        db,
                        job=job,
                        background=asset,
                        context=RegisteredShareVideoBackground(
                            source_quote=scene_source_text,
                            scene_index=scene.index,
                            scene_title=scene.title,
                            evidence_quote=scene.evidence_quote,
                        ),
                    )
                    scene_info.background_status = "ready"
                    scene_info.background_storage_path = asset.storage_path
                    scene_info.background_prompt_hash = asset.prompt_hash
                    scene_info.background_prompt_version = asset.prompt_template_version
                    scene_info.background_provider = asset.provider
                    scene_info.background_model = asset.model
                    scene_info.background_cache_hit = asset.cache_hit
                    scene_info.background_error_message = ""
            scene_assets.append(scene_info)
            metadata.background_generated_count = generated_count
            metadata.background_scenes = scene_assets
            await persist_progress(metadata)

        if first_valid_path is None:
            metadata.background_status = "failed"
            metadata.background_error_message = "智能多图背景生成失败，已使用经典背景"
            metadata.background_scenes = scene_assets
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
            metadata.background_status = "partial" if errors or reused_count else "ready"
            metadata.background_error_message = "；".join(errors)[:500]
            metadata.background_scenes = scene_assets
            metadata.background_reused_scene_count = reused_count
            metadata.background_provider = first_asset_provider
            metadata.background_model = first_asset_model
            metadata.background_cache_hit = None
        await persist_progress(metadata)
    else:
        try:
            background = await get_or_generate_share_video_background(
                job.selected_text,
                background_style,
                usage_context=usage_context,
            )
        except ShareVideoBackgroundGenerationError as exc:
            metadata.background_status = "failed"
            metadata.background_scene_count = 0
            metadata.background_error_message = str(exc)[:500]
        else:
            if background is None:
                metadata.background_status = "disabled"
                metadata.background_scene_count = 0
                metadata.background_error_message = ""
            else:
                background_image_path = background.path
                await register_share_video_background_asset(
                    db,
                    job=job,
                    background=background,
                    context=RegisteredShareVideoBackground(source_quote=job.selected_text),
                )
                metadata.background_status = "ready"
                metadata.background_scene_count = 1
                metadata.background_storage_path = background.storage_path
                metadata.background_prompt_hash = background.prompt_hash
                metadata.background_prompt_version = background.prompt_template_version
                metadata.background_provider = background.provider
                metadata.background_model = background.model
                metadata.background_cache_hit = background.cache_hit
                metadata.background_error_message = ""
        await persist_progress(metadata)

    return PreparedShareVideoBackground(
        image_path=background_image_path,
        segments=background_segments,
    )
