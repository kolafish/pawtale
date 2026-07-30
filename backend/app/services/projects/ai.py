import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projects.types import ProjectType
from app.domain.references import ReferenceType, coerce_setting_reference_type
from app.models.projects import Document, ReferenceItem
from app.schemas.projects import (
    AnalyzeApplyRequest,
    AnalyzeApplyResponse,
    AnalyzedCharacter,
    AnalyzedOutline,
    AnalyzedSetting,
    ArticleAIReviewSuggestion,
    DocumentAIContinueRequest,
    DocumentAIContinueResponse,
    DocumentAIReviewRequest,
    DocumentAIReviewResponse,
    DocumentAIReviseMultiRequest,
    DocumentAIReviseMultiResponse,
    DocumentAIReviseMultiResultItem,
    DocumentAIReviseRequest,
    DocumentAIReviseResponse,
    ProjectAnalyzeRequest,
    ProjectAnalyzeResponse,
    ReferenceItemBrief,
)
from app.services import ai_project_analysis, ai_revision, ai_story
from app.services.article import ai as article_ai
from app.services.ai_base import AITextServiceBase
from app.services.ai_factory import get_ai_service
import app.services.project_documents as project_docs
from app.services.content_storage import read_content
from app.services.projects.access import get_document, load_project


logger = logging.getLogger(__name__)


def _require_project_type(
    project_type: str,
    *,
    allowed: set[ProjectType],
    detail: str,
) -> None:
    if project_type not in {item.value for item in allowed}:
        raise HTTPException(status_code=400, detail=detail)


def _check_expected_revision(
    expected_revision: int | None,
    actual_revision: int,
) -> None:
    if expected_revision is not None and expected_revision != actual_revision:
        raise HTTPException(
            status_code=409,
            detail="文档内容已在其他位置更新，请刷新后再使用 AI",
        )


async def _check_revision_unchanged(
    db: AsyncSession,
    doc: Document,
    base_revision: int,
) -> None:
    await db.refresh(doc, attribute_names=["content_revision"])
    if doc.content_revision != base_revision:
        raise HTTPException(
            status_code=409,
            detail="AI处理期间文档内容已更新，请基于最新内容重试",
        )


async def _revise_text(
    *,
    project_type: str,
    ai_service: AITextServiceBase,
    original_text: str,
    instruction: str,
    context: str | None,
    requested_model: str | None,
    debug: bool,
) -> tuple[str, dict | None]:
    if project_type == ProjectType.ARTICLE.value:
        result = await article_ai.revise_article(
            ai_service,
            content=original_text,
            instruction=instruction,
            context=context,
            requested_model=requested_model,
            debug=debug,
        )
        return result.content, result.debug_info
    if project_type != ProjectType.NOVEL.value:
        raise HTTPException(status_code=400, detail="回忆录请使用专属编辑流程")
    result = await ai_revision.revise_chapter(
        ai_service,
        original_content=original_text,
        revision_instruction=instruction,
        characters=[],
        writing_style="细腻",
        chapter_length="长",
        debug=debug,
    )
    return (
        (result.get("content") or "").strip(),
        result.get("debug_info") if debug else None,
    )


async def analyze_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: ProjectAnalyzeRequest,
) -> ProjectAnalyzeResponse:
    project = await load_project(db, project_id, user_id)
    _require_project_type(
        project.type,
        allowed={ProjectType.NOVEL},
        detail="仅小说项目支持项目分析",
    )
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

    ai_service = get_ai_service(req.ai_model)

    try:
        result = await ai_project_analysis.analyze_project_content(
            ai_service,
            combined,
            debug=req.debug,
        )
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
    project = await load_project(db, project_id, user_id)
    _require_project_type(
        project.type,
        allowed={ProjectType.NOVEL},
        detail="仅小说项目支持应用项目分析",
    )
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
    project = await load_project(db, project_id, user_id)
    _require_project_type(
        project.type,
        allowed={ProjectType.NOVEL, ProjectType.ARTICLE},
        detail="回忆录请使用专属成书流程",
    )
    doc = await get_document(db, project_id, document_id)
    base_content_revision = doc.content_revision
    if (
        req.expected_content_revision is not None
        and req.expected_content_revision != base_content_revision
    ):
        raise HTTPException(
            status_code=409,
            detail="文档内容已在其他位置更新，请刷新后再继续",
        )
    content = await read_content(project_id, doc.id)
    base_content = content.strip()
    if not base_content:
        raise HTTPException(status_code=400, detail="请先写一些内容")

    ai_service = get_ai_service(req.ai_model)
    selected_ai_model = req.ai_model or ai_service.model_name
    try:
        if project.type == ProjectType.ARTICLE.value:
            article_result = await article_ai.continue_article(
                ai_service,
                content=base_content[-8000:],
                instruction=req.instruction,
                requested_model=req.ai_model,
                debug=req.debug,
            )
            generated_chunk = article_result.content
            debug_info = article_result.debug_info
        else:
            story_result = await ai_story.generate_story(
                ai_service,
                characters=[],
                scene={
                    "name": "当前文本",
                    "description": "基于已有正文续写",
                    "location": None,
                    "time_weather": None,
                    "sensory_details": {},
                    "props": [],
                },
                mood="甜",
                writing_style="细腻",
                chapter_length="中",
                previous_content=base_content[-4000:],
                custom_direction=req.instruction,
                chapter_number=1,
                debug=req.debug,
            )
            generated_chunk = (story_result.get("content") or "").strip()
            debug_info = story_result.get("debug_info") if req.debug else None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI续写失败: {str(e)}") from e

    if not generated_chunk:
        raise HTTPException(status_code=502, detail="AI未返回可用续写内容")

    new_content = f"{content.rstrip()}\n\n{generated_chunk}"
    try:
        await project_docs.commit_document_update(
            db,
            project_id,
            doc,
            new_content,
            expected_content_revision=base_content_revision,
        )
    except project_docs.ContentRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="文档内容已在其他位置更新，请刷新后再继续",
        ) from exc

    return DocumentAIContinueResponse(
        document_id=doc.id,
        content=new_content,
        content_revision=doc.content_revision,
        generated_chunk=generated_chunk,
        ai_model=selected_ai_model,
        debug_info=debug_info,
    )


async def ai_revise_document(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentAIReviseRequest,
) -> DocumentAIReviseResponse:
    project = await load_project(db, project_id, user_id)
    _require_project_type(
        project.type,
        allowed={ProjectType.NOVEL, ProjectType.ARTICLE},
        detail="回忆录请使用专属编辑流程",
    )
    doc = await get_document(db, project_id, document_id)
    base_content_revision = doc.content_revision
    _check_expected_revision(
        req.expected_content_revision,
        base_content_revision,
    )
    ai_service = get_ai_service(req.ai_model)
    selected_ai_model = req.ai_model or ai_service.model_name

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
        revised_text, debug_info = await _revise_text(
            project_type=project.type,
            ai_service=ai_service,
            original_text=original_text,
            instruction=req.instruction,
            context=req.full_content if scope == "selection" else None,
            requested_model=req.ai_model,
            debug=req.debug,
        )
    except Exception as e:
        logger.exception("AI revise failed for doc=%s project=%s", document_id, project_id)
        raise HTTPException(status_code=500, detail=f"AI修改失败: {str(e)}") from e

    await _check_revision_unchanged(db, doc, base_content_revision)
    if not revised_text:
        raise HTTPException(status_code=502, detail="AI未返回可用修改内容")

    return DocumentAIReviseResponse(
        document_id=document_id,
        revised_text=revised_text,
        original_text=original_text,
        scope=scope,
        ai_model=selected_ai_model,
        content_revision=base_content_revision,
        debug_info=debug_info,
    )


async def ai_review_document(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentAIReviewRequest,
) -> DocumentAIReviewResponse:
    project = await load_project(db, project_id, user_id)
    if project.type != ProjectType.ARTICLE.value:
        raise HTTPException(status_code=400, detail="仅文章项目支持文章检查")
    doc = await get_document(db, project_id, document_id)
    base_content_revision = doc.content_revision
    _check_expected_revision(
        req.expected_content_revision,
        base_content_revision,
    )
    content = (await read_content(project_id, doc.id)).strip()
    if not content:
        raise HTTPException(status_code=400, detail="请先写一些内容")

    ai_service = get_ai_service(req.ai_model)
    selected_ai_model = req.ai_model or ai_service.model_name
    try:
        result = await article_ai.review_article(
            ai_service,
            content=content,
            requested_model=req.ai_model,
            debug=req.debug,
        )
    except Exception as e:
        logger.exception("Article AI review failed for doc=%s project=%s", document_id, project_id)
        raise HTTPException(status_code=500, detail=f"AI检查失败: {str(e)}") from e

    await _check_revision_unchanged(db, doc, base_content_revision)
    return DocumentAIReviewResponse(
        document_id=document_id,
        summary=result.summary,
        suggestions=[
            ArticleAIReviewSuggestion(
                excerpt=item.excerpt,
                issue=item.issue,
                suggestion=item.suggestion,
            )
            for item in result.suggestions
        ],
        ai_model=selected_ai_model,
        content_revision=base_content_revision,
        debug_info=result.debug_info,
    )


async def ai_revise_document_multi(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentAIReviseMultiRequest,
) -> DocumentAIReviseMultiResponse:
    project = await load_project(db, project_id, user_id)
    _require_project_type(
        project.type,
        allowed={ProjectType.NOVEL, ProjectType.ARTICLE},
        detail="回忆录请使用专属编辑流程",
    )
    doc = await get_document(db, project_id, document_id)
    base_content_revision = doc.content_revision
    _check_expected_revision(
        req.expected_content_revision,
        base_content_revision,
    )

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
            revised, debug_info = await _revise_text(
                project_type=project.type,
                ai_service=ai_service,
                original_text=original_text,
                instruction=req.instruction,
                context=req.full_content if scope == "selection" else None,
                requested_model=model,
                debug=req.debug,
            )
            if not revised:
                return DocumentAIReviseMultiResultItem(ai_model=model, error="AI未返回可用修改内容")
            return DocumentAIReviseMultiResultItem(
                ai_model=model,
                revised_text=revised,
                debug_info=debug_info,
            )
        except Exception as e:
            logger.exception("AI revise-multi failed for model=%s doc=%s", model, document_id)
            return DocumentAIReviseMultiResultItem(ai_model=model, error=str(e))

    results = await asyncio.gather(*(revise_with_model(model) for model in req.models))
    await _check_revision_unchanged(db, doc, base_content_revision)

    return DocumentAIReviseMultiResponse(
        document_id=document_id,
        original_text=original_text,
        scope=scope,
        content_revision=base_content_revision,
        results=list(results),
    )
