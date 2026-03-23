import re
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models.projects import (
    Project, DocumentGroup, Document, ReferenceItem, DocumentReference, WritingSession,
)
from app.models.models import Character, Scene
from app.schemas.projects import (
    ProjectCreate, ProjectUpdate, ProjectListItem, ProjectResponse,
    GroupCreate, GroupUpdate, GroupResponse, GroupReorderItem,
    DocumentCreate, DocumentUpdate, DocumentResponse, DocumentMeta, DocumentReorderItem,
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
from app.services.content_storage import (
    read_content, write_content, delete_content,
    ensure_project_dir, delete_project_dir, delete_group_documents,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _count_words(text: str) -> int:
    """Count words: Chinese characters + English words."""
    if not text:
        return 0
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(re.findall(r'[a-zA-Z]+', text))
    return chinese + english


def _project_total_words(project: Project) -> int:
    total = 0
    for g in (project.groups or []):
        for d in (g.documents or []):
            total += d.word_count
    return total


def _doc_meta(doc: Document) -> DocumentMeta:
    return DocumentMeta(
        id=doc.id,
        group_id=doc.group_id,
        name=doc.name,
        status=doc.status or "draft",
        label=doc.label,
        word_count=doc.word_count,
        sort_order=doc.sort_order,
        updated_at=doc.updated_at,
    )


def _group_resp(g: DocumentGroup) -> GroupResponse:
    return GroupResponse(
        id=g.id,
        name=g.name,
        sort_order=g.sort_order,
        documents=[_doc_meta(d) for d in (g.documents or [])],
    )


def _project_resp(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        title=project.title,
        description=project.description,
        word_count_target=project.word_count_target,
        daily_word_goal=project.daily_word_goal,
        total_word_count=_project_total_words(project),
        groups=[_group_resp(g) for g in (project.groups or [])],
        references=[
            ReferenceItemBrief(
                id=r.id, type=r.type, name=r.name, sort_order=r.sort_order,
                global_id=(r.metadata_json or {}).get("global_character_id") or (r.metadata_json or {}).get("global_scene_id"),
            )
            for r in (project.references or [])
        ],
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


async def _load_project(db: AsyncSession, project_id: str) -> Project:
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id)
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
async def list_projects(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.groups).selectinload(DocumentGroup.documents))
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()
    return [
        ProjectListItem(
            id=p.id,
            title=p.title,
            description=p.description,
            word_count_target=p.word_count_target,
            total_word_count=_project_total_words(p),
            group_count=len(p.groups or []),
            document_count=sum(len(g.documents or []) for g in (p.groups or [])),
            updated_at=p.updated_at,
        )
        for p in projects
    ]


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(req: ProjectCreate, db: AsyncSession = Depends(get_db)):
    project = Project(
        title=req.title,
        description=req.description,
        word_count_target=req.word_count_target,
        daily_word_goal=req.daily_word_goal,
    )
    db.add(project)
    await db.flush()

    group = DocumentGroup(project_id=project.id, name="第一卷", sort_order=0)
    db.add(group)
    await db.flush()

    doc = Document(group_id=group.id, name="未命名文档", sort_order=0, word_count=0)
    db.add(doc)

    await db.commit()
    await ensure_project_dir(project.id)
    await write_content(project.id, doc.id, "")
    project = await _load_project(db, project.id)
    return _project_resp(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    project = await _load_project(db, project_id)
    return _project_resp(project)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, req: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    project = await _load_project(db, project_id)
    for field in ["title", "description", "word_count_target", "daily_word_goal"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(project, field, val)
    await db.commit()
    project = await _load_project(db, project.id)
    return _project_resp(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    project = await _load_project(db, project_id)
    await db.delete(project)
    await db.commit()
    await delete_project_dir(project_id)


# ── Groups ───────────────────────────────────────────

@router.post("/{project_id}/groups", response_model=GroupResponse, status_code=201)
async def create_group(project_id: str, req: GroupCreate, db: AsyncSession = Depends(get_db)):
    project = await _load_project(db, project_id)
    max_order = max((g.sort_order for g in project.groups), default=-1)
    group = DocumentGroup(project_id=project_id, name=req.name, sort_order=max_order + 1)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return GroupResponse(id=group.id, name=group.name, sort_order=group.sort_order, documents=[])


@router.put("/{project_id}/groups/reorder", status_code=200)
async def reorder_groups(project_id: str, items: list[GroupReorderItem], db: AsyncSession = Depends(get_db)):
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
async def update_group(project_id: str, group_id: str, req: GroupUpdate, db: AsyncSession = Depends(get_db)):
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
    return _group_resp(group)


@router.delete("/{project_id}/groups/{group_id}", status_code=204)
async def delete_group(project_id: str, group_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DocumentGroup)
        .where(DocumentGroup.id == group_id, DocumentGroup.project_id == project_id)
        .options(selectinload(DocumentGroup.documents))
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")
    doc_ids = [d.id for d in group.documents]
    await db.delete(group)
    await db.commit()
    await delete_group_documents(project_id, doc_ids)


# ── Documents ────────────────────────────────────────

@router.post(
    "/{project_id}/groups/{group_id}/documents",
    response_model=DocumentResponse,
    status_code=201,
)
async def create_document(
    project_id: str, group_id: str, req: DocumentCreate, db: AsyncSession = Depends(get_db),
):
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
    await db.commit()
    await db.refresh(doc)
    await write_content(project_id, doc.id, "")
    return DocumentResponse(
        id=doc.id, group_id=doc.group_id, name=doc.name, content="",
        synopsis=doc.synopsis, status=doc.status, label=doc.label,
        word_count_target=doc.word_count_target, word_count=0,
        sort_order=doc.sort_order, created_at=doc.created_at, updated_at=doc.updated_at,
    )


@router.put("/{project_id}/documents/reorder", status_code=200)
async def reorder_documents(
    project_id: str, items: list[DocumentReorderItem], db: AsyncSession = Depends(get_db),
):
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
async def get_document(project_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    content = await read_content(project_id, doc.id)
    return DocumentResponse(
        id=doc.id, group_id=doc.group_id, name=doc.name, content=content,
        synopsis=doc.synopsis, status=doc.status, label=doc.label,
        word_count_target=doc.word_count_target, word_count=doc.word_count,
        sort_order=doc.sort_order, created_at=doc.created_at, updated_at=doc.updated_at,
    )


@router.put("/{project_id}/documents/{document_id}", response_model=DocumentResponse)
async def update_document(
    project_id: str, document_id: str, req: DocumentUpdate, db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if req.content is not None:
        await write_content(project_id, doc.id, req.content)
        doc.word_count = _count_words(req.content)
    for field in ["name", "synopsis", "status", "label", "word_count_target"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(doc, field, val)
    await db.commit()
    await db.refresh(doc)
    content = await read_content(project_id, doc.id)
    return DocumentResponse(
        id=doc.id, group_id=doc.group_id, name=doc.name, content=content,
        synopsis=doc.synopsis, status=doc.status, label=doc.label,
        word_count_target=doc.word_count_target, word_count=doc.word_count,
        sort_order=doc.sort_order, created_at=doc.created_at, updated_at=doc.updated_at,
    )


@router.delete("/{project_id}/documents/{document_id}", status_code=204)
async def delete_document(project_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await db.delete(doc)
    await db.commit()
    await delete_content(project_id, document_id)


# ── References ───────────────────────────────────────

@router.get("/{project_id}/references", response_model=list[ReferenceItemResponse])
async def list_references(project_id: str, db: AsyncSession = Depends(get_db)):
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
    project_id: str, req: ReferenceItemCreate, db: AsyncSession = Depends(get_db),
):
    # verify project exists
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
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
    project_id: str, ref_id: str, req: ReferenceItemUpdate, db: AsyncSession = Depends(get_db),
):
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
async def delete_reference(project_id: str, ref_id: str, db: AsyncSession = Depends(get_db)):
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
    project_id: str, req: ImportGlobalRequest, db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
    created = []
    for cid in req.character_ids:
        char = await db.get(Character, cid)
        if not char:
            continue
        ref = ReferenceItem(
            project_id=project_id,
            type="character",
            name=char.name,
            content=char.background or "",
            metadata_json={
                "global_character_id": char.id,
                "gender": char.gender,
                "age": char.age,
                "identity": char.identity,
                "appearance": char.appearance,
                "personality": char.personality or [],
                "speaking_style": char.speaking_style,
                "core_trait": char.core_trait,
                "life_goal": char.life_goal,
                "fatal_flaw": char.fatal_flaw,
                "growth_hint": char.growth_hint,
                "habits": char.habits or [],
                "secret": char.secret,
            },
        )
        db.add(ref)
        created.append(ref)
    for sid in req.scene_ids:
        scene = await db.get(Scene, sid)
        if not scene:
            continue
        ref = ReferenceItem(
            project_id=project_id,
            type="location",
            name=scene.name,
            content=scene.description or "",
            metadata_json={
                "global_scene_id": scene.id,
                "category": scene.category,
                "location": scene.location,
                "time_weather": scene.time_weather,
                "mood_keywords": scene.mood_keywords or [],
                "dramatic_potential": scene.dramatic_potential,
                "sensory_details": scene.sensory_details or {},
                "props": scene.props or [],
            },
        )
        db.add(ref)
        created.append(ref)
    await db.commit()
    for ref in created:
        await db.refresh(ref)
    return [ReferenceItemResponse.model_validate(r) for r in created]


# ── Import Preview (duplicate detection) ───────────

@router.post("/{project_id}/references/import-preview", response_model=ImportPreviewResponse)
async def import_preview(
    project_id: str, req: ImportGlobalRequest, db: AsyncSession = Depends(get_db),
):
    """Preview import: check for duplicates before importing from global library."""
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")

    # Load all project references for matching
    refs_result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.project_id == project_id)
    )
    project_refs = refs_result.scalars().all()

    items: list[ImportPreviewItem] = []

    for cid in req.character_ids:
        char = await db.get(Character, cid)
        if not char:
            continue
        existing = _find_matching_ref(project_refs, "character", char.id, char.name)
        items.append(ImportPreviewItem(
            global_id=char.id,
            global_name=char.name,
            global_type="character",
            existing_ref_id=existing["ref_id"] if existing else None,
            existing_ref_name=existing["ref_name"] if existing else None,
            match_by=existing["match_by"] if existing else None,
        ))

    for sid in req.scene_ids:
        scene = await db.get(Scene, sid)
        if not scene:
            continue
        existing = _find_matching_ref(project_refs, "location", scene.id, scene.name)
        items.append(ImportPreviewItem(
            global_id=scene.id,
            global_name=scene.name,
            global_type="scene",
            existing_ref_id=existing["ref_id"] if existing else None,
            existing_ref_name=existing["ref_name"] if existing else None,
            match_by=existing["match_by"] if existing else None,
        ))

    return ImportPreviewResponse(items=items)


def _find_matching_ref(
    refs: list[ReferenceItem], ref_type: str, global_id: str, name: str,
) -> dict | None:
    """Find existing project reference matching a global record."""
    global_key = "global_character_id" if ref_type == "character" else "global_scene_id"
    # Match by linked global_id first
    for r in refs:
        if r.type == ref_type and (r.metadata_json or {}).get(global_key) == global_id:
            return {"ref_id": r.id, "ref_name": r.name, "match_by": "global_id"}
    # Match by name (case-insensitive)
    for r in refs:
        if r.type == ref_type and r.name.strip().lower() == name.strip().lower():
            return {"ref_id": r.id, "ref_name": r.name, "match_by": "name"}
    return None


# ── Export Reference to Global Library ────────────

@router.post("/{project_id}/references/{ref_id}/export-global", response_model=ExportGlobalResponse)
async def export_to_global(
    project_id: str, ref_id: str, req: ExportGlobalRequest, db: AsyncSession = Depends(get_db),
):
    """Export a project reference to the global library (character/scene)."""
    result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.id == ref_id, ReferenceItem.project_id == project_id)
    )
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="参考资料不存在")

    meta = ref.metadata_json or {}

    if ref.type == "character":
        return await _export_character(ref, meta, req, db)
    elif ref.type == "location":
        return await _export_scene(ref, meta, req, db)
    else:
        raise HTTPException(status_code=400, detail=f"类型 '{ref.type}' 不支持导出到全局库")


async def _export_character(
    ref: ReferenceItem, meta: dict, req: ExportGlobalRequest, db: AsyncSession,
) -> ExportGlobalResponse:
    """Handle export of a character reference to global Character table."""
    # Project-specific fields that should NOT be exported
    project_only_keys = {"story_name", "role_in_story", "relationships", "character_arc",
                         "global_character_id", "global_scene_id"}

    # Check for existing link
    linked_id = meta.get("global_character_id")

    if req.action == "preview":
        # Check linked first
        if linked_id:
            char = await db.get(Character, linked_id)
            if char:
                return ExportGlobalResponse(
                    status="preview",
                    match=ExportGlobalMatch(global_id=char.id, global_name=char.name, global_type="character"),
                )
        # Name match
        result = await db.execute(
            select(Character).where(func.lower(Character.name) == ref.name.strip().lower())
        )
        match = result.scalar_one_or_none()
        if match:
            return ExportGlobalResponse(
                status="preview",
                match=ExportGlobalMatch(global_id=match.id, global_name=match.name, global_type="character"),
            )
        return ExportGlobalResponse(status="no_match")

    # Build character fields from reference
    char_data = {
        "name": ref.name,
        "background": ref.content or "",
        "gender": meta.get("gender"),
        "age": meta.get("age"),
        "identity": meta.get("identity"),
        "appearance": meta.get("appearance"),
        "personality": meta.get("personality") or [],
        "speaking_style": meta.get("speaking_style"),
        "core_trait": meta.get("core_trait"),
        "life_goal": meta.get("life_goal"),
        "fatal_flaw": meta.get("fatal_flaw"),
        "growth_hint": meta.get("growth_hint"),
        "habits": meta.get("habits") or [],
        "secret": meta.get("secret"),
    }

    if req.action == "update":
        target_id = req.global_id or linked_id
        if not target_id:
            raise HTTPException(status_code=400, detail="更新操作需要指定 global_id")
        char = await db.get(Character, target_id)
        if not char:
            raise HTTPException(status_code=404, detail="全局角色不存在")
        for k, v in char_data.items():
            setattr(char, k, v)
        # Link back
        ref.metadata_json = {**meta, "global_character_id": char.id}
        await db.commit()
        return ExportGlobalResponse(status="updated", global_id=char.id)

    elif req.action == "create":
        char = Character(**char_data)
        db.add(char)
        await db.flush()
        # Link back
        ref.metadata_json = {**meta, "global_character_id": char.id}
        await db.commit()
        return ExportGlobalResponse(status="created", global_id=char.id)

    raise HTTPException(status_code=400, detail=f"未知操作: {req.action}")


async def _export_scene(
    ref: ReferenceItem, meta: dict, req: ExportGlobalRequest, db: AsyncSession,
) -> ExportGlobalResponse:
    """Handle export of a location reference to global Scene table."""
    linked_id = meta.get("global_scene_id")

    if req.action == "preview":
        if linked_id:
            scene = await db.get(Scene, linked_id)
            if scene:
                return ExportGlobalResponse(
                    status="preview",
                    match=ExportGlobalMatch(global_id=scene.id, global_name=scene.name, global_type="scene"),
                )
        result = await db.execute(
            select(Scene).where(func.lower(Scene.name) == ref.name.strip().lower())
        )
        match = result.scalar_one_or_none()
        if match:
            return ExportGlobalResponse(
                status="preview",
                match=ExportGlobalMatch(global_id=match.id, global_name=match.name, global_type="scene"),
            )
        return ExportGlobalResponse(status="no_match")

    scene_data = {
        "name": ref.name,
        "description": ref.content or "",
        "category": meta.get("category") or "自定义",
        "location": meta.get("location"),
        "time_weather": meta.get("time_weather"),
        "mood_keywords": meta.get("mood_keywords") or [],
        "dramatic_potential": meta.get("dramatic_potential"),
        "sensory_details": meta.get("sensory_details") or {},
        "props": meta.get("props") or [],
        "is_preset": False,
    }

    if req.action == "update":
        target_id = req.global_id or linked_id
        if not target_id:
            raise HTTPException(status_code=400, detail="更新操作需要指定 global_id")
        scene = await db.get(Scene, target_id)
        if not scene:
            raise HTTPException(status_code=404, detail="全局场景不存在")
        for k, v in scene_data.items():
            setattr(scene, k, v)
        ref.metadata_json = {**meta, "global_scene_id": scene.id}
        await db.commit()
        return ExportGlobalResponse(status="updated", global_id=scene.id)

    elif req.action == "create":
        scene = Scene(**scene_data)
        db.add(scene)
        await db.flush()
        ref.metadata_json = {**meta, "global_scene_id": scene.id}
        await db.commit()
        return ExportGlobalResponse(status="created", global_id=scene.id)

    raise HTTPException(status_code=400, detail=f"未知操作: {req.action}")


# ── Sync Reference from Global Library ──────────────

@router.post("/{project_id}/references/{ref_id}/sync-global", response_model=ReferenceItemResponse)
async def sync_reference_from_global(
    project_id: str, ref_id: str, db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ReferenceItem).where(ReferenceItem.id == ref_id, ReferenceItem.project_id == project_id)
    )
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="参考资料不存在")

    meta = ref.metadata_json or {}
    # Project-specific fields to preserve across sync
    project_fields = {"story_name", "role_in_story", "relationships", "character_arc"}
    preserved = {k: meta[k] for k in project_fields if k in meta}

    global_char_id = meta.get("global_character_id")
    global_scene_id = meta.get("global_scene_id")

    if global_char_id:
        char = await db.get(Character, global_char_id)
        if not char:
            raise HTTPException(status_code=404, detail="全局角色已被删除")
        ref.name = char.name
        ref.content = char.background or ""
        ref.metadata_json = {
            "global_character_id": char.id,
            "gender": char.gender,
            "age": char.age,
            "identity": char.identity,
            "appearance": char.appearance,
            "personality": char.personality or [],
            "speaking_style": char.speaking_style,
            "core_trait": char.core_trait,
            "life_goal": char.life_goal,
            "fatal_flaw": char.fatal_flaw,
            "growth_hint": char.growth_hint,
            "habits": char.habits or [],
            "secret": char.secret,
            **preserved,
        }
    elif global_scene_id:
        scene = await db.get(Scene, global_scene_id)
        if not scene:
            raise HTTPException(status_code=404, detail="全局场景已被删除")
        ref.name = scene.name
        ref.content = scene.description or ""
        ref.metadata_json = {
            "global_scene_id": scene.id,
            "category": scene.category,
            "location": scene.location,
            "time_weather": scene.time_weather,
            "mood_keywords": scene.mood_keywords or [],
            "dramatic_potential": scene.dramatic_potential,
            "sensory_details": scene.sensory_details or {},
            "props": scene.props or [],
            **preserved,
        }
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
    project_id: str, document_id: str, db: AsyncSession = Depends(get_db),
):
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
):
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
    for rid in req.reference_ids:
        link = DocumentReference(document_id=document_id, reference_id=rid)
        db.add(link)
    await db.commit()
    return {"ok": True}


# ── Writing Sessions ────────────────────────────────

@router.get("/{project_id}/sessions", response_model=list[WritingSessionResponse])
async def list_sessions(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WritingSession)
        .where(WritingSession.project_id == project_id)
        .order_by(WritingSession.date.desc())
        .limit(90)
    )
    return [WritingSessionResponse.model_validate(s) for s in result.scalars().all()]


@router.post("/{project_id}/sessions", response_model=WritingSessionResponse, status_code=201)
async def create_session(
    project_id: str, req: WritingSessionCreate, db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
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
async def export_project(project_id: str, db: AsyncSession = Depends(get_db)):
    project = await _load_project(db, project_id)
    sections = []
    total = 0
    for g in project.groups:
        docs = []
        for d in g.documents:
            content = await read_content(project_id, d.id)
            wc = _count_words(content)
            total += wc
            docs.append({"name": d.name, "content": content, "word_count": wc})
        sections.append({"group": g.name, "documents": docs})
    return ProjectExportResponse(title=project.title, sections=sections, total_words=total)


# ── Project Analysis (AI extract from manuscript) ──

@router.post("/{project_id}/analyze", response_model=ProjectAnalyzeResponse, response_model_exclude_none=True)
async def analyze_project(
    project_id: str, req: ProjectAnalyzeRequest, db: AsyncSession = Depends(get_db),
):
    project = await _load_project(db, project_id)

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
    total_words = _count_words(full_combined)

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
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")

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
):
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
    await write_content(project_id, doc.id, new_content)
    doc.word_count = _count_words(new_content)
    await db.commit()

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
):
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
):
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
