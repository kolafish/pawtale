from __future__ import annotations

from hashlib import sha256
import json

from pydantic import ValidationError

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapActionReasonCode,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.schemas.memoir_narrative import (
    MemoirNarrativeGapCandidate,
    MemoirSemanticGapAuditOutput,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.narrative.claim_usage_policy import (
    claim_usable_for_publication,
)
from app.services.memoir.narrative.gap_rules import ChapterAuditContext


SEMANTIC_GAP_SYSTEM_PROMPT = (
    "你是严谨的中文回忆录结构编辑。只指出输入事实能支持的问题，"
    "不补写事实，不修改原文。"
)
SEMANTIC_GAP_TYPES = {
    MemoirNarrativeGapType.CAUSALITY,
    MemoirNarrativeGapType.RELATIONSHIP,
    MemoirNarrativeGapType.CONTRADICTION,
    MemoirNarrativeGapType.REPETITION,
    MemoirNarrativeGapType.UNCLEAR_REFERENCE,
    MemoirNarrativeGapType.UNSUPPORTED_FACT,
}
SEMANTIC_DRAFT_CHAR_LIMIT = 24000
SEMANTIC_GAP_LIMIT = 3
SEMANTIC_DIMENSION_GAP_TYPES = {
    "causality": MemoirNarrativeGapType.CAUSALITY,
    "chapter_context": MemoirNarrativeGapType.CAUSALITY,
    "chapter_focus": MemoirNarrativeGapType.CAUSALITY,
    "opening": MemoirNarrativeGapType.CAUSALITY,
    "closing": MemoirNarrativeGapType.CAUSALITY,
    "transition": MemoirNarrativeGapType.CAUSALITY,
    "list_style": MemoirNarrativeGapType.CAUSALITY,
    "person_introduction": MemoirNarrativeGapType.RELATIONSHIP,
    "relationship_clarity": MemoirNarrativeGapType.RELATIONSHIP,
    "reference": MemoirNarrativeGapType.UNCLEAR_REFERENCE,
    "repetition": MemoirNarrativeGapType.REPETITION,
    "contradiction": MemoirNarrativeGapType.CONTRADICTION,
    "unsupported_fact": MemoirNarrativeGapType.UNSUPPORTED_FACT,
}


def build_semantic_gap_prompt(context: ChapterAuditContext) -> str:
    draft_content = context.draft.content.strip() if context.draft else ""
    input_payload = {
        "chapter": {
            "id": context.chapter.id,
            "title": context.chapter.title,
            "draft": {
                "version_id": context.draft.version_id,
                "status": context.draft.status,
                "content": draft_content[:SEMANTIC_DRAFT_CHAR_LIMIT],
                "content_truncated": len(draft_content) > SEMANTIC_DRAFT_CHAR_LIMIT,
            }
            if context.draft
            else None,
        },
        "stories": [
            {
                "id": story.id,
                "title": story.title,
                "story_type": story.story_type,
                "summary": story.summary,
                "time_start": story.time_start.isoformat() if story.time_start else None,
                "time_end": story.time_end.isoformat() if story.time_end else None,
                "claims": [
                    {
                        "id": claim.id,
                        "statement": claim.statement,
                        "verification_status": claim.verification_status,
                        "supporting_evidence_count": context.evidence_counts.get(claim.id, 0),
                    }
                    for link in context.story_claims.get(story.id, [])
                    if (claim := context.claims.get(link.claim_id)) is not None
                    and claim_usable_for_publication(claim)
                ],
            }
            for story in context.stories.values()
            if story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
            and story.verification_status != MemoirStoryVerificationStatus.REJECTED.value
        ],
    }
    return f"""检查下面章节结构是否存在需要人工处理的语义问题。

input:
{json.dumps(input_payload, ensure_ascii=False)}

只输出严格 JSON：
{{
  "schema_version": "2",
  "gaps": [
    {{
      "gap_type": "causality|relationship|contradiction|repetition|unclear_reference|unsupported_fact",
      "severity": "high|medium|low",
      "dimension": "chapter_context|chapter_focus|opening|closing|person_introduction|relationship_clarity|transition|list_style|causality|reference|repetition|contradiction|unsupported_fact",
      "reason_signal": "missing_new_evidence|existing_material_available|draft_usage_problem|fact_conflict_detected|publication_decision_required",
      "title": "适合直接展示给用户的简短问题标题",
      "description": "说明问题及为何需要处理，不提供虚构补写",
      "story_ids": ["必须来自 input.stories.id"],
      "claim_ids": ["必须来自 input.stories.claims.id"]
    }}
  ]
}}

审计顺序：
1. 先看整章是否缺少进入本章所需的背景、开头是否突兀、结尾是否缺少收束、章节重点是否分散。
2. 再看人物首次出现和人物关系是否清楚、相邻段落是否缺少转场、是否只是履历式罗列。
3. 最后才看核心事件是否因缺少关键前因后果而无法理解。

约束：
- 最多输出 3 个问题，按“最影响读者理解且一次补充能明显改善”的价值从高到低排序；宁缺毋滥。
- 同一 dimension 最多输出一个问题；纯事件前因后果类 causality 最多一个，不能让三个问题都变成事件细节追问。
- title 和 description 必须点明本章具体哪里影响理解，禁止套用“在哪里发生、怎么开始、怎么结束”的通用采访模板。
- 一本回忆录可以有意对次要事件一笔带过。不能仅因为事件简短、没有完整起因经过结果，就要求继续展开。
- opening、closing、chapter_context、chapter_focus、transition、list_style 使用 causality gap_type；person_introduction、relationship_clarity 使用 relationship；reference 使用 unclear_reference。
- reason_signal 只描述可核验的基础原因：确实缺少讲述者掌握的信息用 missing_new_evidence；相关素材已经存在但尚未正确归章用 existing_material_available；问题只在当前正文组织或素材使用上用 draft_usage_problem；事实互相冲突用 fact_conflict_detected；需要确认来源、人物身份或收录决定用 publication_decision_required。
- 人物姓名、人物身份或人物关系如果没有被引用的 Claim 明确支持，不能标为 existing_material_available 或 draft_usage_problem：需要讲述者补充事实用 missing_new_evidence，需要在实名、匿名或具体指代之间做选择用 publication_decision_required。
- draft_usage_problem 只用于 chapter.draft 中已经存在的正文问题；没有整章正文时，现有 Story 的选择、归章或结构组织问题使用 existing_material_available。
- 不得直接输出 interview/editorial/manual_review，最终解决通道由系统规则决定。
- chapter.draft 是当前实际书稿，应优先依据它判断开头、结尾、转场、人物关系和章节重点；没有正文时才主要依据 Story 摘要。
- 不把纯润色偏好当成需要采访的问题；只有补充讲述者掌握的信息才能实质改善时才输出。
- 每个问题必须引用至少一个输入中的 Story 或 Claim；不能用不存在的 ID。
- contradiction 必须引用至少两条互相冲突的 Claim。
- unsupported_fact 必须引用具体 Claim，不能凭写作风格猜测。
- high 只用于有明确 Claim 证据的矛盾或无来源核心事实；其余问题使用 medium 或 low。
- 不输出确定性规则能直接判断的槽位缺失或时间排序问题。
- 不建议新增具体事实，不把不确定内容改写为确定事实。
"""


def _validated_candidates(
    context: ChapterAuditContext,
    output: MemoirSemanticGapAuditOutput,
) -> list[MemoirNarrativeGapCandidate]:
    valid_story_ids = {
        story.id
        for story in context.stories.values()
        if story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
        and story.verification_status != MemoirStoryVerificationStatus.REJECTED.value
    }
    valid_claim_ids = {
        claim.id
        for claim in context.claims.values()
        if claim_usable_for_publication(claim)
    }
    candidates: list[MemoirNarrativeGapCandidate] = []
    selected_dimensions: set[str] = set()
    for item in output.gaps:
        reason_signal = item.reason_signal
        if (
            reason_signal
            == MemoirNarrativeGapActionReasonCode.DRAFT_USAGE_PROBLEM
            and context.draft is None
        ):
            reason_signal = (
                MemoirNarrativeGapActionReasonCode.EXISTING_MATERIAL_AVAILABLE
            )
        if item.gap_type not in SEMANTIC_GAP_TYPES:
            raise ValueError("semantic audit returned a deterministic-only gap type")
        gap_type = SEMANTIC_DIMENSION_GAP_TYPES[item.dimension]
        story_ids = set(item.story_ids)
        claim_ids = set(item.claim_ids)
        if not story_ids and not claim_ids:
            raise ValueError("semantic gap requires Story or Claim evidence")
        if not story_ids.issubset(valid_story_ids) or not claim_ids.issubset(valid_claim_ids):
            raise ValueError("semantic gap referenced evidence outside the chapter")
        if gap_type == MemoirNarrativeGapType.CONTRADICTION and len(claim_ids) < 2:
            raise ValueError("semantic contradiction requires at least two Claims")
        if gap_type == MemoirNarrativeGapType.UNSUPPORTED_FACT and not claim_ids:
            raise ValueError("semantic unsupported fact requires a Claim")
        if (
            reason_signal
            == MemoirNarrativeGapActionReasonCode.FACT_CONFLICT_DETECTED
            and gap_type != MemoirNarrativeGapType.CONTRADICTION
        ):
            raise ValueError("fact conflict signal requires a contradiction gap")
        if item.dimension in selected_dimensions:
            continue
        story_ids.update(
            story_id
            for story_id, links in context.story_claims.items()
            if story_id in valid_story_ids
            and any(link.claim_id in claim_ids for link in links)
        )
        severity = item.severity
        if (
            severity == MemoirNarrativeGapSeverity.HIGH
            and gap_type
            not in {
                MemoirNarrativeGapType.CONTRADICTION,
                MemoirNarrativeGapType.UNSUPPORTED_FACT,
            }
        ):
            severity = MemoirNarrativeGapSeverity.MEDIUM
        evidence_identity = "|".join(sorted(story_ids) + sorted(claim_ids))
        evidence_hash = sha256(evidence_identity.encode("utf-8")).hexdigest()[:20]
        claim_role = (
            MemoirNarrativeGapClaimRole.CONFLICTING
            if gap_type == MemoirNarrativeGapType.CONTRADICTION
            else MemoirNarrativeGapClaimRole.UNSUPPORTED_CANDIDATE
            if gap_type == MemoirNarrativeGapType.UNSUPPORTED_FACT
            else MemoirNarrativeGapClaimRole.SUPPORTING
        )
        candidates.append(
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=context.chapter.id,
                gap_type=gap_type,
                severity=severity,
                target_slot=f"semantic:{item.dimension}:{evidence_hash}",
                title=item.title,
                description=item.description,
                detected_by=MemoirNarrativeGapDetectedBy.AI,
                action_reason_signal=reason_signal,
                claim_roles={claim_id: claim_role for claim_id in sorted(claim_ids)},
                involved_story_ids=set(story_ids),
            )
        )
        selected_dimensions.add(item.dimension)
        if len(candidates) >= SEMANTIC_GAP_LIMIT:
            break
    return candidates


async def evaluate_semantic_gaps(
    context: ChapterAuditContext,
    *,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> tuple[list[MemoirNarrativeGapCandidate], str | None, str | None]:
    if not any(
        story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
        and story.verification_status != MemoirStoryVerificationStatus.REJECTED.value
        for story in context.stories.values()
    ):
        return [], None, None
    client = ai_client or get_ai_service(ai_model)
    content = await client._call_llm(
        SEMANTIC_GAP_SYSTEM_PROMPT,
        build_semantic_gap_prompt(context),
        temperature=0.0,
        max_tokens=3000,
        timeout=60.0,
    )
    try:
        output = MemoirSemanticGapAuditOutput.model_validate(
            extract_json_payload(str(content))
        )
        candidates = _validated_candidates(context, output)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("AI chapter gap audit did not match the evidence schema") from exc
    return (
        candidates,
        getattr(client, "provider_name", None),
        getattr(client, "model_name", ai_model),
    )
