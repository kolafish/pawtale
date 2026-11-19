from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
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
    ShareVideoBackgroundCacheKey,
    SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI,
    ShareVideoBackgroundGenerationError,
    get_or_generate_share_video_background,
    prepare_share_video_image_request,
    ShareVideoBackgroundRequest,
    ShareVideoImageReference,
    ShareVideoImageSlot,
    lock_share_video_image_owner,
    recover_share_video_background,
    share_video_background_file_path,
    share_video_narrator_context,
    normalize_share_video_background_mode,
    normalize_share_video_background_style,
)
from app.services.memoir.share_video_custom_backgrounds import (
    SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC,
    SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM,
    load_share_video_photo_paths,
    normalize_share_video_background_source,
)
from app.services.memoir.share_video_scene_planner import (
    SHARE_VIDEO_SCENE_MIN_DURATION_MS,
    SHARE_VIDEO_SCENE_PLAN_VERSION,
    group_share_video_sentences,
    plan_share_video_background_scenes,
    scene_plan_to_metadata,
    share_video_cast_reference_subjects,
)
from app.services.memoir.share_video_metadata import (
    BackgroundStatus,
    ShareVideoJobMetadata,
    ShareVideoPlannedSceneMetadata,
    ShareVideoSceneAssetMetadata,
    ShareVideoScenePlanMetadata,
)
from app.services.ai_usage_records import AIUsageContext
from app.services.memoir.job_leases import JobLease


logger = logging.getLogger(__name__)

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


def _scene_source_text(selected_text: str, scene: ShareVideoPlannedSceneMetadata) -> str:
    return selected_text[scene.start_char:scene.end_char].strip()


def _render_background_segments(
    scenes: list[ShareVideoPlannedSceneMetadata],
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


def _photo_segments(
    selected_text: str,
    char_timings: list[dict[str, int]],
    duration_ms: int,
    paths: list[Path],
) -> list[rendering.ShareVideoBackgroundSegment]:
    """Give each photo, in order, a run of whole sentences; split evenly when sentences run out."""
    groups = group_share_video_sentences(selected_text, char_timings, duration_ms, len(paths))
    if len(groups) == len(paths):
        starts = [0, *(group[0].start_ms for group in groups[1:])]
    else:
        starts = [round(duration_ms * index / len(paths)) for index in range(len(paths))]
    ends = [*starts[1:], duration_ms]
    return [
        rendering.ShareVideoBackgroundSegment(image_path=path, start_ms=start, end_ms=max(start + 1, end))
        for path, start, end in zip(paths, starts, ends, strict=True)
    ]


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

    lease = JobLease(job.id, job.lease_token)

    def image_slot(name: str) -> ShareVideoImageSlot | None:
        return ShareVideoImageSlot(lease, name, usage_context) if usage_context else None

    user_id = job.user_id

    def checkpoint_key(key: ShareVideoBackgroundCacheKey, name: str) -> ShareVideoBackgroundCacheKey:
        slot = image_slot(name)
        return key.for_slot(slot) if slot and key.image_storage_path is None else key

    if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM:
        photo_paths = await load_share_video_photo_paths(
            db, project_id=job.project_id, asset_ids=metadata.photo_asset_ids,
        ) if metadata.photo_asset_ids else None
        if not photo_paths:
            raise rendering.ShareVideoGenerationError("有照片已删除，请重新选择")
        background_segments = _photo_segments(job.selected_text, metadata.char_timing_dicts(), duration_ms, photo_paths)
        metadata.background_status = "ready"
        metadata.background_effective_mode = SHARE_VIDEO_BACKGROUND_MODE_SINGLE
        metadata.background_scene_count = len(photo_paths)
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
        plan = metadata.background_scene_plan
        has_saved_images = bool(metadata.scene_image_requests or metadata.cast_image_request or metadata.background_scenes or metadata.background_reference_digest)
        if plan is None or not plan.scenes or (plan.version != SHARE_VIDEO_SCENE_PLAN_VERSION and not has_saved_images):
            metadata.background_status = "planning"
            metadata.background_error_message = ""
            await persist_progress(metadata)
            planned = await plan_share_video_background_scenes(
                job.selected_text, metadata.char_timing_dicts(), duration_ms,
                ai_model=metadata.ai_model, narrator_context=narrator_context,
                background_note=metadata.background_note,
            )
            plan = ShareVideoScenePlanMetadata.model_validate(scene_plan_to_metadata(planned))
            metadata.background_scene_plan = plan
            metadata.cast_reference_status = None
            metadata.background_reference_digest = None
            metadata.background_scenes = []
        elif metadata.cast_reference_status is None and not metadata.background_scenes and not metadata.background_reference_digest:
            # An unfinished plan from before the recurring-cast rule may not have drawn anything yet.
            plan.cast_reference_subjects = list(share_video_cast_reference_subjects(plan.scenes))
        if len(plan.scenes) > 1 and not metadata.background_reference_storage_path:
            metadata.background_reference_storage_path = (
                ShareVideoImageReference.legacy_storage_path(metadata.background_reference_digest)
                or f"memoir/share-video-backgrounds/references/jobs/{job.id}.jpg"
            )
        metadata.background_status = "generating"
        metadata.background_scene_count = len(plan.scenes)
        metadata.background_reused_scene_count = 0
        metadata.background_plan_provider = plan.provider
        metadata.background_plan_model = plan.model
        metadata.background_error_message = ""

        story_text = job.selected_text
        visual_context = plan.visual_context
        if plan.person_count:
            visual_context += f"\n参与这段经历的人物数量：{plan.person_count}。"
        scene_assets = {scene.index: scene for scene in metadata.background_scenes}
        scene_paths: dict[int, Path | None] = {}
        for scene in scene_assets.values():
            if scene.background_storage_path:
                path = share_video_background_file_path(scene.background_storage_path)
                if path.is_file() and path.stat().st_size:
                    scene_paths[scene.index] = path
                    scene.background_cache_hit = True
                    scene.background_status = "ready"
                    scene.background_error_message = ""
                else:
                    # Retain the receipt for a paid image even if its file was removed.
                    scene_paths[scene.index] = None
                    scene.background_status = "failed"
                    scene.background_error_message = "已生成的背景图文件缺失"
        metadata.background_generated_count = sum(path is not None for path in scene_paths.values())
        for index, key in metadata.scene_image_requests.items():
            if index not in scene_paths:
                metadata.scene_image_requests[index] = checkpoint_key(key, f"scene-{index}")
        if metadata.cast_image_request:
            metadata.cast_image_request = checkpoint_key(metadata.cast_image_request, "cast")
        await persist_progress(metadata)

        async def prepare_requests(
            scenes: list[ShareVideoPlannedSceneMetadata], reference: ShareVideoImageReference | None,
        ) -> list[tuple[ShareVideoPlannedSceneMetadata, ShareVideoBackgroundRequest]]:
            requests = [
                (scene, prepare_share_video_image_request(
                    _scene_source_text(job.selected_text, scene), background_style,
                    shot_direction=scene.shot_direction, reference=reference, story_text=story_text,
                    visual_context=visual_context, narrator_context=narrator_context, background_note=metadata.background_note,
                    slot=image_slot(f"scene-{scene.index}"),
                ))
                for scene in scenes
            ]
            keys = {scene.index: request.key for scene, request in requests}
            if any(metadata.scene_image_requests.get(index) != key for index, key in keys.items()):
                metadata.scene_image_requests.update(keys)
                await persist_progress(metadata)
            return requests

        async def generate_scene(
            scene: ShareVideoPlannedSceneMetadata, request: ShareVideoBackgroundRequest,
        ) -> tuple[ShareVideoPlannedSceneMetadata, ShareVideoBackgroundAsset | None, ShareVideoBackgroundGenerationError | None]:
            try:
                asset = await get_or_generate_share_video_background(request, slot=image_slot(f"scene-{scene.index}"))
            except ShareVideoBackgroundGenerationError as exc:
                return scene, None, exc
            return scene, asset, None

        async def record_scene(
            scene: ShareVideoPlannedSceneMetadata,
            asset: ShareVideoBackgroundAsset | None,
            error: ShareVideoBackgroundGenerationError | None,
        ) -> None:
            scene_info = ShareVideoSceneAssetMetadata.model_validate(scene.model_dump())
            if error is not None:
                scene_info.background_status = "failed"
                scene_info.background_error_message = str(error)[:300]
                scene_paths[scene.index] = None
            elif asset is None:
                scene_info.background_status = "disabled"
                scene_info.background_error_message = ""
                scene_paths[scene.index] = None
            else:
                await lock_share_video_image_owner(db, lease, user_id=user_id)
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

        async def select_reference(path: Path) -> ShareVideoImageReference:
            chosen = await asyncio.to_thread(ShareVideoImageReference.from_path, path)
            # Hold the job row only while publishing the reference and its progress.
            # Deletion or lease transfer must not leave a late JPEG on disk.
            await lock_share_video_image_owner(db, lease, user_id=user_id)
            chosen.save(metadata.background_reference_storage_path)
            metadata.background_reference_digest = chosen.digest
            return chosen

        # A worker can stop after a paid image reaches disk and before the scene is registered.
        # Look up the original key before building prompts from today's narrator settings.
        for scene in plan.scenes:
            key = metadata.scene_image_requests.get(scene.index)
            if scene.index not in scene_paths and key:
                try:
                    cached = await recover_share_video_background(key, image_slot(f"scene-{scene.index}"))
                except ShareVideoBackgroundGenerationError as exc:
                    await record_scene(scene, None, exc)
                else:
                    if cached:
                        await record_scene(scene, cached, None)

        reference = None
        reference_error = None
        unfinished = [scene for scene in plan.scenes if scene.index not in scene_paths]
        if unfinished:
            try:
                if metadata.background_reference_digest:
                    reference = await asyncio.to_thread(
                        ShareVideoImageReference.restore, metadata.background_reference_digest, metadata.background_reference_storage_path,
                    )
                elif metadata.cast_reference_status == "ready" or (
                    plan.cast_reference_subjects and metadata.cast_reference_status != "failed" and not scene_paths
                ):
                    # Older jobs saved the cast status but only cached the original image.
                    already_paid = metadata.cast_reference_status == "ready"
                    cast_slot = image_slot("cast")
                    cast = await recover_share_video_background(metadata.cast_image_request, cast_slot)
                    if cast is None:
                        request = prepare_share_video_image_request(
                            story_text, background_style, cast_subjects=tuple(plan.cast_reference_subjects),
                            visual_context=visual_context, narrator_context=narrator_context, background_note=metadata.background_note,
                            slot=cast_slot,
                        )
                        if not already_paid or not metadata.cast_image_request:
                            metadata.cast_image_request = request.key
                            await persist_progress(metadata)
                            cast = await get_or_generate_share_video_background(request, slot=cast_slot, cache_only=already_paid)
                    if cast:
                        reference = await select_reference(cast.path)
                    elif already_paid:
                        raise ShareVideoBackgroundGenerationError("原有的多人定稿图已缺失")
                    metadata.cast_reference_status = "ready" if reference else "failed"
                elif scene_paths:
                    # Before checkpoints, the first successful scene was the fixed reference.
                    anchor = scene_paths[min(scene_paths)]
                    if anchor is None:
                        raise ShareVideoBackgroundGenerationError("原有的首张参考图已缺失")
                    reference = await select_reference(anchor)
            except ShareVideoBackgroundGenerationError as exc:
                if metadata.background_reference_digest or metadata.cast_reference_status == "ready" or scene_paths:
                    reference_error = exc
                else:
                    metadata.cast_reference_status = "failed"
                logger.warning("Share video reference unavailable: %s", exc)
            await persist_progress(metadata)

        remaining = iter(unfinished)
        if reference_error:
            # Preserve paid frames and let the renderer fill gaps with adjacent frames.
            for scene in remaining:
                await record_scene(scene, None, reference_error)
        elif reference is None and len(plan.scenes) > 1:
            for scene in remaining:
                (entry,) = await prepare_requests([scene], None)
                _, asset, error = await generate_scene(*entry)
                if asset:
                    try:
                        reference = await select_reference(asset.path)
                    except ShareVideoBackgroundGenerationError as exc:
                        asset, error = None, exc
                # The scene receipt and the chosen reference commit in the same transaction.
                await record_scene(scene, asset, error)
                if reference:
                    break
        requests = await prepare_requests(list(remaining), reference)
        pending = [asyncio.create_task(generate_scene(scene, request)) for scene, request in requests]
        try:
            for finished in asyncio.as_completed(pending):
                await record_scene(*await finished)
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

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
        error_messages = [
            f"{scene.index + 1}: {scene.background_error_message[:120]}"
            for scene in scene_asset_list if scene.background_error_message
        ]

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
            single_slot = image_slot("single")
            if not metadata.background_storage_path and metadata.single_image_request:
                checkpoint = checkpoint_key(metadata.single_image_request, "single")
                if checkpoint != metadata.single_image_request:
                    metadata.single_image_request = checkpoint
                    await persist_progress(metadata)
            saved_key = metadata.single_image_request
            if saved_key is None and metadata.background_storage_path:
                # Early single-image jobs recorded the final file without a request checkpoint.
                saved_key = ShareVideoBackgroundCacheKey(
                    storage_path=metadata.background_storage_path,
                    style_id=background_style,
                    model=metadata.background_model or "",
                    prompt_hash=metadata.background_prompt_hash or "",
                    template_version=metadata.background_prompt_version or "",
                )
            background = await recover_share_video_background(saved_key, single_slot)
            if background is None:
                request = prepare_share_video_image_request(
                    job.selected_text, background_style, narrator_context=narrator_context, background_note=metadata.background_note,
                    slot=single_slot,
                )
                metadata.single_image_request = request.key
                await persist_progress(metadata)
                background = await get_or_generate_share_video_background(request, slot=single_slot)
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
                await lock_share_video_image_owner(db, lease, user_id=user_id)
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
