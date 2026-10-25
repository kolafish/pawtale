from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import time
from typing import Any, Awaitable, Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.application_errors import InsufficientAICreditError
from app.domain.memoir.types import (
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookRevisionProposal,
    MemoirBookReview,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.models.projects import Project
from app.schemas.memoir_book_review import (
    MemoirBookRevisionMetadataField,
    MemoirBookReviewResponse,
)
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import (
    ai_error_metadata,
    ai_text_timeout_seconds,
    ai_user_error_message,
)
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir import (
    book_editorial_actions,
    book_output_versions,
    chapter_digests,
    completion_issues,
    fact_decisions,
)
from app.services.memoir.book_output_manuscript import (
    book_information_text,
    formal_book_title,
    stored_manuscript_metadata,
)
from app.services.memoir.book_output_snapshots import load_book_plan_drafts, readable_chapter_versions
from app.services.memoir.chapter_generation import latest_chapter_draft_version
from app.services.memoir.transcript_projection import TranscriptEdit, current_material_text, load_transcript_edits
from app.services.memoir.prompt_budget import checked_review_prompt
from app.services.memoir.semantic_state import (
    semantic_json_signature,
    semantic_text_hash,
)


BOOK_REVIEW_ENGINE = "llm_book_review_v7"
BOOK_REVIEW_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录总编辑。"
    "你根据当前章节正文通读全书并判断阅读体验，不创造人生经历。"
)
logger = logging.getLogger(__name__)
BookReviewProgressCallback = Callable[
    [dict[str, object]],
    Awaitable[None],
]


def book_review_response(review: MemoirBookReview) -> MemoirBookReviewResponse:
    return MemoirBookReviewResponse(
        id=review.id,
        project_id=review.project_id,
        input_signature=review.input_signature,
        engine=review.engine,
        status=review.status,
        review=review.review_json,
        error_message=review.error_message,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@dataclass(frozen=True)
class BookReviewChapter:
    chapter_id: str
    title: str
    sort_order: int
    digest_id: str
    digest_signature: str
    digest: dict
    draft_version_id: str
    draft_hash: str
    material_ids: frozenset[str]
    draft_content: str = ""
    decisions: tuple[fact_decisions.FactDecision, ...] = ()
    edits: tuple[TranscriptEdit, ...] = ()


@dataclass(frozen=True)
class BookReviewContext:
    project_id: str
    plan_id: str
    book_metadata: dict[str, str]
    chapters: tuple[BookReviewChapter, ...]
    input_signature: str


class _BookReviewIssueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: str = ""
    question: str = ""
    reason: str = ""
    priority: str = ""
    chapter_ids: list[str] = Field(default_factory=list)
    evidence_quote: str = ""
    resolution: Literal[
        "new_material",
        "editorial_change",
        "user_decision",
    ]


class _BookReviewEditorialActionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal[
        "remove_repetition",
        "improve_transition",
        "reorder_chapters",
        "revise_book_profile",
    ]
    title: str
    reason: str
    chapter_ids: list[str] = Field(default_factory=list)
    metadata_fields: list[MemoirBookRevisionMetadataField] = Field(
        default_factory=list
    )


class BookStructureFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["chapter_structure", "section_structure", "title_scope"]
    chapter_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)


class _BookReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    strengths: list[str] = Field(default_factory=list)
    structure_guidance: list[str] = Field(default_factory=list)
    structure_findings: list[BookStructureFinding] = Field(default_factory=list)
    editorial_actions: list[_BookReviewEditorialActionOutput] = Field(
        default_factory=list
    )
    candidate_issues: list[_BookReviewIssueOutput] = Field(default_factory=list, description="无回答就无法理解关键关系的问题；已明确记不清、拒答、已回答的内容禁止追问；可成文则为空")


def _compact(value: object, *, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _user_visible_text(
    value: object,
    *,
    context: BookReviewContext,
    limit: int,
) -> str:
    return _compact(_chapter_references(context).render_text(value), limit=limit)


def _user_visible_string_list(
    value: object,
    *,
    context: BookReviewContext,
    limit: int,
    item_limit: int,
) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _user_visible_text(
            item,
            context=context,
            limit=item_limit,
        )
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


async def require_readable_chapter_drafts(db: AsyncSession, *, project_id: str) -> int:
    plan = await load_book_plan_drafts(db, project_id=project_id)
    return len(readable_chapter_versions(plan))


async def ensure_chapter_digests(
    db: AsyncSession,
    *,
    project_id: str,
    ai_model: str | None,
    ai_client: AITextClient | None,
    progress_callback: BookReviewProgressCallback | None = None,
) -> None:
    _, chapters = await _plan(db, project_id=project_id)
    chapter_refs = tuple(
        (chapter.id, chapter.title)
        for chapter in chapters
    )
    total_chapter_count = len(chapter_refs)
    async def request_progress(patch: dict[str, object]) -> None:
        if progress_callback is not None:
            await progress_callback({key: patch[key] for key in ("provider_request_pending", "provider", "resolved_ai_model") if key in patch})
    for index, (chapter_id, chapter_title) in enumerate(chapter_refs):
        if progress_callback is not None:
            await progress_callback(
                {
                    "stage": "preparing_chapters",
                    "total_chapter_count": total_chapter_count,
                    "processed_chapter_count": index,
                    "current_chapter_title": chapter_title,
                }
            )
        digest = await chapter_digests.generate_or_reuse_chapter_digest(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            ai_model=ai_model,
            ai_client=ai_client,
            progress_callback=request_progress,
        )
        if digest.status != MemoirSemanticCacheStatus.READY.value:
            raise HTTPException(
                status_code=422,
                detail=digest.error_message or f"章节“{chapter_title}”审阅失败",
            )
        # Persist each chapter checkpoint before reading through a new Session.
        # Progress reporting must not own the digest transaction.
        await db.commit()


async def load_book_review_metadata(
    db: AsyncSession,
    *,
    project_id: str,
    fallback_title: str = "",
) -> dict[str, str]:
    output = await db.scalar(
        select(MemoirBookOutput).where(
            MemoirBookOutput.project_id == project_id,
        )
    )
    latest: MemoirBookOutputVersion | None = (
        await book_output_versions.load_latest_book_output_version(
            db,
            output_id=output.id,
        )
        if output is not None
        else None
    )
    project = await db.get(Project, project_id, populate_existing=True)
    book_title = (
        (project.title or "").strip()
        if project is not None
        else formal_book_title(fallback_title.strip())
    ) or "我的回忆录"
    return {
        "title": book_title,
        **stored_manuscript_metadata(
            latest.manuscript_json if latest is not None else {}
        ),
    }


async def load_book_review_context(
    db: AsyncSession,
    *,
    project_id: str,
) -> BookReviewContext:
    plan, plan_chapters = await _plan(db, project_id=project_id)
    book_metadata = await load_book_review_metadata(db, project_id=project_id, fallback_title=plan.title or "")
    chapters: list[BookReviewChapter] = []
    decisions = await fact_decisions.load_fact_decisions(db, project_id=project_id)
    edits_by_chapter: dict[str, list[TranscriptEdit]] = {}
    if decisions:
        rows = (await db.execute(select(MemoirSectionMaterial, MemoirSection.chapter_id).join(
            MemoirSection, MemoirSection.id == MemoirSectionMaterial.section_id,
        ).where(
            MemoirSection.project_id == project_id,
            MemoirSection.chapter_id.in_([chapter.id for chapter in plan_chapters]),
            MemoirSection.status == "active", MemoirSection.include_in_book.is_(True),
            MemoirSectionMaterial.include_in_book.is_(True),
        ).options(
            joinedload(MemoirSectionMaterial.utterance),
            joinedload(MemoirSectionMaterial.transcript_version),
        ).order_by(MemoirSection.sort_order, MemoirSection.id,
                   MemoirSectionMaterial.sort_order, MemoirSectionMaterial.id))).all()
        edits = await load_transcript_edits(db, [material for material, _ in rows])
        for material, chapter_id in rows:
            if current_material_text(material):
                edits_by_chapter.setdefault(chapter_id, []).extend(edits.get(material.transcript_version_id, ()))
    for chapter in plan_chapters:
        digest_context = await chapter_digests.load_chapter_digest_context(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
        digest = await chapter_digests.get_current_chapter_digest(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
            context=digest_context,
        )
        if (
            digest is None
            or digest.status != MemoirSemanticCacheStatus.READY.value
        ):
            raise HTTPException(
                status_code=409,
                detail=f"章节“{chapter.title}”摘要缺失或已过期",
            )
        confirmed = await latest_chapter_draft_version(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
        if confirmed is None:
            raise HTTPException(
                status_code=409,
                detail=f"章节“{chapter.title}”还没有可读正文",
            )
        chapters.append(
            BookReviewChapter(
                chapter_id=chapter.id,
                title=chapter.title,
                sort_order=chapter.sort_order,
                digest_id=digest.id,
                digest_signature=digest.input_signature,
                digest=digest.digest_json,
                draft_version_id=confirmed.id,
                draft_hash=semantic_text_hash(confirmed.content),
                draft_content=confirmed.content,
                material_ids=digest_context.allowed_material_ids,
                decisions=fact_decisions.for_sources(
                    decisions, chapter_id=chapter.id, material_ids=digest_context.allowed_material_ids,
                ),
                edits=tuple(edits_by_chapter.get(chapter.id, ())),
            )
        )
    signature = semantic_json_signature(
        {
            "plan": {"id": plan.id},
            "book_metadata": book_metadata,
            "chapters": [
                {
                    "id": chapter.chapter_id,
                    "title": chapter.title,
                    "sort_order": chapter.sort_order,
                    "draft_hash": chapter.draft_hash,
                    **({"fact_decisions": fact_decisions.signature(chapter.decisions)} if chapter.decisions else {}),
                    **({"transcript_edits": [
                        [edit.edited_at, edit.before, edit.after] for edit in chapter.edits
                    ]} if chapter.edits else {}),
                }
                for chapter in chapters
            ],
            "engine": BOOK_REVIEW_ENGINE,
        }
    )
    return BookReviewContext(
        project_id=project_id,
        plan_id=plan.id,
        book_metadata=book_metadata,
        chapters=tuple(chapters),
        input_signature=signature,
    )


def _chapter_references(context: BookReviewContext) -> ModelReferenceMap:
    return ModelReferenceMap(
        "C",
        (chapter.chapter_id for chapter in context.chapters),
        labels={
            chapter.chapter_id: f"第{index}章《{chapter.title}》"
            for index, chapter in enumerate(context.chapters, start=1)
        },
        fallback_label="相关章节",
    )


def build_book_review_prompt(context: BookReviewContext) -> str:
    references = _chapter_references(context)
    chapters = "\n\n".join(
        (
            f"chapter_id: {references.encode(chapter.chapter_id)}\n"
            f"章节: {chapter.title}\n"
            f"当前正文:\n{chapter.draft_content}\n"
            f"{fact_decisions.prompt(chapter.decisions, edits=chapter.edits)}"
        )
        for chapter in context.chapters
    )
    prompt = f"""请通读下面这本回忆录。

当前成书信息：
{book_information_text(context.book_metadata)}

有序章节：
{chapters}

请输出严格 JSON：
{{
  "summary": "全书目前的整体面貌",
  "strengths": ["已经成立的主线或结构优点"],
  "structure_guidance": ["确有必要时给出的可选编辑建议"],
  "structure_findings": [{{"kind":"chapter_structure|section_structure|title_scope","chapter_ids":["相关 chapter_id"],"reason":"根据现有正文发现的具体目录问题及依据"}}],
  "editorial_actions": [
    {{
      "action_type": "remove_repetition|improve_transition|reorder_chapters|revise_book_profile",
      "title": "用户能直接理解的一句话修改标题",
      "reason": "为什么需要这项修改，以及预期改善",
      "chapter_ids": ["涉及章节；调整顺序时按建议顺序排列"],
      "metadata_fields": ["仅 revise_book_profile 填写：title|subtitle|narrator_name|prepared_by|preface|afterword|back_cover_quote"]
    }}
  ],
  "candidate_issues": [
    {{
      "issue_type": "context|sequence|relationship|impact|meaning|clarity|structure|conflict",
      "question": "缺少回答就无法理解关键人物或事件关系、且现有文字无法解决的问题；已明确记不清的不问",
      "reason": "为什么这会明显影响全书理解或用户决定",
      "priority": "high|medium|low",
      "chapter_ids": ["相关 chapter_id"],
      "evidence_quote": "事实冲突事项所依据的章节正文短原句；其他事项留空",
      "resolution": "new_material|editorial_change|user_decision"
    }}
  ]
}}

要求：
- 输入是当前书稿及明确列出的用户决定，未提供原始录音或完整转写，不能声称核验过这些来源。
- 章是大主题，小节是其下的小主题；不同人物、事件时间、当时与现在的观点不同，不能直接判为事实冲突。保留否定、转述来源和局部疑义。
- candidate_issues 仅放缺少回答就无法理解关键人物或事件关系的问题。能保留疑义成文就不补问；不能为丰富背景、补年份或补细节制造问题。明确记不清、已回答或拒答的内容禁止换问法追问，包括改问大概年份、年龄或意愿；无必要问题必须为空。
- 可通过段落安排、衔接或保留联想解决的跳转，只提编辑建议，不要求用户解释人生经历的意义或关系。
- 成书资料为空不等于错误，不要求补齐可选文案；口述人、整理人等身份只有与明确已知资料矛盾时才建议更正，不能要求编辑凭空补出姓名。
- 这是供讲述者自己和亲友阅读的私人回忆录，不按正规出版或公开传播标准审查。
- 普通经历、婚姻家庭、个人感受、亲友姓名或称呼、爱好默认保留，不提出隐私追问或匿名化建议。
- 私人书稿不产生 privacy 事项或以隐私确认阻止成书。
- 用户已决定保留的内容不得反复追问；用户明确要求不收录的内容必须尊重，不能自行恢复或改变收录范围。
- 不用固定人生目录检查每个阶段是否都讲过。
- 从整体阅读感受出发，没有明显影响时可以不提修改。
- editorial_actions 只放基于现有内容、能够直接执行的可选建议。
- revise_book_profile 用于书名、副标题、口述人、整理人、前言、后记或封底文案，chapter_ids 返回空数组，并在 metadata_fields 精确列出需要修改的字段；其他类型的 metadata_fields 返回空数组。
- 除目录问题外，不能直接执行的其他编辑建议写入 structure_guidance，不要求用户补讲；目录问题必须写入 structure_findings。
- 允许发现并提出章节过碎、主题混杂、章或小节需要拆合、标题范围不符、归属不合适等结构问题，填入 structure_findings，并关联涉及的已有 chapter_id。
- structure_findings 的 kind：chapter_structure 表示章的划分或归属问题；section_structure 表示小节的划分或归属问题；title_scope 表示章名或小节名与内容范围不符。只描述有正文依据的问题，不强求每次都有发现。
- 目录调整由统一的目录写入流程核对素材后执行，保留原始讲述关联并保护人工修改，再更新正文。不要通过正文改写制造另一份目录，不把结构问题变成要求用户补讲的事项。
- editorial_actions 仍只返回上面声明的四种正文/成书信息编辑类型，不把拆章、合章或合节伪装成这些操作。
- 书名、全书定位或出版陈述不合适时使用 revise_book_profile，不要把它拆成需要用户补讲的问题。
- 只有回答会提供新的亲历事实，而且这些事实适合写入某章正文时，resolution 才能是 new_material。
- 只有事实冲突、内容是否收录等确实需要用户拍板的决定，resolution 才能是 user_decision。
- candidate_issues 数量不设配额，只保留彼此独立且确实需要用户处理的问题，并合并相似问题。
- 不新增正文和明确用户决定以外的人生事实，不把审阅猜测写成确定结论。
- 每个 candidate_issue 至少关联一个章节；chapter_ids 只能使用输入中出现的本次请求短编号 chapter_id。
- 事实冲突事项的 evidence_quote 必须逐字摘自相关章节当前正文，选能指认这件事的最短完整片段；找不到可核对原文时留空，不要编造。
- summary、strengths、structure_guidance、editorial_actions 的 title/reason、question 和 reason 会直接展示给用户；引用章节时使用章节标题。
"""
    return checked_review_prompt(prompt, subject="全书")


def normalize_book_review(
    payload: dict[str, Any],
    *,
    context: BookReviewContext,
) -> dict:
    summary = _user_visible_text(
        payload.get("summary"),
        context=context,
        limit=1000,
    )
    if not summary:
        raise ValueError("book review summary is empty")
    candidates = payload.get("candidate_issues")
    if not isinstance(candidates, list):
        candidates = []
    issues: list[dict] = []
    promoted_guidance: list[str] = []
    allowed_types = {
        "context",
        "sequence",
        "relationship",
        "impact",
        "meaning",
        "clarity",
        "structure",
        "conflict",
    }
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        question = _user_visible_text(
            candidate.get("question"),
            context=context,
            limit=180,
        )
        reason = _user_visible_text(
            candidate.get("reason"),
            context=context,
            limit=300,
        )
        if not question or not reason:
            continue
        chapter_ids = _chapter_references(context).decode_many(
            candidate.get("chapter_ids"),
            limit=8,
        )
        if not chapter_ids:
            continue
        issue_type = _compact(candidate.get("issue_type"), limit=24)
        if issue_type == "privacy":
            continue
        priority = _compact(candidate.get("priority"), limit=12)
        resolution = _compact(candidate.get("resolution"), limit=24)
        issue_type = issue_type if issue_type in allowed_types else "clarity"
        if resolution == "editorial_change":
            guidance = question.rstrip("？?。 ")
            if reason and reason not in guidance:
                guidance = f"{guidance}。{reason}"
            if guidance and guidance not in promoted_guidance:
                promoted_guidance.append(guidance)
            continue
        if resolution == "user_decision":
            normalized_channel = "manual_review"
        else:
            normalized_channel = "interview"
        issue = {
            "issue_type": issue_type,
            "channel": normalized_channel,
            "question": question,
            "reason": reason,
            "priority": (
                priority
                if priority in {"high", "medium", "low"}
                else "medium"
            ),
            "chapter_ids": chapter_ids,
            "material_ids": [],
            "resolution": resolution,
        }
        if issue_type == "conflict":
            evidence_quote = _compact(candidate.get("evidence_quote"), limit=160)
            if len("".join(evidence_quote.split())) >= 7 and any(
                evidence_quote in chapter.draft_content
                for chapter in context.chapters
                if chapter.chapter_id in chapter_ids
            ):
                issue["evidence_quote"] = evidence_quote
        issues.append(issue)
    anchor_counts: dict[tuple[str, tuple[str, ...], str], int] = {}
    for issue in issues:
        quote = issue.get("evidence_quote")
        if isinstance(quote, str) and quote:
            key = (issue["issue_type"], tuple(sorted(issue["chapter_ids"])), quote)
            anchor_counts[key] = anchor_counts.get(key, 0) + 1
    for issue in issues:
        quote = issue.get("evidence_quote")
        if isinstance(quote, str) and quote:
            key = (issue["issue_type"], tuple(sorted(issue["chapter_ids"])), quote)
            if anchor_counts[key] > 1:
                issue.pop("evidence_quote", None)
        issue["fingerprint"] = completion_issues.stable_book_issue_fingerprint(issue)
    model_guidance = _user_visible_string_list(
        payload.get("structure_guidance"),
        context=context,
        limit=8,
        item_limit=240,
    )
    structure_guidance: list[str] = []
    for guidance in [*promoted_guidance, *model_guidance]:
        if guidance not in structure_guidance:
            structure_guidance.append(guidance)
        if len(structure_guidance) >= 8:
            break
    structure_findings: list[dict[str, object]] = []
    references = _chapter_references(context)
    for raw in payload.get("structure_findings", []):
        finding = BookStructureFinding.model_validate(raw)
        chapter_ids = list(dict.fromkeys(references.decode(ref) for ref in finding.chapter_ids))
        reason = _user_visible_text(finding.reason, context=context, limit=1000)
        if not reason:
            raise ValueError("结构问题缺少正文依据")
        structure_findings.append(BookStructureFinding(
            kind=finding.kind, chapter_ids=chapter_ids, reason=reason,
        ).model_dump())
        if reason not in structure_guidance:
            structure_guidance.append(reason)
    editorial_actions: list[dict[str, object]] = []
    raw_actions = payload.get("editorial_actions")
    references = _chapter_references(context)
    current_order = [chapter.chapter_id for chapter in context.chapters]
    for raw_action in raw_actions if isinstance(raw_actions, list) else []:
        if not isinstance(raw_action, dict):
            continue
        action_type = _compact(raw_action.get("action_type"), limit=32)
        if action_type not in book_editorial_actions.BOOK_EDITORIAL_ACTION_TYPES:
            continue
        title = _user_visible_text(
            raw_action.get("title"),
            context=context,
            limit=120,
        )
        reason = _user_visible_text(
            raw_action.get("reason"),
            context=context,
            limit=300,
        )
        chapter_ids = references.decode_many(
            raw_action.get("chapter_ids"),
            limit=len(context.chapters),
        )
        metadata_fields = (
            list(
                dict.fromkeys(
                    str(field)
                    for field in raw_action.get("metadata_fields", [])
                    if str(field) in book_editorial_actions.BOOK_METADATA_FIELDS
                )
            )
            if isinstance(raw_action.get("metadata_fields"), list)
            else []
        )
        if (
            not title
            or not reason
            or (action_type != "revise_book_profile" and not chapter_ids)
            or (action_type == "revise_book_profile" and not metadata_fields)
        ):
            continue
        if action_type == "reorder_chapters":
            if len(chapter_ids) < 2:
                continue
            current_subset = [
                chapter_id
                for chapter_id in current_order
                if chapter_id in chapter_ids
            ]
            if current_subset == chapter_ids:
                continue
        normalized: dict[str, object] = {
            "action_type": action_type,
            "title": title,
            "reason": reason,
            "chapter_ids": chapter_ids,
            "metadata_fields": (
                metadata_fields
                if action_type == "revise_book_profile"
                else []
            ),
        }
        normalized["id"] = book_editorial_actions.action_id(normalized)
        if normalized not in editorial_actions:
            editorial_actions.append(normalized)
    return {
        "summary": summary,
        "strengths": _user_visible_string_list(
            payload.get("strengths"),
            context=context,
            limit=8,
            item_limit=240,
        ),
        "structure_guidance": structure_guidance,
        "structure_findings": structure_findings,
        "editorial_actions": editorial_actions,
        "candidate_issues": issues,
    }


async def get_current_book_review(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookReview | None:
    try:
        context = await load_book_review_context(db, project_id=project_id)
    except HTTPException as exc:
        if exc.status_code == 409:
            return None
        raise
    return await db.scalar(
        select(MemoirBookReview)
        .where(
            MemoirBookReview.project_id == project_id,
            MemoirBookReview.engine == BOOK_REVIEW_ENGINE,
            MemoirBookReview.input_signature == context.input_signature,
        )
        .order_by(
            MemoirBookReview.updated_at.desc(),
            MemoirBookReview.id.desc(),
        )
        .limit(1)
    )


async def generate_or_reuse_book_review(
    db: AsyncSession,
    *,
    project_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    force_regenerate: bool = False,
    progress_callback: BookReviewProgressCallback | None = None,
) -> MemoirBookReview:
    await ensure_chapter_digests(
        db,
        project_id=project_id,
        ai_model=ai_model,
        ai_client=ai_client,
        progress_callback=progress_callback,
    )
    async with AsyncSession(bind=db.bind, expire_on_commit=False) as inputs_db:
        context = await load_book_review_context(inputs_db, project_id=project_id)
    if progress_callback is not None:
        await progress_callback(
            {
                "stage": "reviewing",
                "total_chapter_count": len(context.chapters),
                "processed_chapter_count": len(context.chapters),
                "current_chapter_title": "",
            }
        )
    old_review = (
        MemoirBookReview.project_id == project_id,
        MemoirBookReview.engine == BOOK_REVIEW_ENGINE,
        MemoirBookReview.input_signature != context.input_signature,
    )
    await db.execute(
        update(MemoirBookReview)
        .where(*old_review, MemoirBookReview.status == MemoirSemanticCacheStatus.READY.value)
        .values(status=MemoirSemanticCacheStatus.STALE.value)
    )
    await db.execute(
        delete(MemoirBookReview).where(
            *old_review,
            ~select(MemoirBookRevisionProposal.id)
            .where(MemoirBookRevisionProposal.review_id == MemoirBookReview.id)
            .exists(),
        )
    )
    existing = await db.scalar(
        select(MemoirBookReview).where(
            MemoirBookReview.project_id == project_id,
            MemoirBookReview.engine == BOOK_REVIEW_ENGINE,
            MemoirBookReview.input_signature == context.input_signature,
        )
    )
    if (
        existing is not None
        and existing.status == MemoirSemanticCacheStatus.READY.value
        and not force_regenerate
    ):
        if progress_callback is not None:
            await progress_callback(
                {
                    "stage": "saving",
                    "total_chapter_count": len(context.chapters),
                    "processed_chapter_count": len(context.chapters),
                    "current_chapter_title": "",
                }
            )
        await completion_issues.sync_book_review_issues(
            db,
            project_id=project_id,
            input_signature=existing.input_signature,
            candidates=existing.review_json.get("candidate_issues", []),
        )
        return existing
    client = ai_client or get_ai_service(ai_model)
    prompt = build_book_review_prompt(context)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    timeout_seconds = ai_text_timeout_seconds(policy.timeout_profile)
    started_at = time.monotonic()
    if progress_callback is not None:
        await progress_callback({"provider_request_pending": True,
                                 "provider": getattr(client, "provider_name", None),
                                 "resolved_ai_model": getattr(client, "model_name", ai_model)})
    # Persist stale-cache/progress updates and release the read transaction.
    # The full input signature is checked again before the result is saved.
    await db.commit()
    generation: dict[str, object]
    try:
        generated = await generate_structured_with_client(
            client,
            BOOK_REVIEW_SYSTEM_PROMPT,
            prompt,
            output_model=_BookReviewOutput,
            requested_model=ai_model,
            temperature=0.2,
            max_tokens=policy.max_tokens,
            timeout=timeout_seconds,
            max_attempts=policy.max_attempts,
        )
        review_json = normalize_book_review(
            generated.value.model_dump(),
            context=context,
        )
        # Re-read committed inputs without invalidating the caller's job,
        # directory or voice-session objects.
        async with AsyncSession(bind=db.bind, expire_on_commit=False) as inputs_db:
            current = await load_book_review_context(inputs_db, project_id=project_id)
        if current.input_signature != context.input_signature:
            raise HTTPException(
                status_code=409,
                detail="审阅期间全书内容已变化，请重试",
            )
        if progress_callback is not None:
            await progress_callback(
                {
                    "stage": "saving",
                    "provider_request_pending": False,
                    "total_chapter_count": len(context.chapters),
                    "processed_chapter_count": len(context.chapters),
                    "current_chapter_title": "",
                }
            )
        if existing is not None:
            await db.refresh(existing)
        status = MemoirSemanticCacheStatus.READY
        error = ""
        generation = {
            **generated.response.diagnostics(),
            "engine": BOOK_REVIEW_ENGINE,
            "timeout_seconds": timeout_seconds,
        }
    except (HTTPException, InsufficientAICreditError):
        raise
    except Exception as exc:
        logger.exception("memoir book review generation failed project_id=%s", project_id)
        review_json = {}
        status = MemoirSemanticCacheStatus.FAILED
        error = ai_user_error_message(
            exc,
            "全书审阅服务暂时不可用，请稍后重试",
        )
        generation = {
            **ai_error_metadata(
                exc,
                requested_model=ai_model,
                provider=getattr(client, "provider_name", None),
                resolved_model=getattr(client, "model_name", ai_model),
                timeout_seconds=timeout_seconds,
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
            ),
            "engine": BOOK_REVIEW_ENGINE,
        }
    review = existing or MemoirBookReview(
        project_id=project_id,
        input_signature=context.input_signature,
        engine=BOOK_REVIEW_ENGINE,
    )
    review.status = status.value
    review.review_json = {
        **review_json,
        "generation": generation,
    }
    review.error_message = error
    review.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(review)
    await db.flush()
    if progress_callback is not None:
        await progress_callback({"provider_request_pending": False})
    if status == MemoirSemanticCacheStatus.READY:
        await completion_issues.sync_book_review_issues(
            db,
            project_id=project_id,
            input_signature=review.input_signature,
            candidates=review.review_json.get("candidate_issues", []),
        )
    return review
