from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookReview,
    MemoirChapterDigest,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir import chapter_digests, completion_issues
from app.services.memoir.semantic_state import (
    semantic_json_signature,
    semantic_text_hash,
)


BOOK_REVIEW_ENGINE = "llm_book_review_v1"
BOOK_REVIEW_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录总编辑。"
    "你根据章节摘要审阅全书层面的结构，不创造人生经历。"
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BookReviewChapter:
    chapter_id: str
    title: str
    sort_order: int
    digest_id: str
    digest_signature: str
    digest: dict
    confirmed_draft_version_id: str | None
    confirmed_draft_hash: str
    material_ids: frozenset[str]


@dataclass(frozen=True)
class BookReviewContext:
    project_id: str
    plan_id: str
    title: str
    chapters: tuple[BookReviewChapter, ...]
    input_signature: str


def _compact(value: object, *, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _string_list(value: object, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _compact(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


async def _plan(
    db: AsyncSession,
    *,
    project_id: str,
) -> tuple[MemoirBookPlan, list[MemoirBookPlanChapter]]:
    plan = await db.scalar(
        select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id)
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    chapters = list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter)
                .where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.plan_id == plan.id,
                    MemoirBookPlanChapter.include_in_book.is_(True),
                )
                .order_by(
                    MemoirBookPlanChapter.sort_order,
                    MemoirBookPlanChapter.id,
                )
            )
        ).all()
    )
    if not chapters:
        raise HTTPException(status_code=422, detail="全书没有收录章节")
    return plan, chapters


async def _confirmed_version(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookChapterDraftVersion | None:
    return await db.scalar(
        select(MemoirBookChapterDraftVersion)
        .join(
            MemoirBookChapterDraft,
            MemoirBookChapterDraft.id
            == MemoirBookChapterDraftVersion.draft_id,
        )
        .where(
            MemoirBookChapterDraftVersion.project_id == project_id,
            MemoirBookChapterDraftVersion.chapter_id == chapter_id,
            MemoirBookChapterDraft.status
            == MemoirBookChapterDraftStatus.CONFIRMED.value,
        )
        .order_by(
            MemoirBookChapterDraftVersion.version.desc(),
            MemoirBookChapterDraftVersion.created_at.desc(),
            MemoirBookChapterDraftVersion.id.desc(),
        )
        .limit(1)
    )


async def ensure_chapter_digests(
    db: AsyncSession,
    *,
    project_id: str,
    ai_model: str | None,
    ai_client: AITextClient | None,
) -> None:
    _, chapters = await _plan(db, project_id=project_id)
    chapter_refs = tuple(
        (chapter.id, chapter.title)
        for chapter in chapters
    )
    for chapter_id, chapter_title in chapter_refs:
        digest = await chapter_digests.generate_or_reuse_chapter_digest(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            ai_model=ai_model,
            ai_client=ai_client,
        )
        if digest.status != MemoirSemanticCacheStatus.READY.value:
            raise HTTPException(
                status_code=422,
                detail=digest.error_message or f"章节“{chapter_title}”审阅失败",
            )


async def load_book_review_context(
    db: AsyncSession,
    *,
    project_id: str,
) -> BookReviewContext:
    plan, plan_chapters = await _plan(db, project_id=project_id)
    chapters: list[BookReviewChapter] = []
    for chapter in plan_chapters:
        digest_context = await chapter_digests.load_chapter_digest_context(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
        digest = await db.scalar(
            select(MemoirChapterDigest).where(
                MemoirChapterDigest.project_id == project_id,
                MemoirChapterDigest.chapter_id == chapter.id,
                MemoirChapterDigest.input_signature
                == digest_context.input_signature,
                MemoirChapterDigest.status
                == MemoirSemanticCacheStatus.READY.value,
            )
        )
        if digest is None:
            raise HTTPException(
                status_code=409,
                detail=f"章节“{chapter.title}”摘要缺失或已过期",
            )
        confirmed = await _confirmed_version(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
        chapters.append(
            BookReviewChapter(
                chapter_id=chapter.id,
                title=chapter.title,
                sort_order=chapter.sort_order,
                digest_id=digest.id,
                digest_signature=digest.input_signature,
                digest=digest.digest_json,
                confirmed_draft_version_id=confirmed.id if confirmed else None,
                confirmed_draft_hash=(
                    semantic_text_hash(confirmed.content) if confirmed else ""
                ),
                material_ids=digest_context.allowed_material_ids,
            )
        )
    signature = semantic_json_signature(
        {
            "plan": {"id": plan.id, "title": plan.title},
            "chapters": [
                {
                    "id": chapter.chapter_id,
                    "title": chapter.title,
                    "sort_order": chapter.sort_order,
                    "digest_id": chapter.digest_id,
                    "digest_signature": chapter.digest_signature,
                    "digest_hash": semantic_json_signature(chapter.digest),
                    "confirmed_draft_version_id": (
                        chapter.confirmed_draft_version_id
                    ),
                    "confirmed_draft_hash": chapter.confirmed_draft_hash,
                }
                for chapter in chapters
            ],
            "engine": BOOK_REVIEW_ENGINE,
        }
    )
    return BookReviewContext(
        project_id=project_id,
        plan_id=plan.id,
        title=plan.title,
        chapters=tuple(chapters),
        input_signature=signature,
    )


def build_book_review_prompt(context: BookReviewContext) -> str:
    chapters = "\n\n".join(
        (
            f"chapter_id: {chapter.chapter_id}\n"
            f"章节: {chapter.title}\n"
            f"摘要: {chapter.digest.get('summary', '')}\n"
            f"重点: {chapter.digest.get('focus', '')}"
        )
        for chapter in context.chapters
    )
    return f"""请审阅回忆录“{context.title}”的全书结构。

有序章节：
{chapters}

请输出严格 JSON：
{{
  "summary": "全书目前的整体面貌",
  "strengths": ["已经成立的主线或结构优点"],
  "structure_guidance": ["只需编辑处理的跨章顺序、去重、过渡或篇幅建议"],
  "candidate_issues": [
    {{
      "issue_type": "context|sequence|relationship|impact|meaning|clarity|structure|privacy|conflict",
      "channel": "interview|manual_review",
      "question": "确实需要用户处理时的一句自然问题",
      "reason": "为什么这会明显影响全书理解或用户决定",
      "priority": "high|medium|low",
      "chapter_ids": ["相关 chapter_id"],
      "material_ids": ["相关 material_id，可为空"]
    }}
  ]
}}

要求：
- 不用固定人生目录检查每个阶段是否都讲过。
- 只判断明显的跨章断裂、重复、人物突现、主题失衡和用户决定。
- 纯编辑问题写入 structure_guidance，不要求用户补讲。
- candidate_issues 最多三个；不能创建摘要中没有的人生事实。
"""


def normalize_book_review(
    payload: Any,
    *,
    allowed_chapter_ids: set[str],
    allowed_material_ids: set[str],
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("book review AI did not return a JSON object")
    summary = _compact(payload.get("summary"), limit=1000)
    if not summary:
        raise ValueError("book review summary is empty")
    candidates = payload.get("candidate_issues")
    if not isinstance(candidates, list):
        candidates = []
    issues: list[dict] = []
    allowed_types = {
        "context",
        "sequence",
        "relationship",
        "impact",
        "meaning",
        "clarity",
        "structure",
        "privacy",
        "conflict",
    }
    for candidate in candidates[:3]:
        if not isinstance(candidate, dict):
            continue
        question = _compact(candidate.get("question"), limit=180)
        reason = _compact(candidate.get("reason"), limit=300)
        if not question or not reason:
            continue
        chapter_ids = _string_list(
            candidate.get("chapter_ids"),
            limit=8,
            item_limit=36,
        )
        material_ids = _string_list(
            candidate.get("material_ids"),
            limit=12,
            item_limit=36,
        )
        if set(chapter_ids) - allowed_chapter_ids:
            raise ValueError("book review referenced chapter outside allowlist")
        if set(material_ids) - allowed_material_ids:
            raise ValueError("book review referenced material outside allowlist")
        issue_type = _compact(candidate.get("issue_type"), limit=24)
        channel = _compact(candidate.get("channel"), limit=24)
        priority = _compact(candidate.get("priority"), limit=12)
        issues.append(
            {
                "issue_type": (
                    issue_type if issue_type in allowed_types else "clarity"
                ),
                "channel": (
                    channel
                    if channel in {"interview", "manual_review"}
                    else "interview"
                ),
                "question": question,
                "reason": reason,
                "priority": (
                    priority
                    if priority in {"high", "medium", "low"}
                    else "medium"
                ),
                "chapter_ids": chapter_ids,
                "material_ids": material_ids,
            }
        )
    return {
        "summary": summary,
        "strengths": _string_list(
            payload.get("strengths"),
            limit=8,
            item_limit=240,
        ),
        "structure_guidance": _string_list(
            payload.get("structure_guidance"),
            limit=8,
            item_limit=240,
        ),
        "candidate_issues": issues,
    }


async def generate_or_reuse_book_review(
    db: AsyncSession,
    *,
    project_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    force_regenerate: bool = False,
) -> MemoirBookReview:
    await ensure_chapter_digests(
        db,
        project_id=project_id,
        ai_model=ai_model,
        ai_client=ai_client,
    )
    context = await load_book_review_context(db, project_id=project_id)
    previous = list(
        (
            await db.scalars(
                select(MemoirBookReview).where(
                    MemoirBookReview.project_id == project_id,
                    MemoirBookReview.engine == BOOK_REVIEW_ENGINE,
                )
            )
        ).all()
    )
    for item in previous:
        if (
            item.input_signature != context.input_signature
            and item.status == MemoirSemanticCacheStatus.READY.value
        ):
            item.status = MemoirSemanticCacheStatus.STALE.value
            db.add(item)
    existing = next(
        (
            item
            for item in previous
            if item.input_signature == context.input_signature
        ),
        None,
    )
    if (
        existing is not None
        and existing.status == MemoirSemanticCacheStatus.READY.value
        and not force_regenerate
    ):
        await completion_issues.sync_book_review_issues(
            db,
            project_id=project_id,
            input_signature=existing.input_signature,
            candidates=existing.review_json.get("candidate_issues", []),
        )
        return existing
    client = ai_client or get_ai_service(ai_model)
    prompt = build_book_review_prompt(context)
    try:
        raw = await client._call_llm(
            BOOK_REVIEW_SYSTEM_PROMPT,
            prompt,
            temperature=0.2,
            max_tokens=2400,
            timeout=60.0,
        )
        review_json = normalize_book_review(
            extract_json_payload(str(raw)),
            allowed_chapter_ids={
                chapter.chapter_id for chapter in context.chapters
            },
            allowed_material_ids={
                material_id
                for chapter in context.chapters
                for material_id in chapter.material_ids
            },
        )
        db.expire_all()
        current = await load_book_review_context(db, project_id=project_id)
        if current.input_signature != context.input_signature:
            raise HTTPException(
                status_code=409,
                detail="审阅期间全书内容已变化，请重试",
            )
        if existing is not None:
            await db.refresh(existing)
        status = MemoirSemanticCacheStatus.READY
        error = ""
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("memoir book review generation failed project_id=%s", project_id)
        review_json = {}
        status = MemoirSemanticCacheStatus.FAILED
        error = str(exc) or "book review AI unavailable"
    review = existing or MemoirBookReview(
        project_id=project_id,
        input_signature=context.input_signature,
        engine=BOOK_REVIEW_ENGINE,
    )
    review.status = status.value
    review.review_json = {
        **review_json,
        "generation": {
            "provider": getattr(client, "provider_name", None),
            "model": getattr(client, "model_name", ai_model),
            "engine": BOOK_REVIEW_ENGINE,
        },
    } if review_json else {}
    review.error_message = error
    review.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(review)
    await db.flush()
    if status == MemoirSemanticCacheStatus.READY:
        await completion_issues.sync_book_review_issues(
            db,
            project_id=project_id,
            input_signature=review.input_signature,
            candidates=review.review_json.get("candidate_issues", []),
        )
    return review
