from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirMaterialShareVideo
from app.models.projects import Project
from app.services.memoir import share_video_rendering as rendering
from app.services.memoir.image_assets import (
    RegisteredShareVideoBackground,
    register_share_video_background_asset,
)
from app.services.memoir.share_video_backgrounds import (
    SHARE_VIDEO_BACKGROUND_MODE_SINGLE,
    ShareVideoBackgroundAsset,
    SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI,
    ShareVideoBackgroundGenerationError,
    get_or_generate_share_video_background,
    share_video_narrator_context,
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
    SHARE_VIDEO_SCENE_MIN_DURATION_MS,
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
    # Auto lets the scene planner decide how many images the content needs;
    # only clips too short for two scenes skip planning.
    return (
        SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI
        if duration_ms >= 2 * SHARE_VIDEO_SCENE_MIN_DURATION_MS
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
    project = await db.get(Project, job.project_id)
    project_metadata = project.metadata_json if project is not None and isinstance(project.metadata_json, dict) else {}
    narrator_context = share_video_narrator_context(project_metadata.get("memoir_profile"))

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
            narrator_context=narrator_context,
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

        story_text = job.selected_text if len(plan.scenes) > 1 else ""
        visual_context = plan.visual_context if story_text else ""
        scene_assets: dict[int, ShareVideoSceneAssetMetadata] = {}
        scene_paths: dict[int, Path | None] = {}
        errors: dict[int, str] = {}

        async def generate_scene(
            scene: ShareVideoScene,
            reference_image_path: Path | None,
        ) -> tuple[ShareVideoScene, ShareVideoBackgroundAsset | None, ShareVideoBackgroundGenerationError | None]:
            try:
                asset = await get_or_generate_share_video_background(
                    _scene_source_text(job.selected_text, scene),
                    background_style,
                    story_text=story_text,
                    visual_context=visual_context,
                    narrator_context=narrator_context,
                    reference_image_path=reference_image_path,
                    usage_context=usage_context,
                )
            except ShareVideoBackgroundGenerationError as exc:
                return scene, None, exc
            return scene, asset, None

        async def record_scene(
            scene: ShareVideoScene,
            asset: ShareVideoBackgroundAsset | None,
            error: ShareVideoBackgroundGenerationError | None,
        ) -> None:
            scene_info = ShareVideoSceneAssetMetadata.model_validate(_scene_metadata(scene))
            if error is not None:
                errors[scene.index] = f"{scene.index + 1}: {str(error)[:120]}"
                scene_info.background_status = "failed"
                scene_info.background_error_message = str(error)[:300]
                scene_paths[scene.index] = None
            elif asset is None:
                scene_info.background_status = "disabled"
                scene_info.background_error_message = ""
                scene_paths[scene.index] = None
            else:
                scene_paths[scene.index] = asset.path
                await register_share_video_background_asset(
                    db,
                    job=job,
                    background=asset,
                    context=RegisteredShareVideoBackground(
                        source_quote=_scene_source_text(job.selected_text, scene),
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
            scene_assets[scene.index] = scene_info
            metadata.background_generated_count = sum(path is not None for path in scene_paths.values())
            metadata.background_scenes = [scene_assets[index] for index in sorted(scene_assets)]
            await persist_progress(metadata)

        # The first scene is drawn alone so the rest can use it as a reference
        # image and keep the same people; the rest are drawn in parallel.
        first_scene, first_asset, first_error = await generate_scene(plan.scenes[0], None)
        await record_scene(first_scene, first_asset, first_error)
        reference_image_path = first_asset.path if first_asset is not None else None
        pending = [
            asyncio.create_task(generate_scene(scene, reference_image_path))
            for scene in plan.scenes[1:]
        ]
        try:
            for finished in asyncio.as_completed(pending):
                await record_scene(*await finished)
        finally:
            for task in pending:
                task.cancel()

        valid_paths = [scene_paths.get(scene.index) for scene in plan.scenes]
        ready_assets = [
            scene_assets[scene.index]
            for scene in plan.scenes
            if scene_paths.get(scene.index) is not None
        ]
        first_valid_path = next((path for path in valid_paths if path is not None), None)
        first_asset_provider = ready_assets[0].background_provider if ready_assets else None
        first_asset_model = ready_assets[0].background_model if ready_assets else None
        scene_asset_list = [scene_assets[index] for index in sorted(scene_assets)]
        error_messages = [errors[index] for index in sorted(errors)]

        if first_valid_path is None:
            metadata.background_status = "failed"
            metadata.background_error_message = "智能多图背景生成失败，已使用经典背景"
            metadata.background_scenes = scene_asset_list
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
            metadata.background_status = "partial" if error_messages or reused_count else "ready"
            metadata.background_error_message = "；".join(error_messages)[:500]
            metadata.background_scenes = scene_asset_list
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
                narrator_context=narrator_context,
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
