from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Character, Scene
from app.models.projects import DocumentReference, ReferenceItem
from app.schemas.projects import (
    DocumentReferencesUpdate,
    ExportGlobalMatch,
    ExportGlobalRequest,
    ExportGlobalResponse,
    ImportGlobalRequest,
    ImportPreviewItem,
    ImportPreviewResponse,
    ReferenceItemCreate,
    ReferenceItemResponse,
    ReferenceItemUpdate,
)
from app.services import project_references as project_refs
from app.services.projects.access import get_document, get_reference, load_project


async def list_references(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> list[ReferenceItemResponse]:
    await load_project(db, project_id, user_id)
    result = await db.execute(
        select(ReferenceItem)
        .where(ReferenceItem.project_id == project_id)
        .order_by(ReferenceItem.sort_order)
    )
    return [ReferenceItemResponse.model_validate(r) for r in result.scalars().all()]


async def create_reference(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: ReferenceItemCreate,
) -> ReferenceItemResponse:
    await load_project(db, project_id, user_id)
    ref = ReferenceItem(
        project_id=project_id,
        type=req.type,
        name=req.name,
        content=req.content,
        metadata_json=req.metadata_json,
    )
    db.add(ref)
    await db.commit()
    await db.refresh(ref)
    return ReferenceItemResponse.model_validate(ref)


async def update_reference(
    db: AsyncSession,
    project_id: str,
    ref_id: str,
    user_id: str,
    req: ReferenceItemUpdate,
) -> ReferenceItemResponse:
    await load_project(db, project_id, user_id)
    ref = await get_reference(db, project_id, ref_id)
    for field in ["name", "content", "metadata_json"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(ref, field, val)
    await db.commit()
    await db.refresh(ref)
    return ReferenceItemResponse.model_validate(ref)


async def delete_reference(db: AsyncSession, project_id: str, ref_id: str, user_id: str) -> None:
    await load_project(db, project_id, user_id)
    ref = await get_reference(db, project_id, ref_id)
    await db.delete(ref)
    await db.commit()


async def import_from_global(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: ImportGlobalRequest,
) -> list[ReferenceItemResponse]:
    await load_project(db, project_id, user_id)
    created: list[ReferenceItem] = []
    for cid in req.character_ids:
        result = await db.execute(
            select(Character).where(Character.id == cid, project_refs.global_character_filter(user_id))
        )
        char = result.scalar_one_or_none()
        if not char:
            continue
        ref = project_refs.character_reference(project_id, char)
        db.add(ref)
        created.append(ref)
    for sid in req.scene_ids:
        result = await db.execute(
            select(Scene).where(Scene.id == sid, project_refs.global_scene_filter(user_id))
        )
        scene = result.scalar_one_or_none()
        if not scene:
            continue
        ref = project_refs.scene_reference(project_id, scene)
        db.add(ref)
        created.append(ref)
    await db.commit()
    for ref in created:
        await db.refresh(ref)
    return [ReferenceItemResponse.model_validate(r) for r in created]


async def import_preview(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: ImportGlobalRequest,
) -> ImportPreviewResponse:
    await load_project(db, project_id, user_id)
    refs_result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.project_id == project_id)
    )
    project_ref_items = refs_result.scalars().all()
    items: list[ImportPreviewItem] = []

    for cid in req.character_ids:
        result = await db.execute(
            select(Character).where(Character.id == cid, project_refs.global_character_filter(user_id))
        )
        char = result.scalar_one_or_none()
        if not char:
            continue
        existing = project_refs.find_matching_reference(
            project_ref_items,
            "character",
            char.id,
            char.name,
        )
        items.append(
            ImportPreviewItem(
                global_id=char.id,
                global_name=char.name,
                global_type="character",
                existing_ref_id=existing["ref_id"] if existing else None,
                existing_ref_name=existing["ref_name"] if existing else None,
                match_by=existing["match_by"] if existing else None,
            )
        )

    for sid in req.scene_ids:
        result = await db.execute(
            select(Scene).where(Scene.id == sid, project_refs.global_scene_filter(user_id))
        )
        scene = result.scalar_one_or_none()
        if not scene:
            continue
        existing = project_refs.find_matching_reference(project_ref_items, "location", scene.id, scene.name)
        items.append(
            ImportPreviewItem(
                global_id=scene.id,
                global_name=scene.name,
                global_type="scene",
                existing_ref_id=existing["ref_id"] if existing else None,
                existing_ref_name=existing["ref_name"] if existing else None,
                match_by=existing["match_by"] if existing else None,
            )
        )

    return ImportPreviewResponse(items=items)


async def export_to_global(
    db: AsyncSession,
    project_id: str,
    ref_id: str,
    user_id: str,
    req: ExportGlobalRequest,
) -> ExportGlobalResponse:
    await load_project(db, project_id, user_id)
    ref = await get_reference(db, project_id, ref_id)
    meta = ref.metadata_json or {}

    if ref.type == "character":
        return await _export_character(ref, meta, req, db, user_id)
    if ref.type == "location":
        return await _export_scene(ref, meta, req, db, user_id)
    raise HTTPException(status_code=400, detail=f"类型 '{ref.type}' 不支持导出到全局库")


async def _export_character(
    ref: ReferenceItem,
    meta: dict,
    req: ExportGlobalRequest,
    db: AsyncSession,
    user_id: str,
) -> ExportGlobalResponse:
    linked_id = meta.get("global_character_id")

    if req.action == "preview":
        if linked_id:
            result = await db.execute(
                select(Character).where(Character.id == linked_id, project_refs.global_character_filter(user_id))
            )
            char = result.scalar_one_or_none()
            if char:
                return ExportGlobalResponse(
                    status="preview",
                    match=ExportGlobalMatch(
                        global_id=char.id,
                        global_name=char.name,
                        global_type="character",
                    ),
                )
        result = await db.execute(
            select(Character).where(
                func.lower(Character.name) == ref.name.strip().lower(),
                project_refs.global_character_filter(user_id),
            )
        )
        match = result.scalar_one_or_none()
        if match:
            return ExportGlobalResponse(
                status="preview",
                match=ExportGlobalMatch(
                    global_id=match.id,
                    global_name=match.name,
                    global_type="character",
                ),
            )
        return ExportGlobalResponse(status="no_match")

    char_data = project_refs.character_data_from_reference(ref, meta)

    if req.action == "update":
        target_id = req.global_id or linked_id
        if not target_id:
            raise HTTPException(status_code=400, detail="更新操作需要指定 global_id")
        result = await db.execute(
            select(Character).where(Character.id == target_id, Character.user_id == user_id)
        )
        char = result.scalar_one_or_none()
        if not char:
            raise HTTPException(status_code=404, detail="全局角色不存在")
        for k, v in char_data.items():
            setattr(char, k, v)
        ref.metadata_json = {**meta, "global_character_id": char.id}
        await db.commit()
        return ExportGlobalResponse(status="updated", global_id=char.id)

    if req.action == "create":
        char = Character(**char_data, user_id=user_id)
        db.add(char)
        await db.flush()
        ref.metadata_json = {**meta, "global_character_id": char.id}
        await db.commit()
        return ExportGlobalResponse(status="created", global_id=char.id)

    raise HTTPException(status_code=400, detail=f"未知操作: {req.action}")


async def _export_scene(
    ref: ReferenceItem,
    meta: dict,
    req: ExportGlobalRequest,
    db: AsyncSession,
    user_id: str,
) -> ExportGlobalResponse:
    linked_id = meta.get("global_scene_id")

    if req.action == "preview":
        if linked_id:
            result = await db.execute(
                select(Scene).where(Scene.id == linked_id, project_refs.global_scene_filter(user_id))
            )
            scene = result.scalar_one_or_none()
            if scene:
                return ExportGlobalResponse(
                    status="preview",
                    match=ExportGlobalMatch(
                        global_id=scene.id,
                        global_name=scene.name,
                        global_type="scene",
                    ),
                )
        result = await db.execute(
            select(Scene).where(
                func.lower(Scene.name) == ref.name.strip().lower(),
                project_refs.global_scene_filter(user_id),
            )
        )
        match = result.scalar_one_or_none()
        if match:
            return ExportGlobalResponse(
                status="preview",
                match=ExportGlobalMatch(
                    global_id=match.id,
                    global_name=match.name,
                    global_type="scene",
                ),
            )
        return ExportGlobalResponse(status="no_match")

    scene_data = project_refs.scene_data_from_reference(ref, meta)

    if req.action == "update":
        target_id = req.global_id or linked_id
        if not target_id:
            raise HTTPException(status_code=400, detail="更新操作需要指定 global_id")
        result = await db.execute(select(Scene).where(Scene.id == target_id, Scene.user_id == user_id))
        scene = result.scalar_one_or_none()
        if not scene:
            raise HTTPException(status_code=404, detail="全局场景不存在")
        for k, v in scene_data.items():
            setattr(scene, k, v)
        ref.metadata_json = {**meta, "global_scene_id": scene.id}
        await db.commit()
        return ExportGlobalResponse(status="updated", global_id=scene.id)

    if req.action == "create":
        scene = Scene(**scene_data, user_id=user_id)
        db.add(scene)
        await db.flush()
        ref.metadata_json = {**meta, "global_scene_id": scene.id}
        await db.commit()
        return ExportGlobalResponse(status="created", global_id=scene.id)

    raise HTTPException(status_code=400, detail=f"未知操作: {req.action}")


async def sync_reference_from_global(
    db: AsyncSession,
    project_id: str,
    ref_id: str,
    user_id: str,
) -> ReferenceItemResponse:
    await load_project(db, project_id, user_id)
    ref = await get_reference(db, project_id, ref_id)
    meta = ref.metadata_json or {}
    preserved = project_refs.project_character_metadata(meta)

    global_char_id = meta.get("global_character_id")
    global_scene_id = meta.get("global_scene_id")

    if global_char_id:
        result = await db.execute(
            select(Character).where(Character.id == global_char_id, project_refs.global_character_filter(user_id))
        )
        char = result.scalar_one_or_none()
        if not char:
            raise HTTPException(status_code=404, detail="全局角色已被删除")
        ref.name = char.name
        ref.content = char.background or ""
        ref.metadata_json = {**project_refs.character_metadata(char), **preserved}
    elif global_scene_id:
        result = await db.execute(
            select(Scene).where(Scene.id == global_scene_id, project_refs.global_scene_filter(user_id))
        )
        scene = result.scalar_one_or_none()
        if not scene:
            raise HTTPException(status_code=404, detail="全局场景已被删除")
        ref.name = scene.name
        ref.content = scene.description or ""
        ref.metadata_json = {**project_refs.scene_metadata(scene), **preserved}
    else:
        raise HTTPException(status_code=400, detail="此资料未关联全局库")

    await db.commit()
    await db.refresh(ref)
    return ReferenceItemResponse.model_validate(ref)


async def get_document_references(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
) -> list[ReferenceItemResponse]:
    await load_project(db, project_id, user_id)
    doc = await get_document(db, project_id, document_id, with_references=True)
    return [ReferenceItemResponse.model_validate(link.reference) for link in doc.reference_links]


async def set_document_references(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentReferencesUpdate,
) -> dict[str, bool]:
    await load_project(db, project_id, user_id)
    doc = await get_document(db, project_id, document_id, with_references=True)

    for link in list(doc.reference_links):
        await db.delete(link)
    await db.flush()

    if req.reference_ids:
        ref_result = await db.execute(
            select(ReferenceItem.id).where(
                ReferenceItem.project_id == project_id,
                ReferenceItem.id.in_(req.reference_ids),
            )
        )
        valid_ids = {row[0] for row in ref_result.fetchall()}
        invalid_ids = set(req.reference_ids) - valid_ids
        if invalid_ids:
            raise HTTPException(status_code=404, detail="部分参考资料不存在")
    for rid in req.reference_ids:
        db.add(DocumentReference(document_id=document_id, reference_id=rid))
    await db.commit()
    return {"ok": True}
