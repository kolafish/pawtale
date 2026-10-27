"""Body-bound section summaries. Source-era editorial caches are never reused."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirSection, MemoirSectionDigest
from app.schemas.memoir_section_digests import MemoirBodyDigest, MemoirSectionDigestResponse
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir.semantic_state import semantic_json_signature, semantic_text_hash
from app.services.memoir.prompt_budget import checked_review_prompt

SECTION_DIGEST_ENGINE = "section_body_digest_v1"
BODY_DIGEST_INSTRUCTION = (
    '先完成 content 正文，再只根据这份最终正文生成 body_digest：'
    '{"summary":"内容概括","focus":"叙事重点","points":[{"text":"正文中的事实要点",'
    '"material_ids":["对应素材短编号"]}]}。摘要保留否定、不确定程度、人物和时间关系；'
    'summary 用一至三句，focus 用一句；points 保留结构判断所需要点，不复述全文。'
    '不同主题分别列出要点与来源。不输出订正、去重、拆合建议、待办或补充问题。要点只关联支持它的素材；'
    '无法确定对应来源时 material_ids 为空，不能猜测。'
)


def digest_snapshot(content: str, digest: MemoirBodyDigest) -> dict:
    return {"body_digest": digest.model_dump(mode="json"), "body_digest_hash": semantic_text_hash(content)}


def snapshot_digest(snapshot: dict) -> MemoirBodyDigest | None:
    if snapshot.get("body_digest_hash") != semantic_text_hash(str(snapshot.get("content") or "")):
        return None
    raw = snapshot.get("body_digest")
    return MemoirBodyDigest.model_validate(raw) if raw else None


@dataclass(frozen=True)
class SectionDigestContext:
    project_id: str
    section_id: str
    chapter_id: str
    title: str
    content: str
    input_signature: str
    embedded: MemoirBodyDigest | None
    body_created_at: datetime


async def load_section_digest_context(db: AsyncSession, *, project_id: str, section_id: str) -> SectionDigestContext:
    section = await db.scalar(select(MemoirSection).where(
        MemoirSection.id == section_id, MemoirSection.project_id == project_id))
    if section is None:
        raise HTTPException(404, "小章节不存在")
    version = await db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.project_id == project_id,
        MemoirBookChapterDraftVersion.chapter_id == section.chapter_id,
    ).order_by(MemoirBookChapterDraftVersion.version.desc()).limit(1))
    snapshot = next((s for s in version.section_snapshot_json or [] if s.get("section_id") == section_id), None) if version else None
    if not snapshot or not str(snapshot.get("content") or "").strip():
        raise HTTPException(409, "小节还没有可读正文")
    content = snapshot["content"]
    return SectionDigestContext(project_id, section_id, section.chapter_id, section.title, content,
        semantic_json_signature({"engine": SECTION_DIGEST_ENGINE, "section_id": section_id,
            "title": section.title, "content": semantic_text_hash(content)}), snapshot_digest(snapshot), version.created_at)


async def _cached(db: AsyncSession, context: SectionDigestContext) -> MemoirSectionDigest | None:
    return await db.scalar(select(MemoirSectionDigest).where(
        MemoirSectionDigest.project_id == context.project_id, MemoirSectionDigest.section_id == context.section_id,
        MemoirSectionDigest.engine == SECTION_DIGEST_ENGINE,
        MemoirSectionDigest.input_signature == context.input_signature,
        MemoirSectionDigest.status == "ready"))


async def get_current_section_digest(db: AsyncSession, *, project_id: str, section_id: str) -> MemoirSectionDigest | None:
    try:
        context = await load_section_digest_context(db, project_id=project_id, section_id=section_id)
    except HTTPException as exc:
        if exc.status_code == 409:
            return None
        raise
    return _embedded_digest(context) if context.embedded else await _cached(db, context)


def _embedded_digest(context: SectionDigestContext) -> MemoirSectionDigest:
    return MemoirSectionDigest(project_id=context.project_id, section_id=context.section_id,
        id="body:" + context.input_signature, engine=SECTION_DIGEST_ENGINE,
        input_signature=context.input_signature, status="ready", digest_json=context.embedded.model_dump(mode="json"),
        error_message="", created_at=context.body_created_at, updated_at=context.body_created_at)


async def generate_or_reuse_section_digest(db: AsyncSession, *, project_id: str, section_id: str,
    ai_model: str | None = None, ai_client: AITextClient | None = None,
    force_regenerate: bool = False) -> MemoirSectionDigest:
    context = await load_section_digest_context(db, project_id=project_id, section_id=section_id)
    if context.embedded is not None:
        return _embedded_digest(context)
    existing = await _cached(db, context)
    if existing is not None and not force_regenerate:
        return existing
    # Backfill only the saved body. Never regenerate prose, read original
    # material, or fabricate provenance for legacy/manual text.
    await db.commit()
    result = await generate_structured_with_client(ai_client or get_ai_service(ai_model),
        "你只概括当前已保存的小节正文，不提出编辑建议，不增加事实。",
        checked_review_prompt(f"标题：{context.title}\n正文：\n{context.content}\n"
            + '返回 JSON：{"summary":"内容概括","focus":"叙事重点","points":[{"text":"正文要点","material_ids":[]}]}。'
            + '保留否定、转述归属和不确定性。不提供来源编号，material_ids 必须为空。', subject="正文摘要"),
        output_model=MemoirBodyDigest, requested_model=ai_model, temperature=0.2,
        max_tokens=2000, timeout=60.0, max_attempts=1)
    digest = result.value
    if any(point.material_ids for point in digest.points):
        raise ValueError("正文摘要不能猜测素材来源")
    generation = result.response.diagnostics()
    db.expire_all()
    # Coordinate with every existing chapter writer before checking/publishing.
    from app.services.memoir.chapter_generation import lock_chapter_draft_write
    await lock_chapter_draft_write(db, project_id=project_id, chapter_id=context.chapter_id)
    current = await load_section_digest_context(db, project_id=project_id, section_id=section_id)
    if current.input_signature != context.input_signature:
        raise HTTPException(409, "摘要生成期间正文已变化，请重新整理")
    row = await _cached(db, context)
    if row is None:
        row = MemoirSectionDigest(project_id=project_id, section_id=section_id, engine=SECTION_DIGEST_ENGINE,
            input_signature=context.input_signature)
    row.status = "ready"
    row.digest_json = {**digest.model_dump(mode="json"), **({"generation": generation} if generation else {})}
    row.error_message = ""
    row.updated_at = utcnow_naive()
    db.add(row)
    await db.flush()
    return row


async def mark_section_digests_stale(db: AsyncSession, *, project_id: str, section_ids: set[str]) -> int:
    if not section_ids:
        return 0
    result = await db.execute(delete(MemoirSectionDigest).where(
        MemoirSectionDigest.project_id == project_id, MemoirSectionDigest.section_id.in_(section_ids)))
    return result.rowcount


def digest_response(digest: MemoirSectionDigest) -> MemoirSectionDigestResponse:
    return MemoirSectionDigestResponse(id=digest.id, project_id=digest.project_id, section_id=digest.section_id,
        input_signature=digest.input_signature, engine=digest.engine, status=digest.status,
        digest=digest.digest_json, error_message=digest.error_message,
        created_at=digest.created_at, updated_at=digest.updated_at)
