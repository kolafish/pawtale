from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import mimetypes
from io import BytesIO
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.application_errors import ResourceNotFoundError
from app.config import DATA_DIR
from app.domain.memoir.types import (
    MemoirBookImagePlacementType,
    MemoirCreatedBy,
    MemoirImageAssetSourceType,
    MemoirImageAssetStatus,
    MemoirSectionStatus,
    MemoirShareVideoJobStatus,
)
from app.models.memoir import (
    MemoirBookImagePlacement,
    MemoirBookOutput,
    MemoirBookPlanChapter,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.models.projects import Project
from app.schemas.memoir_images import (
    COVER_IMAGE_LAYOUTS,
    MemoirBookImagePlacementCreateRequest,
    MemoirBookImagePlacementResponse,
    MemoirBookImagePlacementUpdateRequest,
    MemoirImageAssetListResponse,
    MemoirImageAssetResponse,
    MemoirImageAssetUpdateRequest,
)
from app.services import content_storage
from app.services.memoir.access import load_memoir_project
from app.services.memoir.branding import branded_image_derivative
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.book_output_versions import append_book_output_version
from app.services.memoir.share_video_backgrounds import ShareVideoBackgroundAsset


AI_GENERATED_IMAGE_DISCLOSURE = "AI 根据讲述内容生成的意象插图，不是真实照片。"
IMAGE_ASSET_THUMBNAIL_VERSION = "memoir-thumb-v1"
IMAGE_ASSET_THUMBNAIL_MAX_SIZE = (960, 960)
IMAGE_ASSET_THUMBNAIL_JPEG_QUALITY = 85
MATERIAL_ANCHORED_IMAGE_SOURCE_TYPES = {
    MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
    MemoirImageAssetSourceType.CHAPTER_ILLUSTRATION.value,
    MemoirImageAssetSourceType.UTTERANCE_ILLUSTRATION.value,
}


@dataclass(frozen=True)
class RegisteredShareVideoBackground:
    source_quote: str
    scene_index: int | None = None
    scene_title: str = ""
    evidence_quote: str = ""


def _safe_text(value: object, max_length: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def _source_signature(*parts: object) -> str:
    payload = "\u241f".join(str(part or "") for part in parts)
    return sha256(payload.encode("utf-8")).hexdigest()


def _file_path(storage_path: str) -> Path:
    if not storage_path:
        raise HTTPException(status_code=404, detail="图片文件不存在")
    path = Path(storage_path)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (DATA_DIR / path).resolve()
    data_root = DATA_DIR.resolve()
    if data_root not in resolved.parents and resolved != data_root:
        raise HTTPException(status_code=400, detail="图片路径无效")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    return resolved


def _should_brand_download(asset: MemoirImageAsset) -> bool:
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    return (
        asset.source_type != MemoirImageAssetSourceType.MANUAL_UPLOAD.value
        and asset.provider != "user_upload"
        and metadata.get("share_video_background_source") != "custom_upload"
    )


def _thumbnail_derivative_path(source_path: Path) -> Path:
    return source_path.with_name(
        f"{source_path.stem}.{IMAGE_ASSET_THUMBNAIL_VERSION}.jpg"
    )


def image_asset_thumbnail_derivative(source_path: Path) -> Path:
    target_path = _thumbnail_derivative_path(source_path)
    if (
        target_path.exists()
        and target_path.is_file()
        and target_path.stat().st_mtime >= source_path.stat().st_mtime
    ):
        return target_path

    try:
        with Image.open(source_path) as source:
            transposed = ImageOps.exif_transpose(source)
            if "A" in transposed.getbands():
                rgba = transposed.convert("RGBA")
                image = Image.new("RGB", rgba.size, (255, 255, 255))
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = transposed.convert("RGB")
            image.thumbnail(
                IMAGE_ASSET_THUMBNAIL_MAX_SIZE,
                Image.Resampling.LANCZOS,
            )
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail="图片预览文件无法处理") from exc

    temporary_path = target_path.with_name(
        f".{target_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    try:
        image.save(
            temporary_path,
            format="JPEG",
            quality=IMAGE_ASSET_THUMBNAIL_JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
        os.replace(temporary_path, target_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target_path


def _relative_data_storage_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(DATA_DIR.resolve()).as_posix()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="图片路径无效") from exc


def image_asset_file_path(
    asset: MemoirImageAsset,
    *,
    thumbnail: bool = False,
    branded: bool = False,
) -> Path:
    storage_path = asset.thumbnail_storage_path if thumbnail and asset.thumbnail_storage_path else asset.storage_path
    path = _file_path(storage_path)
    if branded and not thumbnail and _should_brand_download(asset):
        return branded_image_derivative(path)
    return path


def _file_url(project_id: str, asset_id: str, *, thumbnail: bool = False) -> str:
    suffix = "?variant=thumbnail" if thumbnail else ""
    return f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}/file{suffix}"


async def _chapter_titles(db: AsyncSession, chapter_ids: set[str]) -> dict[str, str]:
    if not chapter_ids:
        return {}
    rows = (
        await db.execute(
            select(MemoirBookPlanChapter.id, MemoirBookPlanChapter.title)
            .where(MemoirBookPlanChapter.id.in_(chapter_ids))
        )
    ).all()
    return {str(chapter_id): str(title or "") for chapter_id, title in rows}


async def _source_material_locations(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: set[str],
) -> dict[str, tuple[str, str]]:
    if not utterance_ids:
        return {}
    rows = (
        await db.execute(
            select(
                MemoirSectionMaterial.utterance_id,
                MemoirSection.id,
                MemoirSection.chapter_id,
            )
            .join(
                MemoirSection,
                MemoirSection.id == MemoirSectionMaterial.section_id,
            )
            .where(
                MemoirSectionMaterial.project_id == project_id,
                MemoirSectionMaterial.utterance_id.in_(utterance_ids),
                MemoirSectionMaterial.include_in_book.is_(True),
                MemoirSection.project_id == project_id,
                MemoirSection.status == MemoirSectionStatus.ACTIVE.value,
                MemoirSection.include_in_book.is_(True),
            )
        )
    ).all()
    return {
        str(utterance_id): (str(section_id), str(chapter_id))
        for utterance_id, section_id, chapter_id in rows
    }


def _uses_material_anchor(asset: MemoirImageAsset) -> bool:
    return bool(
        asset.source_utterance_id
        and asset.source_type in MATERIAL_ANCHORED_IMAGE_SOURCE_TYPES
    )


def _placement_response(
    placement: MemoirBookImagePlacement,
    *,
    chapter_titles: dict[str, str],
) -> MemoirBookImagePlacementResponse:
    return MemoirBookImagePlacementResponse(
        id=placement.id,
        project_id=placement.project_id,
        image_asset_id=placement.image_asset_id,
        placement_type=MemoirBookImagePlacementType(placement.placement_type),
        chapter_id=placement.chapter_id,
        chapter_title=chapter_titles.get(placement.chapter_id or "", ""),
        caption_override=placement.caption_override,
        display_size=placement.display_size,
        sort_order=placement.sort_order,
        enabled=placement.enabled,
        created_at=placement.created_at,
        updated_at=placement.updated_at,
    )


def _asset_response(
    asset: MemoirImageAsset,
    *,
    chapter_titles: dict[str, str],
) -> MemoirImageAssetResponse:
    placements = [
        _placement_response(placement, chapter_titles=chapter_titles)
        for placement in sorted(asset.placements, key=lambda item: (not item.enabled, item.sort_order, item.created_at))
    ]
    return MemoirImageAssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        status=MemoirImageAssetStatus(asset.status),
        source_type=MemoirImageAssetSourceType(asset.source_type),
        source_signature=asset.source_signature,
        storage_path=asset.storage_path,
        thumbnail_storage_path=asset.thumbnail_storage_path,
        file_url=_file_url(asset.project_id, asset.id),
        thumbnail_url=_file_url(asset.project_id, asset.id, thumbnail=True),
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        title=asset.title,
        caption=asset.caption,
        alt_text=asset.alt_text,
        style_id=asset.style_id,
        prompt_hash=asset.prompt_hash,
        prompt_version=asset.prompt_version,
        provider=asset.provider,
        model=asset.model,
        source_utterance_id=asset.source_utterance_id,
        source_chapter_id=asset.source_chapter_id,
        source_chapter_title=chapter_titles.get(asset.source_chapter_id or "", ""),
        share_video_job_id=asset.share_video_job_id,
        source_quote=asset.source_quote,
        metadata_json=asset.metadata_json or {},
        include_in_book=any(placement.enabled for placement in asset.placements),
        placements=placements,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


async def _load_asset(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    asset_id: str,
) -> MemoirImageAsset:
    await load_memoir_project(db, project_id, user_id)
    asset = await db.scalar(
        select(MemoirImageAsset)
        .where(
            MemoirImageAsset.project_id == project_id,
            MemoirImageAsset.id == asset_id,
        )
        .options(selectinload(MemoirImageAsset.placements))
    )
    if not asset or asset.status == MemoirImageAssetStatus.DELETED.value:
        raise HTTPException(status_code=404, detail="图片素材不存在")
    return asset


async def _lock_manual_image_selection(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> Project:
    await load_memoir_project(db, project_id, user_id)
    project = await db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise ResourceNotFoundError("项目不存在")
    return project


async def _begin_manual_image_selection(
    db: AsyncSession,
    *,
    project: Project,
    keep: MemoirBookImagePlacement | None = None,
) -> None:
    if project.memoir_book_image_selection_manual:
        return
    placements = (
        await db.scalars(
            select(MemoirBookImagePlacement).where(
                MemoirBookImagePlacement.project_id == project.id,
                MemoirBookImagePlacement.enabled.is_(True),
                MemoirBookImagePlacement.placement_type.notin_(["cover", "back_cover"]),
            )
        )
    ).all()
    for placement in placements:
        if placement is not keep:
            placement.enabled = False
    project.memoir_book_image_selection_manual = True


async def auto_place_share_video_backgrounds(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: set[str],
) -> bool:
    """Keep one video illustration per included chapter until the user arranges images."""
    if not chapter_ids:
        return False
    project = await db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or project.memoir_book_image_selection_manual:
        return False

    assets = (
        await db.scalars(
            select(MemoirImageAsset)
            .join(
                MemoirMaterialShareVideo,
                MemoirImageAsset.share_video_job_id == MemoirMaterialShareVideo.id,
            )
            .where(
                MemoirImageAsset.project_id == project_id,
                MemoirImageAsset.status == MemoirImageAssetStatus.READY.value,
                MemoirImageAsset.source_type == MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
                MemoirImageAsset.source_utterance_id.is_not(None),
                MemoirMaterialShareVideo.project_id == project_id,
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.SUCCEEDED.value,
            )
            .order_by(MemoirImageAsset.created_at, MemoirImageAsset.id)
        )
    ).all()
    source_locations = await _source_material_locations(
        db,
        project_id=project_id,
        utterance_ids={asset.source_utterance_id for asset in assets if asset.source_utterance_id},
    )
    candidates: dict[str, list[MemoirImageAsset]] = {}
    for asset in assets:
        metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
        location = source_locations.get(asset.source_utterance_id or "")
        if (
            not location
            or location[1] not in chapter_ids
            or asset.provider == "user_upload"
            or metadata.get("share_video_background_source") == "custom_upload"
        ):
            continue
        try:
            _file_path(asset.storage_path)
        except HTTPException:
            continue
        candidates.setdefault(location[1], []).append(asset)

    placements = (
        await db.scalars(
            select(MemoirBookImagePlacement)
            .where(MemoirBookImagePlacement.project_id == project_id,
                MemoirBookImagePlacement.placement_type.notin_(["cover", "back_cover"]))
            .order_by(MemoirBookImagePlacement.created_at, MemoirBookImagePlacement.id)
        )
    ).all()
    candidate_chapter_by_asset = {
        asset.id: chapter_id
        for chapter_id, chapter_assets in candidates.items()
        for asset in chapter_assets
    }
    selected: dict[str, MemoirBookImagePlacement] = {}
    changed = False
    for placement in placements:
        if not placement.enabled:
            continue
        chapter_id = candidate_chapter_by_asset.get(placement.image_asset_id)
        if chapter_id and chapter_id not in selected:
            selected[chapter_id] = placement
            if placement.placement_type != MemoirBookImagePlacementType.CHAPTER_INLINE.value:
                placement.placement_type = MemoirBookImagePlacementType.CHAPTER_INLINE.value
                changed = True
            if placement.chapter_id != chapter_id:
                placement.chapter_id = chapter_id
                changed = True
        else:
            placement.enabled = False
            changed = True

    by_asset = {placement.image_asset_id: placement for placement in placements}
    for chapter_id, chapter_assets in candidates.items():
        if chapter_id in selected:
            continue
        asset = chapter_assets[0]
        placement = by_asset.get(asset.id)
        if placement is None:
            placement = MemoirBookImagePlacement(
                project_id=project_id,
                image_asset_id=asset.id,
                placement_type=MemoirBookImagePlacementType.CHAPTER_INLINE.value,
                chapter_id=chapter_id,
            )
            db.add(placement)
        else:
            placement.placement_type = MemoirBookImagePlacementType.CHAPTER_INLINE.value
            placement.chapter_id = chapter_id
            placement.enabled = True
        changed = True
    if changed:
        await db.flush()
    return changed


def _store_uploaded_image(content: bytes, folder: Path) -> tuple[Path, int, int]:
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(status_code=400, detail="请选择 JPEG、PNG 或 WebP 图片")
            if source.width * source.height > 20_000_000:
                raise HTTPException(status_code=400, detail="请选择 2000 万像素以内的图片")
            source.verify()
        with Image.open(BytesIO(content)) as source:
            oriented = ImageOps.exif_transpose(source).convert("RGBA")
            image = Image.new("RGB", oriented.size, "white")
            image.paste(oriented, mask=oriented.getchannel("A"))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "original").write_bytes(content)
            path = folder / "image.jpg"
            image.save(path, format="JPEG", quality=95)
            return path, image.width, image.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="图片文件无效，请重新选择") from exc


async def upload_image_asset(db: AsyncSession, *, user_id: str, project_id: str, upload: UploadFile) -> MemoirImageAssetResponse:
    project = await _lock_manual_image_selection(db, user_id=user_id, project_id=project_id)
    content = await upload.read(10 * 1024 * 1024 + 1)
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="请选择 10MB 以内的图片")
    signature = "upload:" + sha256(content).hexdigest()
    existing = await db.scalar(select(MemoirImageAsset).where(
        MemoirImageAsset.project_id == project_id, MemoirImageAsset.source_signature == signature,
    ).options(selectinload(MemoirImageAsset.placements)))
    if existing and existing.status == MemoirImageAssetStatus.READY.value:
        return _asset_response(existing, chapter_titles={})
    folder = content_storage.PROJECTS_DIR / project_id / "image-uploads" / signature.removeprefix("upload:")
    path, width, height = await asyncio.to_thread(_store_uploaded_image, content, folder)
    asset = existing or MemoirImageAsset(project_id=project_id, user_id=project.user_id, source_signature=signature, placements=[])
    asset.status = MemoirImageAssetStatus.READY.value
    asset.source_type = MemoirImageAssetSourceType.MANUAL_UPLOAD.value
    asset.storage_path = _relative_data_storage_path(path)
    asset.mime_type, asset.size_bytes = "image/jpeg", path.stat().st_size
    asset.width, asset.height = width, height
    asset.title, asset.alt_text = "上传的照片", "用户上传的照片"
    asset.provider = "user_upload"
    asset.metadata_json = {"original_storage_path": _relative_data_storage_path(folder / "original"), "disclosure": ""}
    db.add(asset)
    await db.commit()
    await db.refresh(asset, attribute_names=["placements"])
    return _asset_response(asset, chapter_titles={})


async def list_image_assets(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    include_deleted: bool = False,
) -> MemoirImageAssetListResponse:
    await load_memoir_project(db, project_id, user_id)
    filters = [MemoirImageAsset.project_id == project_id]
    if not include_deleted:
        filters.append(MemoirImageAsset.status != MemoirImageAssetStatus.DELETED.value)
    items = (
        await db.execute(
            select(MemoirImageAsset)
            .where(*filters)
            .options(selectinload(MemoirImageAsset.placements))
            .order_by(MemoirImageAsset.created_at.desc(), MemoirImageAsset.id.desc())
        )
    ).scalars().all()
    chapter_ids = {
        value
        for asset in items
        for value in [
            asset.source_chapter_id,
            *(placement.chapter_id for placement in asset.placements),
        ]
        if value
    }
    chapter_titles = await _chapter_titles(db, chapter_ids)
    ready_count = sum(1 for item in items if item.status == MemoirImageAssetStatus.READY.value)
    included_count = sum(
        1 for item in items if any(placement.enabled for placement in item.placements)
    )
    return MemoirImageAssetListResponse(
        items=[_asset_response(item, chapter_titles=chapter_titles) for item in items],
        total=len(items),
        ready_count=ready_count,
        included_count=included_count,
        unused_count=max(0, ready_count - included_count),
    )


async def update_image_asset(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    asset_id: str,
    req: MemoirImageAssetUpdateRequest,
) -> MemoirImageAssetResponse:
    asset = await _load_asset(db, user_id=user_id, project_id=project_id, asset_id=asset_id)
    fields_set = getattr(req, "model_fields_set", set())
    if "title" in fields_set and req.title is not None:
        asset.title = req.title
    if "caption" in fields_set and req.caption is not None:
        asset.caption = req.caption
    if "alt_text" in fields_set and req.alt_text is not None:
        asset.alt_text = req.alt_text
    if any(placement.enabled for placement in asset.placements):
        await sync_latest_book_output_image_snapshots(db, project_id=project_id)
    await db.commit()
    await db.refresh(asset)
    chapter_titles = await _chapter_titles(
        db,
        {
            value
            for value in [asset.source_chapter_id, *(placement.chapter_id for placement in asset.placements)]
            if value
        },
    )
    return _asset_response(asset, chapter_titles=chapter_titles)


async def delete_image_asset(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    asset_id: str,
) -> None:
    project = await _lock_manual_image_selection(
        db, user_id=user_id, project_id=project_id,
    )
    asset = await _load_asset(db, user_id=user_id, project_id=project_id, asset_id=asset_id)
    if any(placement.enabled and placement.placement_type not in {"cover", "back_cover"} for placement in asset.placements):
        await _begin_manual_image_selection(db, project=project)
    asset.status = MemoirImageAssetStatus.DELETED.value
    for placement in asset.placements:
        placement.enabled = False
        db.add(placement)
    await sync_latest_book_output_image_snapshots(db, project_id=project_id)
    await db.commit()


async def create_image_placement(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    asset_id: str,
    req: MemoirBookImagePlacementCreateRequest,
) -> MemoirImageAssetResponse:
    project = await _lock_manual_image_selection(
        db, user_id=user_id, project_id=project_id,
    )
    asset = await _load_asset(db, user_id=user_id, project_id=project_id, asset_id=asset_id)
    if asset.status != MemoirImageAssetStatus.READY.value:
        raise HTTPException(status_code=400, detail="图片还不可入书")
    source_locations = await _source_material_locations(
        db,
        project_id=project_id,
        utterance_ids={asset.source_utterance_id} if asset.source_utterance_id else set(),
    )
    source_location = source_locations.get(asset.source_utterance_id or "")
    placement_type = req.placement_type
    chapter_id = req.chapter_id
    if (
        placement_type == MemoirBookImagePlacementType.APPENDIX
        and source_location
        and _uses_material_anchor(asset)
    ):
        placement_type = MemoirBookImagePlacementType.CHAPTER_INLINE
        chapter_id = source_location[1]
    elif placement_type == MemoirBookImagePlacementType.CHAPTER_INLINE and source_location:
        chapter_id = source_location[1]
    # Validate before touching any placement, so a rejected request changes nothing.
    display_size = _placement_display_size(placement_type.value, req.display_size, fallback_cover_layout=True)
    if placement_type in {
        MemoirBookImagePlacementType.CHAPTER_OPENING,
        MemoirBookImagePlacementType.CHAPTER_INLINE,
    } and not chapter_id:
        if placement_type == MemoirBookImagePlacementType.CHAPTER_INLINE:
            raise HTTPException(
                status_code=400,
                detail="没有找到图片对应的书稿素材位置，请选择章节",
            )
        raise HTTPException(status_code=400, detail="请选择要放入的章节")
    if chapter_id:
        chapter = await db.scalar(
            select(MemoirBookPlanChapter)
            .where(
                MemoirBookPlanChapter.id == chapter_id,
                MemoirBookPlanChapter.project_id == project_id,
            )
        )
        if not chapter:
            raise HTTPException(status_code=404, detail="章节不存在")
    placement = next(
        (
            item
            for item in asset.placements
            if item.placement_type == placement_type.value
            and (item.chapter_id or "") == (chapter_id or "")
        ),
        None,
    )
    if (
        not placement
        and placement_type == MemoirBookImagePlacementType.CHAPTER_INLINE
        and source_location
    ):
        placement = next(
            (
                item
                for item in asset.placements
                if item.placement_type
                == MemoirBookImagePlacementType.CHAPTER_INLINE.value
            ),
            None,
        ) or next(
            (
                item
                for item in asset.placements
                if item.placement_type
                == MemoirBookImagePlacementType.APPENDIX.value
            ),
            None,
        )
        if placement:
            placement.placement_type = placement_type.value
            placement.chapter_id = chapter_id
    if (
        placement_type == MemoirBookImagePlacementType.CHAPTER_INLINE
        and source_location
    ):
        for redundant in asset.placements:
            if (
                redundant is not placement
                and redundant.enabled
                and redundant.placement_type
                == MemoirBookImagePlacementType.APPENDIX.value
            ):
                redundant.enabled = False
                db.add(redundant)
    if not placement:
        next_sort_order = int(
            await db.scalar(
                select(func.coalesce(func.max(MemoirBookImagePlacement.sort_order), -1))
                .where(
                    MemoirBookImagePlacement.project_id == project_id,
                    MemoirBookImagePlacement.placement_type == placement_type.value,
                    MemoirBookImagePlacement.chapter_id == chapter_id,
                )
            )
            or -1
        ) + 1
        placement = MemoirBookImagePlacement(
            project_id=project_id,
            image_asset_id=asset.id,
            placement_type=placement_type.value,
            chapter_id=chapter_id,
            sort_order=next_sort_order,
        )
        asset.placements.append(placement)
    if placement_type == MemoirBookImagePlacementType.COVER:
        await _select_cover_placement(db, project_id=project_id, keep=placement)
    else:
        await _begin_manual_image_selection(db, project=project, keep=placement)
    placement.caption_override = req.caption_override or placement.caption_override
    placement.display_size = display_size
    placement.enabled = True
    db.add(asset)
    await sync_latest_book_output_image_snapshots(db, project_id=project_id)
    await db.commit()
    await db.refresh(asset)
    chapter_titles = await _chapter_titles(
        db,
        {
            value
            for value in [asset.source_chapter_id, *(item.chapter_id for item in asset.placements)]
            if value
        },
    )
    return _asset_response(asset, chapter_titles=chapter_titles)


def _placement_display_size(
    placement_type: str, requested: str, *, fallback_cover_layout: bool = False,
) -> str:
    is_cover = placement_type == MemoirBookImagePlacementType.COVER.value
    if is_cover and requested in COVER_IMAGE_LAYOUTS:
        return requested
    if is_cover and fallback_cover_layout:
        return "cover_frame"
    if is_cover or requested in COVER_IMAGE_LAYOUTS:
        raise HTTPException(status_code=400, detail="图片版式与位置不匹配")
    return requested


async def _select_cover_placement(
    db: AsyncSession, *, project_id: str, keep: MemoirBookImagePlacement,
) -> None:
    previous_covers = (await db.scalars(select(MemoirBookImagePlacement).where(
        MemoirBookImagePlacement.project_id == project_id,
        MemoirBookImagePlacement.placement_type == MemoirBookImagePlacementType.COVER.value,
        MemoirBookImagePlacement.enabled.is_(True),
    ))).all()
    for previous in previous_covers:
        if previous is not keep:
            previous.enabled = False


async def update_image_placement(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    asset_id: str,
    placement_id: str,
    req: MemoirBookImagePlacementUpdateRequest,
) -> MemoirImageAssetResponse:
    project = await _lock_manual_image_selection(
        db, user_id=user_id, project_id=project_id,
    )
    asset = await _load_asset(db, user_id=user_id, project_id=project_id, asset_id=asset_id)
    placement = next((item for item in asset.placements if item.id == placement_id), None)
    if not placement:
        raise HTTPException(status_code=404, detail="插图位置不存在")
    fields_set = getattr(req, "model_fields_set", set())
    display_size = (
        _placement_display_size(placement.placement_type, req.display_size)
        if "display_size" in fields_set and req.display_size is not None else None
    )
    if placement.placement_type not in {"cover", "back_cover"}:
        await _begin_manual_image_selection(
            db, project=project,
            keep=placement if req.enabled is not False and placement.enabled else None,
        )
    if "caption_override" in fields_set and req.caption_override is not None:
        placement.caption_override = req.caption_override
    if display_size is not None:
        placement.display_size = display_size
    if "enabled" in fields_set and req.enabled is not None:
        placement.enabled = req.enabled
    if placement.placement_type == MemoirBookImagePlacementType.COVER.value and placement.enabled:
        await _select_cover_placement(db, project_id=project_id, keep=placement)
    await sync_latest_book_output_image_snapshots(db, project_id=project_id)
    await db.commit()
    await db.refresh(asset)
    chapter_titles = await _chapter_titles(
        db,
        {
            value
            for value in [asset.source_chapter_id, *(item.chapter_id for item in asset.placements)]
            if value
        },
    )
    return _asset_response(asset, chapter_titles=chapter_titles)


async def delete_image_placement(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    asset_id: str,
    placement_id: str,
) -> MemoirImageAssetResponse:
    return await update_image_placement(
        db,
        user_id=user_id,
        project_id=project_id,
        asset_id=asset_id,
        placement_id=placement_id,
        req=MemoirBookImagePlacementUpdateRequest(enabled=False),
    )


async def load_image_asset_file(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    asset_id: str,
    thumbnail: bool = False,
    branded: bool = False,
) -> tuple[MemoirImageAsset, Path]:
    asset = await _load_asset(db, user_id=user_id, project_id=project_id, asset_id=asset_id)
    if thumbnail:
        if asset.thumbnail_storage_path:
            try:
                return asset, _file_path(asset.thumbnail_storage_path)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        source_path = _file_path(asset.storage_path)
        path = await asyncio.to_thread(
            image_asset_thumbnail_derivative,
            source_path,
        )
        asset.thumbnail_storage_path = _relative_data_storage_path(path)
        await db.commit()
        return asset, path
    path = await asyncio.to_thread(
        image_asset_file_path,
        asset,
        branded=branded,
    )
    return asset, path


def _asset_file_metadata(storage_path: str) -> tuple[str, int | None]:
    try:
        path = _file_path(storage_path)
    except HTTPException:
        mime_type = mimetypes.guess_type(storage_path)[0] or "image/jpeg"
        return mime_type, None
    return mimetypes.guess_type(path.name)[0] or "image/jpeg", path.stat().st_size


def _illustration_snapshot(
    asset: MemoirImageAsset,
    placement: MemoirBookImagePlacement,
    *,
    placement_type: str,
    section_id: str | None,
) -> dict[str, object]:
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    disclosure = "" if asset.source_type == "manual_upload" else (_safe_text(metadata.get("disclosure")) or AI_GENERATED_IMAGE_DISCLOSURE)
    caption = placement.caption_override or asset.caption or disclosure
    return {
        "image_asset_id": asset.id,
        "storage_path": asset.storage_path,
        "mime_type": asset.mime_type,
        "caption": caption,
        "alt_text": asset.alt_text or asset.title,
        "placement": placement_type,
        "display_size": placement.display_size,
        "source_quote": asset.source_quote,
        "source_utterance_id": asset.source_utterance_id,
        "section_id": section_id,
        "disclosure": disclosure,
    }


async def manuscript_image_snapshots(
    db: AsyncSession,
    *,
    project_id: str,
) -> tuple[dict[str, list[dict[str, object]]], list[dict[str, object]], dict[str, object] | None]:
    placements = (
        await db.execute(
            select(MemoirBookImagePlacement)
            .join(MemoirImageAsset, MemoirBookImagePlacement.image_asset_id == MemoirImageAsset.id)
            .where(
                MemoirBookImagePlacement.project_id == project_id,
                MemoirBookImagePlacement.enabled == True,  # noqa: E712
                MemoirImageAsset.status == MemoirImageAssetStatus.READY.value,
            )
            .options(selectinload(MemoirBookImagePlacement.image_asset))
            .order_by(
                MemoirBookImagePlacement.sort_order,
                MemoirBookImagePlacement.created_at,
                MemoirBookImagePlacement.id,
            )
        )
    ).scalars().all()
    source_locations = await _source_material_locations(
        db,
        project_id=project_id,
        utterance_ids={
            placement.image_asset.source_utterance_id
            for placement in placements
            if placement.image_asset.source_utterance_id
        },
    )
    chapter_images: dict[str, list[dict[str, object]]] = {}
    appendix_images: list[dict[str, object]] = []
    cover_image: dict[str, object] | None = None
    seen_targets: set[tuple[str, str, str, str]] = set()
    for placement in placements:
        asset = placement.image_asset
        source_location = source_locations.get(asset.source_utterance_id or "")
        effective_type = placement.placement_type
        chapter_id = placement.chapter_id
        section_id: str | None = None
        if source_location and (
            placement.placement_type == MemoirBookImagePlacementType.CHAPTER_INLINE.value
            or (
                placement.placement_type == MemoirBookImagePlacementType.APPENDIX.value
                and _uses_material_anchor(asset)
            )
        ):
            effective_type = MemoirBookImagePlacementType.CHAPTER_INLINE.value
            section_id, chapter_id = source_location
        snapshot = _illustration_snapshot(
            asset,
            placement,
            placement_type=effective_type,
            section_id=section_id,
        )
        target = (
            asset.id,
            effective_type,
            chapter_id or "",
            section_id or "",
        )
        if target in seen_targets:
            continue
        seen_targets.add(target)
        if effective_type in {
            MemoirBookImagePlacementType.CHAPTER_OPENING.value,
            MemoirBookImagePlacementType.CHAPTER_INLINE.value,
        } and chapter_id:
            chapter_images.setdefault(chapter_id, []).append(snapshot)
        elif effective_type == MemoirBookImagePlacementType.APPENDIX.value:
            appendix_images.append(snapshot)
        elif effective_type == "cover":
            cover_image = snapshot
    return chapter_images, appendix_images, cover_image


async def sync_latest_book_output_image_snapshots(
    db: AsyncSession,
    *,
    project_id: str,
) -> bool:
    output = await db.scalar(
        select(MemoirBookOutput)
        .where(MemoirBookOutput.project_id == project_id)
        .options(selectinload(MemoirBookOutput.versions))
    )
    if not output or not output.versions:
        return False
    latest = output.versions[-1]
    manuscript = (
        deepcopy(latest.manuscript_json)
        if isinstance(latest.manuscript_json, dict)
        else {}
    )
    chapters = manuscript.get("chapters")
    if not isinstance(chapters, list):
        return False
    chapter_images, appendix_images, cover_image = await manuscript_image_snapshots(db, project_id=project_id)
    changed = False
    for raw_chapter in chapters:
        if not isinstance(raw_chapter, dict):
            continue
        chapter_id = _safe_text(raw_chapter.get("chapter_id"), 80)
        illustrations = chapter_images.get(chapter_id, [])
        if raw_chapter.get("illustrations") != illustrations:
            raw_chapter["illustrations"] = illustrations
            changed = True
    if manuscript.get("image_appendix") != appendix_images:
        manuscript["image_appendix"] = appendix_images
        changed = True
    settings = manuscript.get("render_settings")
    if isinstance(settings, dict):
        if settings.get("cover_image") != cover_image:
            from app.services.memoir.book_output_manuscript import stored_manuscript_design
            settings["custom_layout"] = stored_manuscript_design(manuscript)[1].model_dump(mode="json")
            settings["cover_image"] = cover_image
            manuscript["schema_version"] = 5
            changed = True
        if any(chapter_images.values()):
            photo_mode = "chapter"
        elif appendix_images:
            photo_mode = "appendix"
        else:
            photo_mode = "none"
        if settings.get("photo_mode") != photo_mode:
            settings["photo_mode"] = photo_mode
            changed = True
    if not changed:
        return False
    await append_book_output_version(
        db,
        output=output,
        content=latest.content,
        chapter_snapshot_json=[
            item
            for item in (latest.chapter_snapshot_json or [])
            if isinstance(item, dict)
        ],
        manuscript_json=manuscript,
        created_by=MemoirCreatedBy.SYSTEM,
    )
    invalidate_book_output(output)
    return True


async def resync_all_latest_book_output_image_snapshots(db: AsyncSession) -> int:
    project_ids = (
        await db.execute(
            select(MemoirBookOutput.project_id)
            .where(MemoirBookOutput.versions.any())
            .distinct()
        )
    ).scalars().all()
    changed_count = 0
    for project_id in project_ids:
        if await sync_latest_book_output_image_snapshots(
            db,
            project_id=str(project_id),
        ):
            changed_count += 1
    return changed_count


async def register_share_video_background_asset(
    db: AsyncSession,
    *,
    job: MemoirMaterialShareVideo,
    background: ShareVideoBackgroundAsset,
    context: RegisteredShareVideoBackground,
) -> MemoirImageAsset:
    scene_key = "single" if context.scene_index is None else str(context.scene_index)
    source_signature = _source_signature(
        MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
        job.id,
        job.utterance_id,
        scene_key,
        background.storage_path,
    )
    existing = await db.scalar(
        select(MemoirImageAsset)
        .where(
            MemoirImageAsset.project_id == job.project_id,
            MemoirImageAsset.source_signature == source_signature,
        )
        .options(selectinload(MemoirImageAsset.placements))
    )
    mime_type, size_bytes = _asset_file_metadata(background.storage_path)
    title = context.scene_title or "讲述片段插图"
    caption = context.evidence_quote or context.source_quote or AI_GENERATED_IMAGE_DISCLOSURE
    metadata = {
        "disclosure": AI_GENERATED_IMAGE_DISCLOSURE,
        "share_video_start_ms": job.start_ms,
        "share_video_end_ms": job.end_ms,
        "share_video_text_hash": job.text_hash,
        "scene_index": context.scene_index,
    }
    if existing:
        existing.status = MemoirImageAssetStatus.READY.value
        existing.storage_path = background.storage_path
        existing.mime_type = mime_type
        existing.size_bytes = size_bytes
        existing.title = title
        existing.caption = caption
        existing.style_id = background.style_id
        existing.prompt_hash = background.prompt_hash
        existing.prompt_version = background.prompt_template_version
        existing.provider = background.provider
        existing.model = background.model
        existing.source_quote = context.source_quote
        existing.metadata_json = {**(existing.metadata_json or {}), **metadata}
        flag_modified(existing, "metadata_json")
        if any(placement.enabled for placement in existing.placements):
            await sync_latest_book_output_image_snapshots(db, project_id=job.project_id)
        return existing

    asset = MemoirImageAsset(
        project_id=job.project_id,
        user_id=job.user_id,
        status=MemoirImageAssetStatus.READY.value,
        source_type=MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
        source_signature=source_signature,
        storage_path=background.storage_path,
        mime_type=mime_type,
        size_bytes=size_bytes,
        title=title,
        caption=caption,
        alt_text=title,
        style_id=background.style_id,
        prompt_hash=background.prompt_hash,
        prompt_version=background.prompt_template_version,
        provider=background.provider,
        model=background.model,
        source_utterance_id=job.utterance_id,
        share_video_job_id=job.id,
        source_quote=context.source_quote,
        metadata_json=metadata,
    )
    db.add(asset)
    return asset
