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
            ReferenceItemBrief(id=r.id, type=r.type, name=r.name, sort_order=r.sort_order)
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
