from __future__ import annotations

from hashlib import sha256
import json

from pydantic import ValidationError

from app.domain.memoir.narrative_types import (
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRelationRole,
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


def build_semantic_gap_prompt(context: ChapterAuditContext) -> str:
    contract = context.contract
    input_payload = {
        "chapter": {
            "id": context.chapter.id,
            "title": context.chapter.title,
            "objective": contract.objective if contract else "",
            "reader_takeaway": contract.reader_takeaway if contract else "",
        },
        "nodes": [
            {
                "id": node.id,
                "title": node.title,
                "node_type": node.node_type,
                "purpose": node.purpose,
                "sort_order": node.sort_order,
                "story_ids": [
                    link.story_id for link in context.links if link.node_id == node.id
                ],
            }
            for node in context.nodes
        ],
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
                    and claim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value
                    and claim.verification_status
                    not in {
                        MemoirClaimVerificationStatus.DISPUTED.value,
                        MemoirClaimVerificationStatus.REJECTED.value,
                    }
                    and not claim.superseded_by_claim_id
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
  "schema_version": "1",
  "gaps": [
    {{
      "gap_type": "causality|relationship|contradiction|repetition|unclear_reference|unsupported_fact",
      "severity": "high|medium|low",
      "dimension": "causality|person_introduction|reference|transition|list_style|repetition|contradiction|unsupported_fact",
      "title": "简短问题标题",
      "description": "说明问题及为何需要处理，不提供虚构补写",
      "story_ids": ["必须来自 input.stories.id"],
      "claim_ids": ["必须来自 input.stories.claims.id"]
    }}
  ]
}}

约束：
- 只检查因果缺口、人物首次出现缺少介绍、指代不清、转场缺失、履历式罗列、可能重复、可能矛盾和可能无来源事实。
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
        if claim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value
        and claim.verification_status
        not in {
            MemoirClaimVerificationStatus.DISPUTED.value,
            MemoirClaimVerificationStatus.REJECTED.value,
        }
        and not claim.superseded_by_claim_id
    }
    candidates: list[MemoirNarrativeGapCandidate] = []
    for item in output.gaps:
        if item.gap_type not in SEMANTIC_GAP_TYPES:
            raise ValueError("semantic audit returned a deterministic-only gap type")
        story_ids = set(item.story_ids)
        claim_ids = set(item.claim_ids)
        if not story_ids and not claim_ids:
            raise ValueError("semantic gap requires Story or Claim evidence")
        if not story_ids.issubset(valid_story_ids) or not claim_ids.issubset(valid_claim_ids):
            raise ValueError("semantic gap referenced evidence outside the chapter")
        if item.gap_type == MemoirNarrativeGapType.CONTRADICTION and len(claim_ids) < 2:
            raise ValueError("semantic contradiction requires at least two Claims")
        if item.gap_type == MemoirNarrativeGapType.UNSUPPORTED_FACT and not claim_ids:
            raise ValueError("semantic unsupported fact requires a Claim")
        story_ids.update(
            story_id
            for story_id, links in context.story_claims.items()
            if story_id in valid_story_ids
            and any(link.claim_id in claim_ids for link in links)
        )
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
        evidence_identity = "|".join(sorted(story_ids) + sorted(claim_ids))
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
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=context.chapter.id,
                gap_type=item.gap_type,
                severity=severity,
                target_slot=f"semantic:{item.dimension}:{evidence_hash}",
                title=item.title,
                description=item.description,
                detected_by=MemoirNarrativeGapDetectedBy.AI,
                claim_roles={claim_id: claim_role for claim_id in sorted(claim_ids)},
                story_roles={
                    story_id: MemoirNarrativeGapRelationRole.INVOLVED
                    for story_id in sorted(story_ids)
                },
            )
        )
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
