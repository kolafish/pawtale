import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.auth import get_current_user
from app.database import get_db
from app.domain.projects.templates import initialize_project_template
from app.models.projects import (
    Project, DocumentGroup, Document, ReferenceItem, DocumentReference, WritingSession,
)
from app.models.models import Character, Scene
from app.models.users import User
from app.schemas.projects import (
    ProjectCreate, ProjectUpdate, ProjectListItem, ProjectResponse,
    GroupCreate, GroupUpdate, GroupResponse, GroupReorderItem,
    DocumentCreate, DocumentUpdate, DocumentResponse, DocumentReorderItem,
    ReferenceItemCreate, ReferenceItemUpdate, ReferenceItemResponse, ReferenceItemBrief,
    ImportGlobalRequest, DocumentReferencesUpdate,
    WritingSessionCreate, WritingSessionResponse,
    ProjectExportResponse,
    DocumentAIContinueRequest, DocumentAIContinueResponse,
    DocumentAIReviseRequest, DocumentAIReviseResponse,
    DocumentAIReviseMultiRequest, DocumentAIReviseMultiResultItem, DocumentAIReviseMultiResponse,
    ProjectAnalyzeRequest, ProjectAnalyzeResponse,
    AnalyzeApplyRequest, AnalyzeApplyResponse, AnalyzedCharacter, AnalyzedOutline, AnalyzedSetting,
    ExportGlobalRequest, ExportGlobalResponse, ExportGlobalMatch,
    ImportPreviewItem, ImportPreviewResponse,
)
from app.services import get_ai_service
from app.services import project_documents as project_docs
from app.services import project_references as project_refs
from app.services.content_storage import read_content

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _load_project(db: AsyncSession, project_id: str, user_id: str) -> Project:
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id, Project.user_id == user_id)
        .options(
            selectinload(Project.groups).selectinload(DocumentGroup.documents),
            selectinload(Project.references),
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


# ── Project CRUD ─────────────────────────────────────

@router.get("", response_model=list[ProjectListItem])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Project)
        .where(Project.user_id == current_user.id)
        .options(selectinload(Project.groups).selectinload(DocumentGroup.documents))
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()
    return [
        ProjectListItem(
            id=p.id,
            type=p.type,
            title=p.title,
            description=p.description,
            word_count_target=p.word_count_target,
            total_word_count=project_docs.project_total_words(p),
            group_count=len(p.groups or []),
            document_count=sum(len(g.documents or []) for g in (p.groups or [])),
            updated_at=p.updated_at,
        )
        for p in projects
    ]


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    req: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = Project(
        user_id=current_user.id,
        type=req.type.value,
        title=req.title,
        description=req.description,
        word_count_target=req.word_count_target,
        daily_word_goal=req.daily_word_goal,
    )
    db.add(project)
    await db.flush()

    await initialize_project_template(db, project)
    project = await _load_project(db, project.id, current_user.id)
    return project_docs.project_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id, current_user.id)
    return project_docs.project_response(project)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    req: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id, current_user.id)
    for field in ["title", "description", "word_count_target", "daily_word_goal"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(project, field, val)
    await db.commit()
    project = await _load_project(db, project.id, current_user.id)
    return project_docs.project_response(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id, current_user.id)
    await project_docs.delete_project(db, project)


# ── Groups ───────────────────────────────────────────

@router.post("/{project_id}/groups", response_model=GroupResponse, status_code=201)
async def create_group(
    project_id: str,
    req: GroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id, current_user.id)
    max_order = max((g.sort_order for g in project.groups), default=-1)
    group = DocumentGroup(project_id=project_id, name=req.name, sort_order=max_order + 1)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return GroupResponse(id=group.id, name=group.name, sort_order=group.sort_order, documents=[])


@router.put("/{project_id}/groups/reorder", status_code=200)
async def reorder_groups(
    project_id: str,
    items: list[GroupReorderItem],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    for item in items:
        result = await db.execute(
            select(DocumentGroup).where(DocumentGroup.id == item.id, DocumentGroup.project_id == project_id)
        )
        group = result.scalar_one_or_none()
        if group:
            group.sort_order = item.sort_order
    await db.commit()
    return {"ok": True}


@router.put("/{project_id}/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    project_id: str,
    group_id: str,
    req: GroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(DocumentGroup)
        .where(DocumentGroup.id == group_id, DocumentGroup.project_id == project_id)
        .options(selectinload(DocumentGroup.documents))
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")
    if req.name is not None:
        group.name = req.name
    await db.commit()
    await db.refresh(group)
    return project_docs.group_response(group)


@router.delete("/{project_id}/groups/{group_id}", status_code=204)
async def delete_group(
    project_id: str,
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(DocumentGroup)
        .where(DocumentGroup.id == group_id, DocumentGroup.project_id == project_id)
        .options(selectinload(DocumentGroup.documents))
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")
    await project_docs.delete_group(db, project_id, group)


# ── Documents ────────────────────────────────────────

@router.post(
    "/{project_id}/groups/{group_id}/documents",
    response_model=DocumentResponse,
    status_code=201,
)
async def create_document(
    project_id: str,
    group_id: str,
    req: DocumentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(DocumentGroup).where(DocumentGroup.id == group_id, DocumentGroup.project_id == project_id)
        .options(selectinload(DocumentGroup.documents))
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")
    max_order = max((d.sort_order for d in group.documents), default=-1)
    doc = Document(group_id=group_id, name=req.name, sort_order=max_order + 1, word_count=0)
    db.add(doc)
    await db.flush()
    await project_docs.commit_created_document(db, project_id, doc)
    await db.refresh(doc)
    return project_docs.document_response(doc, "")


@router.put("/{project_id}/documents/reorder", status_code=200)
async def reorder_documents(
    project_id: str,
    items: list[DocumentReorderItem],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    for item in items:
        result = await db.execute(
            select(Document)
            .join(DocumentGroup)
            .where(Document.id == item.id, DocumentGroup.project_id == project_id)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.group_id = item.group_id
            doc.sort_order = item.sort_order
    await db.commit()
    return {"ok": True}


@router.get("/{project_id}/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    project_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    content = await read_content(project_id, doc.id)
    return project_docs.document_response(doc, content)


@router.put("/{project_id}/documents/{document_id}", response_model=DocumentResponse)
async def update_document(
    project_id: str,
    document_id: str,
    req: DocumentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    for field in ["name", "synopsis", "status", "label", "word_count_target"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(doc, field, val)
    await project_docs.commit_document_update(db, project_id, doc, req.content)
    await db.refresh(doc)
    content = await read_content(project_id, doc.id)
    return project_docs.document_response(doc, content)


@router.delete("/{project_id}/documents/{document_id}", status_code=204)
async def delete_document(
    project_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await project_docs.delete_document(db, project_id, doc)


# ── References ───────────────────────────────────────

@router.get("/{project_id}/references", response_model=list[ReferenceItemResponse])
async def list_references(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(ReferenceItem)
        .where(ReferenceItem.project_id == project_id)
        .order_by(ReferenceItem.sort_order)
    )
    return [
        ReferenceItemResponse.model_validate(r) for r in result.scalars().all()
    ]


@router.post("/{project_id}/references", response_model=ReferenceItemResponse, status_code=201)
async def create_reference(
    project_id: str,
    req: ReferenceItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # verify project exists
    await _load_project(db, project_id, current_user.id)
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


@router.put("/{project_id}/references/{ref_id}", response_model=ReferenceItemResponse)
async def update_reference(
    project_id: str,
    ref_id: str,
    req: ReferenceItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.id == ref_id, ReferenceItem.project_id == project_id)
    )
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="参考资料不存在")
    for field in ["name", "content", "metadata_json"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(ref, field, val)
    await db.commit()
    await db.refresh(ref)
    return ReferenceItemResponse.model_validate(ref)


@router.delete("/{project_id}/references/{ref_id}", status_code=204)
async def delete_reference(
    project_id: str,
    ref_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.id == ref_id, ReferenceItem.project_id == project_id)
    )
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="参考资料不存在")
    await db.delete(ref)
    await db.commit()


@router.post("/{project_id}/references/import-global", response_model=list[ReferenceItemResponse])
async def import_from_global(
    project_id: str,
    req: ImportGlobalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    created = []
    for cid in req.character_ids:
        result = await db.execute(
            select(Character).where(Character.id == cid, project_refs.global_character_filter(current_user.id))
        )
        char = result.scalar_one_or_none()
        if not char:
            continue
        ref = project_refs.character_reference(project_id, char)
        db.add(ref)
        created.append(ref)
    for sid in req.scene_ids:
        result = await db.execute(
            select(Scene).where(Scene.id == sid, project_refs.global_scene_filter(current_user.id))
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


# ── Import Preview (duplicate detection) ───────────

@router.post("/{project_id}/references/import-preview", response_model=ImportPreviewResponse)
async def import_preview(
    project_id: str,
    req: ImportGlobalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preview import: check for duplicates before importing from global library."""
    await _load_project(db, project_id, current_user.id)

    # Load all project references for matching
    refs_result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.project_id == project_id)
    )
    project_ref_items = refs_result.scalars().all()

    items: list[ImportPreviewItem] = []

    for cid in req.character_ids:
        result = await db.execute(
            select(Character).where(Character.id == cid, project_refs.global_character_filter(current_user.id))
        )
        char = result.scalar_one_or_none()
        if not char:
            continue
        existing = project_refs.find_matching_reference(project_ref_items, "character", char.id, char.name)
        items.append(ImportPreviewItem(
            global_id=char.id,
            global_name=char.name,
            global_type="character",
            existing_ref_id=existing["ref_id"] if existing else None,
            existing_ref_name=existing["ref_name"] if existing else None,
            match_by=existing["match_by"] if existing else None,
        ))

    for sid in req.scene_ids:
        result = await db.execute(
            select(Scene).where(Scene.id == sid, project_refs.global_scene_filter(current_user.id))
        )
        scene = result.scalar_one_or_none()
        if not scene:
            continue
        existing = project_refs.find_matching_reference(project_ref_items, "location", scene.id, scene.name)
        items.append(ImportPreviewItem(
            global_id=scene.id,
            global_name=scene.name,
            global_type="scene",
            existing_ref_id=existing["ref_id"] if existing else None,
            existing_ref_name=existing["ref_name"] if existing else None,
            match_by=existing["match_by"] if existing else None,
        ))

    return ImportPreviewResponse(items=items)

# ── Export Reference to Global Library ────────────

@router.post("/{project_id}/references/{ref_id}/export-global", response_model=ExportGlobalResponse)
async def export_to_global(
    project_id: str,
    ref_id: str,
    req: ExportGlobalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export a project reference to the global library (character/scene)."""
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.id == ref_id, ReferenceItem.project_id == project_id)
    )
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="参考资料不存在")

    meta = ref.metadata_json or {}

    if ref.type == "character":
        return await _export_character(ref, meta, req, db, current_user.id)
    elif ref.type == "location":
        return await _export_scene(ref, meta, req, db, current_user.id)
    else:
        raise HTTPException(status_code=400, detail=f"类型 '{ref.type}' 不支持导出到全局库")


async def _export_character(
    ref: ReferenceItem, meta: dict, req: ExportGlobalRequest, db: AsyncSession, user_id: str,
) -> ExportGlobalResponse:
    """Handle export of a character reference to global Character table."""
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
                    match=ExportGlobalMatch(global_id=char.id, global_name=char.name, global_type="character"),
                )
        # Name match
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
                match=ExportGlobalMatch(global_id=match.id, global_name=match.name, global_type="character"),
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

    elif req.action == "create":
        char = Character(**char_data, user_id=user_id)
        db.add(char)
        await db.flush()
        ref.metadata_json = {**meta, "global_character_id": char.id}
        await db.commit()
        return ExportGlobalResponse(status="created", global_id=char.id)

    raise HTTPException(status_code=400, detail=f"未知操作: {req.action}")


async def _export_scene(
    ref: ReferenceItem, meta: dict, req: ExportGlobalRequest, db: AsyncSession, user_id: str,
) -> ExportGlobalResponse:
    """Handle export of a location reference to global Scene table."""
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
                    match=ExportGlobalMatch(global_id=scene.id, global_name=scene.name, global_type="scene"),
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
                match=ExportGlobalMatch(global_id=match.id, global_name=match.name, global_type="scene"),
            )
        return ExportGlobalResponse(status="no_match")

    scene_data = project_refs.scene_data_from_reference(ref, meta)

    if req.action == "update":
        target_id = req.global_id or linked_id
        if not target_id:
            raise HTTPException(status_code=400, detail="更新操作需要指定 global_id")
        result = await db.execute(
            select(Scene).where(Scene.id == target_id, Scene.user_id == user_id)
        )
        scene = result.scalar_one_or_none()
        if not scene:
            raise HTTPException(status_code=404, detail="全局场景不存在")
        for k, v in scene_data.items():
            setattr(scene, k, v)
        ref.metadata_json = {**meta, "global_scene_id": scene.id}
        await db.commit()
        return ExportGlobalResponse(status="updated", global_id=scene.id)

    elif req.action == "create":
        scene = Scene(**scene_data, user_id=user_id)
        db.add(scene)
        await db.flush()
        ref.metadata_json = {**meta, "global_scene_id": scene.id}
        await db.commit()
        return ExportGlobalResponse(status="created", global_id=scene.id)

    raise HTTPException(status_code=400, detail=f"未知操作: {req.action}")


# ── Sync Reference from Global Library ──────────────

@router.post("/{project_id}/references/{ref_id}/sync-global", response_model=ReferenceItemResponse)
async def sync_reference_from_global(
    project_id: str,
    ref_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.id == ref_id, ReferenceItem.project_id == project_id)
    )
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="参考资料不存在")

    meta = ref.metadata_json or {}
    preserved = project_refs.project_character_metadata(meta)

    global_char_id = meta.get("global_character_id")
    global_scene_id = meta.get("global_scene_id")

    if global_char_id:
        result = await db.execute(
            select(Character).where(Character.id == global_char_id, project_refs.global_character_filter(current_user.id))
        )
        char = result.scalar_one_or_none()
        if not char:
            raise HTTPException(status_code=404, detail="全局角色已被删除")
        ref.name = char.name
        ref.content = char.background or ""
        ref.metadata_json = {**project_refs.character_metadata(char), **preserved}
    elif global_scene_id:
        result = await db.execute(
            select(Scene).where(Scene.id == global_scene_id, project_refs.global_scene_filter(current_user.id))
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


# ── Document-Reference Links ────────────────────────

@router.get(
    "/{project_id}/documents/{document_id}/references",
    response_model=list[ReferenceItemResponse],
)
async def get_document_references(
    project_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    # verify doc belongs to project
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
        .options(selectinload(Document.reference_links).selectinload(DocumentReference.reference))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return [
        ReferenceItemResponse.model_validate(link.reference)
        for link in doc.reference_links
    ]


@router.put("/{project_id}/documents/{document_id}/references", status_code=200)
async def set_document_references(
    project_id: str, document_id: str, req: DocumentReferencesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
        .options(selectinload(Document.reference_links))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    # Remove old links
    for link in list(doc.reference_links):
        await db.delete(link)
    await db.flush()
    # Add new links
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
        link = DocumentReference(document_id=document_id, reference_id=rid)
        db.add(link)
    await db.commit()
    return {"ok": True}


# ── Writing Sessions ────────────────────────────────

@router.get("/{project_id}/sessions", response_model=list[WritingSessionResponse])
async def list_sessions(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(WritingSession)
        .where(WritingSession.project_id == project_id)
        .order_by(WritingSession.date.desc())
        .limit(90)
    )
    return [WritingSessionResponse.model_validate(s) for s in result.scalars().all()]


@router.post("/{project_id}/sessions", response_model=WritingSessionResponse, status_code=201)
async def create_session(
    project_id: str,
    req: WritingSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    session = WritingSession(
        project_id=project_id,
        date=req.date,
        words_written=req.words_written,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return WritingSessionResponse.model_validate(session)


# ── Export ───────────────────────────────────────────

@router.get("/{project_id}/export", response_model=ProjectExportResponse)
async def export_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id, current_user.id)
    sections = []
    total = 0
    for g in project.groups:
        docs = []
        for d in g.documents:
            content = await read_content(project_id, d.id)
            wc = project_docs.count_words(content)
            total += wc
            docs.append({"name": d.name, "content": content, "word_count": wc})
        sections.append({"group": g.name, "documents": docs})
    return ProjectExportResponse(title=project.title, sections=sections, total_words=total)


# ── Project Analysis (AI extract from manuscript) ──

@router.post("/{project_id}/analyze", response_model=ProjectAnalyzeResponse, response_model_exclude_none=True)
async def analyze_project(
    project_id: str, req: ProjectAnalyzeRequest, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id, current_user.id)

    # Gather all manuscript content (full text for word count)
    raw_parts: list[tuple[str, str]] = []  # (header, content)
    for g in project.groups:
        for d in (g.documents or []):
            content = await read_content(project_id, d.id)
            if content.strip():
                raw_parts.append((f"【{g.name} - {d.name}】", content.strip()))

    if not raw_parts:
        raise HTTPException(status_code=400, detail="项目中没有手稿内容可供分析")

    full_combined = "\n\n".join(f"{h}\n{c}" for h, c in raw_parts)
    total_words = project_docs.count_words(full_combined)

    # Truncate each chapter proportionally to fit within AI context limits
    # Claude proxy has input size constraints; keep total content reasonable
    max_total_chars = 4000
    total_raw = sum(len(c) for _, c in raw_parts)
    if total_raw > max_total_chars:
        per_doc = max(500, max_total_chars // len(raw_parts))
        clipped_parts = [f"{h}\n{c[:per_doc]}..." for h, c in raw_parts]
    else:
        clipped_parts = [f"{h}\n{c}" for h, c in raw_parts]
    combined = "\n\n---\n\n".join(clipped_parts)

    selected_ai_model = req.ai_model or "claude-opus-4"
    ai_service = get_ai_service(selected_ai_model)

    try:
        result = await ai_service.analyze_project_content(combined, debug=req.debug)
    except Exception as e:
        logger.exception("AI analyze failed for project=%s", project_id)
        raise HTTPException(status_code=500, detail=f"AI分析失败: {str(e)}")

    return ProjectAnalyzeResponse(
        characters=[AnalyzedCharacter(**c) for c in result.get("characters", [])],
        outlines=[AnalyzedOutline(**o) for o in result.get("outlines", [])],
        settings=[AnalyzedSetting(**s) for s in result.get("settings", [])],
        total_words_analyzed=total_words,
        debug_info=result.get("debug_info"),
    )


@router.post("/{project_id}/analyze/apply", response_model=AnalyzeApplyResponse)
async def apply_analysis(
    project_id: str, req: AnalyzeApplyRequest, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)

    created: list[ReferenceItem] = []

    for char in req.characters:
        ref = ReferenceItem(
            project_id=project_id,
            type="character",
            name=char.name,
            content=char.summary or "",
            metadata_json={
                "gender": char.gender,
                "identity": char.identity,
                "personality": char.personality or [],
                "appearance": char.appearance,
                "relationships": char.relationships,
            },
            sort_order=len(created),
        )
        db.add(ref)
        created.append(ref)

    for outline in req.outlines:
        ref = ReferenceItem(
            project_id=project_id,
            type="outline",
            name=outline.name,
            content=outline.content,
            metadata_json={},
            sort_order=len(created),
        )
        db.add(ref)
        created.append(ref)

    for setting in req.settings:
        ref = ReferenceItem(
            project_id=project_id,
            type=setting.type if setting.type in ("location", "note") else "note",
            name=setting.name,
            content=setting.content,
            metadata_json={},
            sort_order=len(created),
        )
        db.add(ref)
        created.append(ref)

    await db.commit()
    for ref in created:
        await db.refresh(ref)

    return AnalyzeApplyResponse(
        created_count=len(created),
        references=[
            ReferenceItemBrief(id=r.id, type=r.type, name=r.name, sort_order=r.sort_order)
            for r in created
        ],
    )


# ── AI Continue ─────────────────────────────────────

@router.post(
    "/{project_id}/documents/{document_id}/ai-continue",
    response_model=DocumentAIContinueResponse,
    response_model_exclude_none=True,
)
async def ai_continue_document(
    project_id: str, document_id: str,
    req: DocumentAIContinueRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    content = await read_content(project_id, doc.id)
    base_content = content.strip()
    if not base_content:
        raise HTTPException(status_code=400, detail="请先写一些内容")

    selected_ai_model = req.ai_model or "claude-opus-4"
    ai_service = get_ai_service(selected_ai_model)

    scene_payload = {
        "name": "当前文本",
        "description": "基于已有正文续写",
        "location": None,
        "time_weather": None,
        "sensory_details": {},
        "props": [],
    }

    try:
        story_result = await ai_service.generate_story(
            characters=[],
            scene=scene_payload,
            mood="甜",
            writing_style="细腻",
            chapter_length="中",
            previous_content=base_content[-4000:],
            custom_direction=req.instruction,
            chapter_number=1,
            debug=req.debug,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI续写失败: {str(e)}")

    generated_chunk = (story_result.get("content") or "").strip()
    if not generated_chunk:
        raise HTTPException(status_code=502, detail="AI未返回可用续写内容")

    new_content = f"{content.rstrip()}\n\n{generated_chunk}"
    await project_docs.commit_document_update(db, project_id, doc, new_content)

    return DocumentAIContinueResponse(
        document_id=doc.id,
        content=new_content,
        generated_chunk=generated_chunk,
        ai_model=selected_ai_model,
        debug_info=story_result.get("debug_info") if req.debug else None,
    )


# ── AI Revise ──────────────────────────────────────

@router.post(
    "/{project_id}/documents/{document_id}/ai-revise",
    response_model=DocumentAIReviseResponse,
    response_model_exclude_none=True,
)
async def ai_revise_document(
    project_id: str, document_id: str,
    req: DocumentAIReviseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    selected_ai_model = req.ai_model or "claude-opus-4"
    ai_service = get_ai_service(selected_ai_model)

    if req.selected_text:
        original_text = req.selected_text
        scope = "selection"
    else:
        content = await read_content(project_id, doc.id)
        original_text = content.strip()
        scope = "full"

    if not original_text:
        raise HTTPException(status_code=400, detail="没有可修改的内容")

    try:
        revise_result = await ai_service.revise_chapter(
            original_content=original_text,
            revision_instruction=req.instruction,
            characters=[],
            writing_style="细腻",
            chapter_length="长",
            debug=req.debug,
        )
    except Exception as e:
        logger.exception("AI revise failed for doc=%s project=%s", document_id, project_id)
        raise HTTPException(status_code=500, detail=f"AI修改失败: {str(e)}")

    revised_text = (revise_result.get("content") or "").strip()
    if not revised_text:
        raise HTTPException(status_code=502, detail="AI未返回可用修改内容")

    # Do NOT save to database — return as a proposal for the user to accept/reject
    return DocumentAIReviseResponse(
        document_id=doc.id,
        revised_text=revised_text,
        original_text=original_text,
        scope=scope,
        ai_model=selected_ai_model,
        debug_info=revise_result.get("debug_info") if req.debug else None,
    )


# ── AI Revise Multi-model ─────────────────────────

@router.post(
    "/{project_id}/documents/{document_id}/ai-revise-multi",
    response_model=DocumentAIReviseMultiResponse,
    response_model_exclude_none=True,
)
async def ai_revise_document_multi(
    project_id: str, document_id: str,
    req: DocumentAIReviseMultiRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _load_project(db, project_id, current_user.id)
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    if req.selected_text:
        original_text = req.selected_text
        scope = "selection"
    else:
        content = await read_content(project_id, doc.id)
        original_text = content.strip()
        scope = "full"

    if not original_text:
        raise HTTPException(status_code=400, detail="没有可修改的内容")

    async def _revise_with_model(model: str) -> DocumentAIReviseMultiResultItem:
        try:
            ai_service = get_ai_service(model)
            revise_result = await ai_service.revise_chapter(
                original_content=original_text,
                revision_instruction=req.instruction,
                characters=[],
                writing_style="细腻",
                chapter_length="长",
                debug=req.debug,
            )
            revised = (revise_result.get("content") or "").strip()
            if not revised:
                return DocumentAIReviseMultiResultItem(ai_model=model, error="AI未返回可用修改内容")
            return DocumentAIReviseMultiResultItem(
                ai_model=model,
                revised_text=revised,
                debug_info=revise_result.get("debug_info") if req.debug else None,
            )
        except Exception as e:
            logger.exception("AI revise-multi failed for model=%s doc=%s", model, document_id)
            return DocumentAIReviseMultiResultItem(ai_model=model, error=str(e))

    results = await asyncio.gather(*[_revise_with_model(m) for m in req.models])

    return DocumentAIReviseMultiResponse(
        document_id=doc.id,
        original_text=original_text,
        scope=scope,
        results=list(results),
    )
