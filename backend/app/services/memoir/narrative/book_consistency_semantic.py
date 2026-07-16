from __future__ import annotations

from hashlib import sha256
import json

from pydantic import ValidationError

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRelationRole,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
)
from app.schemas.memoir_narrative import (
    MemoirBookSemanticGapAuditOutput,
    MemoirNarrativeGapCandidate,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.narrative.book_consistency_context import BookConsistencyContext
from app.services.memoir.narrative.claim_usage_policy import (
    claim_usable_for_publication,
)


BOOK_SEMANTIC_SYSTEM_PROMPT = (
    "你是严谨的中文回忆录总编辑。只指出输入正文和事实索引能够支持的问题，"
    "不补写事实，不修改正文，不把不确定内容说成确定事实。"
)
BOOK_SEMANTIC_GAP_TYPES = {
    MemoirNarrativeGapType.CAUSALITY,
    MemoirNarrativeGapType.RELATIONSHIP,
    MemoirNarrativeGapType.CONTRADICTION,
    MemoirNarrativeGapType.REPETITION,
    MemoirNarrativeGapType.UNCLEAR_REFERENCE,
    MemoirNarrativeGapType.UNSUPPORTED_FACT,
}


def build_book_semantic_prompt(
    context: BookConsistencyContext,
    *,
    chapter_ids: set[str] | None = None,
    include_block_content: bool = True,
) -> str:
    selected_chapters = [
        chapter
        for chapter in context.chapters
        if chapter_ids is None or chapter.chapter.id in chapter_ids
    ]
    block_by_chapter: dict[str, list] = {}
    for item in context.blocks:
        if chapter_ids is None or item.block.chapter_id in chapter_ids:
            block_by_chapter.setdefault(item.block.chapter_id, []).append(item)
    selected_story_ids = {
        story.id
        for chapter in selected_chapters
        for story in chapter.stories.values()
    }
    selected_claim_ids = {
        link.claim_id
        for chapter in selected_chapters
        for story_id, links in chapter.story_claims.items()
        if story_id in selected_story_ids
        for link in links
    } | {
        claim_id
        for blocks in block_by_chapter.values()
        for block in blocks
        for claim_id in block.claim_ids
    }
    payload = {
        "chapters": [
            {
                "id": chapter.chapter.id,
                "title": chapter.chapter.title,
                "objective": chapter.contract.objective if chapter.contract else "",
                "reader_takeaway": chapter.contract.reader_takeaway if chapter.contract else "",
                "stories": [
                    {
                        "id": story.id,
                        "title": story.title,
                        "summary": story.summary[:600],
                        "time_start": story.time_start.isoformat() if story.time_start else None,
                        "time_end": story.time_end.isoformat() if story.time_end else None,
                    }
                    for story in sorted(chapter.stories.values(), key=lambda item: item.id)
                ],
                "blocks": [
                    {
                        "id": item.block.id,
                        "role": item.block.block_role,
                        "content": item.version.content[:1200] if include_block_content else "",
                        "story_ids": list(item.story_ids),
                        "claim_ids": list(item.claim_ids),
                    }
                    for item in block_by_chapter.get(chapter.chapter.id, [])
                ],
            }
            for chapter in selected_chapters
        ],
        "claims": [
            {
                "id": claim.id,
                "statement": claim.statement[:400],
                "subject": (
                    context.entities[claim.subject_entity_id].canonical_name
                    if claim.subject_entity_id in context.entities
                    else None
                ),
                "object": (
                    context.entities[claim.object_entity_id].canonical_name
                    if claim.object_entity_id in context.entities
                    else claim.object_text
                ),
                "verification_status": claim.verification_status,
                "supporting_evidence_count": context.evidence_counts.get(claim.id, 0),
            }
            for claim in sorted(context.claims.values(), key=lambda item: item.id)
            if claim.id in selected_claim_ids
            and claim_usable_for_publication(claim)
        ],
    }
    return f"""检查下面整本回忆录是否存在需要人工处理的跨章语义问题。

input:
{json.dumps(payload, ensure_ascii=False)}

只输出严格 JSON：
{{
  "schema_version": "1",
  "gaps": [
    {{
      "gap_type": "causality|relationship|contradiction|repetition|unclear_reference|unsupported_fact",
      "severity": "high|medium|low",
      "dimension": "causality|person_introduction|reference|transition|list_style|repetition|contradiction|unsupported_fact",
      "title": "简短问题标题",
      "description": "说明问题及为什么需要人工处理，不提供虚构补写",
      "story_ids": ["来自 input.chapters.stories.id"],
      "claim_ids": ["来自 input.claims.id"],
      "chapter_ids": ["来自 input.chapters.id"]
    }}
  ]
}}

约束：
- 只检查跨章因果、人物首次介绍、指代、转场、履历式罗列、可能重复、可能矛盾和无来源扩写。
- 每个问题必须引用至少一个输入中的 Chapter、Story 或 Claim，所有 ID 必须真实存在。
- contradiction 必须引用至少两条互相冲突的 Claim。
- unsupported_fact 必须引用具体 Claim，不能只凭写作风格猜测。
- high 只用于有明确 Claim 证据的矛盾或无来源核心事实；其余使用 medium 或 low。
- 不输出程序可直接判断的明确时间逆序、缺失槽位、无证据 Claim 或重复主 Story。
- 不建议新增具体事实，不修改正文。
"""


def _validated_book_candidates(
    context: BookConsistencyContext,
    output: MemoirBookSemanticGapAuditOutput,
    *,
    allowed_chapter_ids: set[str],
) -> list[MemoirNarrativeGapCandidate]:
    selected_chapters = [
        chapter
        for chapter in context.chapters
        if chapter.chapter.id in allowed_chapter_ids
    ]
    valid_chapter_ids = {chapter.chapter.id for chapter in selected_chapters}
    valid_story_ids = {
        story.id for chapter in selected_chapters for story in chapter.stories.values()
    }
    scoped_claim_ids = {
        link.claim_id
        for chapter in selected_chapters
        for links in chapter.story_claims.values()
        for link in links
    } | {
        claim_id
        for block in context.blocks
        if block.block.chapter_id in valid_chapter_ids
        for claim_id in block.claim_ids
    }
    valid_claim_ids = {
        claim.id
        for claim in context.claims.values()
        if claim.id in scoped_claim_ids
        and claim_usable_for_publication(claim)
    }
    story_chapter_ids: dict[str, set[str]] = {}
    claim_chapter_ids: dict[str, set[str]] = {}
    for chapter in selected_chapters:
        for story_id in chapter.stories:
            story_chapter_ids.setdefault(story_id, set()).add(chapter.chapter.id)
        for story_id, links in chapter.story_claims.items():
            for link in links:
                claim_chapter_ids.setdefault(link.claim_id, set()).add(chapter.chapter.id)
    for block in context.blocks:
        if block.block.chapter_id not in valid_chapter_ids:
            continue
        for claim_id in block.claim_ids:
            claim_chapter_ids.setdefault(claim_id, set()).add(block.block.chapter_id)
    candidates: list[MemoirNarrativeGapCandidate] = []
    for item in output.gaps:
        if item.gap_type not in BOOK_SEMANTIC_GAP_TYPES:
            raise ValueError("book semantic audit returned a deterministic-only gap type")
        chapter_ids = set(item.chapter_ids)
        story_ids = set(item.story_ids)
        claim_ids = set(item.claim_ids)
        if not chapter_ids and not story_ids and not claim_ids:
            raise ValueError("book semantic gap requires evidence IDs")
        if (
            not chapter_ids.issubset(valid_chapter_ids)
            or not story_ids.issubset(valid_story_ids)
            or not claim_ids.issubset(valid_claim_ids)
        ):
            raise ValueError("book semantic gap referenced evidence outside the project")
        if chapter_ids and any(
            not (story_chapter_ids.get(story_id, set()) & chapter_ids)
            for story_id in story_ids
        ):
            raise ValueError("book semantic Story evidence does not belong to its chapters")
        if chapter_ids and any(
            not (claim_chapter_ids.get(claim_id, set()) & chapter_ids)
            for claim_id in claim_ids
        ):
            raise ValueError("book semantic Claim evidence does not belong to its chapters")
        if item.gap_type == MemoirNarrativeGapType.CONTRADICTION and len(claim_ids) < 2:
            raise ValueError("book semantic contradiction requires at least two Claims")
        if item.gap_type == MemoirNarrativeGapType.UNSUPPORTED_FACT and not claim_ids:
            raise ValueError("book semantic unsupported fact requires a Claim")
        severity = item.severity
        if (
            severity == MemoirNarrativeGapSeverity.HIGH
            and item.gap_type
            not in {
                MemoirNarrativeGapType.CONTRADICTION,
                MemoirNarrativeGapType.UNSUPPORTED_FACT,
            }
        ):
            severity = MemoirNarrativeGapSeverity.MEDIUM
        evidence_identity = "|".join(
            sorted(chapter_ids) + sorted(story_ids) + sorted(claim_ids)
        )
        evidence_hash = sha256(evidence_identity.encode("utf-8")).hexdigest()[:20]
        claim_role = (
            MemoirNarrativeGapClaimRole.CONFLICTING
            if item.gap_type == MemoirNarrativeGapType.CONTRADICTION
            else MemoirNarrativeGapClaimRole.UNSUPPORTED_CANDIDATE
            if item.gap_type == MemoirNarrativeGapType.UNSUPPORTED_FACT
            else MemoirNarrativeGapClaimRole.SUPPORTING
        )
        candidates.append(
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.BOOK,
                gap_type=item.gap_type,
                severity=severity,
                target_slot=f"book:semantic:{item.dimension}:{evidence_hash}",
                title=item.title,
                description=item.description,
                detected_by=MemoirNarrativeGapDetectedBy.AI,
                claim_roles={claim_id: claim_role for claim_id in sorted(claim_ids)},
                story_roles={
                    story_id: MemoirNarrativeGapRelationRole.INVOLVED
                    for story_id in sorted(story_ids)
                },
                chapter_roles={
                    chapter_id: MemoirNarrativeGapRelationRole.INVOLVED
                    for chapter_id in sorted(chapter_ids)
                },
            )
        )
    return candidates


async def evaluate_book_semantic_gaps(
    context: BookConsistencyContext,
    *,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> tuple[list[MemoirNarrativeGapCandidate], str | None, str | None]:
    if not context.chapters:
        return [], None, None
    client = ai_client or get_ai_service(ai_model)
    chapter_ids = [chapter.chapter.id for chapter in context.chapters]
    prompt_scopes: list[tuple[set[str], bool]] = []
    if len(chapter_ids) <= 4:
        prompt_scopes.append((set(chapter_ids), True))
    else:
        prompt_scopes.append((set(chapter_ids), False))
        for start in range(0, len(chapter_ids), 3):
            window = set(chapter_ids[start : start + 4])
            if window:
                prompt_scopes.append((window, True))
    candidates: list[MemoirNarrativeGapCandidate] = []
    try:
        for scope_chapter_ids, include_content in prompt_scopes:
            content = await client._call_llm(
                BOOK_SEMANTIC_SYSTEM_PROMPT,
                build_book_semantic_prompt(
                    context,
                    chapter_ids=scope_chapter_ids,
                    include_block_content=include_content,
                ),
                temperature=0.0,
                max_tokens=5000,
                timeout=90.0,
            )
            output = MemoirBookSemanticGapAuditOutput.model_validate(
                extract_json_payload(str(content))
            )
            candidates.extend(
                _validated_book_candidates(
                    context,
                    output,
                    allowed_chapter_ids=scope_chapter_ids,
                )
            )
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("AI book consistency audit did not match the evidence schema") from exc
    return (
        candidates,
        getattr(client, "provider_name", None),
        getattr(client, "model_name", ai_model),
    )
