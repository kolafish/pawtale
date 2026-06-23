import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.references import ReferenceType, coerce_setting_reference_type
from app.models.projects import ReferenceItem
from app.schemas.projects import (
    AnalyzeApplyRequest,
    AnalyzeApplyResponse,
    AnalyzedCharacter,
    AnalyzedOutline,
    AnalyzedSetting,
    DocumentAIContinueRequest,
    DocumentAIContinueResponse,
    DocumentAIReviseMultiRequest,
    DocumentAIReviseMultiResponse,
    DocumentAIReviseMultiResultItem,
    DocumentAIReviseRequest,
    DocumentAIReviseResponse,
    ProjectAnalyzeRequest,
    ProjectAnalyzeResponse,
    ReferenceItemBrief,
)
from app.services.ai_factory import get_ai_service
import app.services.project_documents as project_docs
from app.services.content_storage import read_content
from app.services.projects.access import get_document, load_project


logger = logging.getLogger(__name__)


async def analyze_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: ProjectAnalyzeRequest,
) -> ProjectAnalyzeResponse:
    project = await load_project(db, project_id, user_id)
    raw_parts: list[tuple[str, str]] = []
    for group in project.groups:
        for doc in group.documents or []:
            content = await read_content(project_id, doc.id)
            if content.strip():
                raw_parts.append((f"【{group.name} - {doc.name}】", content.strip()))

    if not raw_parts:
        raise HTTPException(status_code=400, detail="项目中没有手稿内容可供分析")

    full_combined = "\n\n".join(f"{header}\n{content}" for header, content in raw_parts)
    total_words = project_docs.count_words(full_combined)

    max_total_chars = 4000
    total_raw = sum(len(content) for _, content in raw_parts)
    if total_raw > max_total_chars:
        per_doc = max(500, max_total_chars // len(raw_parts))
        clipped_parts = [f"{header}\n{content[:per_doc]}..." for header, content in raw_parts]
    else:
        clipped_parts = [f"{header}\n{content}" for header, content in raw_parts]
    combined = "\n\n---\n\n".join(clipped_parts)

    selected_ai_model = req.ai_model or "claude-opus-4"
    ai_service = get_ai_service(selected_ai_model)

    try:
        result = await ai_service.analyze_project_content(combined, debug=req.debug)
    except Exception as e:
        logger.exception("AI analyze failed for project=%s", project_id)
        raise HTTPException(status_code=500, detail=f"AI分析失败: {str(e)}") from e

    return ProjectAnalyzeResponse(
        characters=[AnalyzedCharacter(**c) for c in result.get("characters", [])],
        outlines=[AnalyzedOutline(**o) for o in result.get("outlines", [])],
        settings=[AnalyzedSetting(**s) for s in result.get("settings", [])],
        total_words_analyzed=total_words,
        debug_info=result.get("debug_info"),
    )


async def apply_analysis(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: AnalyzeApplyRequest,
) -> AnalyzeApplyResponse:
    await load_project(db, project_id, user_id)
    created: list[ReferenceItem] = []

    for char in req.characters:
        ref = ReferenceItem(
            project_id=project_id,
            type=ReferenceType.CHARACTER.value,
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
            type=ReferenceType.OUTLINE.value,
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
            type=coerce_setting_reference_type(setting.type).value,
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


async def ai_continue_document(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentAIContinueRequest,
) -> DocumentAIContinueResponse:
    await load_project(db, project_id, user_id)
    doc = await get_document(db, project_id, document_id)
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
        raise HTTPException(status_code=500, detail=f"AI续写失败: {str(e)}") from e

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


async def ai_revise_document(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentAIReviseRequest,
) -> DocumentAIReviseResponse:
    await load_project(db, project_id, user_id)
    await get_document(db, project_id, document_id)
    selected_ai_model = req.ai_model or "claude-opus-4"
    ai_service = get_ai_service(selected_ai_model)

    if req.selected_text:
        original_text = req.selected_text
        scope = "selection"
    else:
        content = await read_content(project_id, document_id)
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
        raise HTTPException(status_code=500, detail=f"AI修改失败: {str(e)}") from e

    revised_text = (revise_result.get("content") or "").strip()
    if not revised_text:
        raise HTTPException(status_code=502, detail="AI未返回可用修改内容")

    return DocumentAIReviseResponse(
        document_id=document_id,
        revised_text=revised_text,
        original_text=original_text,
        scope=scope,
        ai_model=selected_ai_model,
        debug_info=revise_result.get("debug_info") if req.debug else None,
    )


async def ai_revise_document_multi(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentAIReviseMultiRequest,
) -> DocumentAIReviseMultiResponse:
    await load_project(db, project_id, user_id)
    await get_document(db, project_id, document_id)

    if req.selected_text:
        original_text = req.selected_text
        scope = "selection"
    else:
        content = await read_content(project_id, document_id)
        original_text = content.strip()
        scope = "full"

    if not original_text:
        raise HTTPException(status_code=400, detail="没有可修改的内容")

    async def revise_with_model(model: str) -> DocumentAIReviseMultiResultItem:
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

    results = await asyncio.gather(*(revise_with_model(model) for model in req.models))

    return DocumentAIReviseMultiResponse(
        document_id=document_id,
        original_text=original_text,
        scope=scope,
        results=list(results),
    )
