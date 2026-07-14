from __future__ import annotations

from hashlib import sha256
import json
import logging

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimEvidenceSpeakerRole,
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
)
from app.models.memoir import MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirFollowupTask,
    MemoirFollowupTaskUtterance,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
    MemoirNarrativeGapClaim,
)
from app.models.projects import Project
from app.repositories.memoir_narrative import MemoirClaimRepository
from app.schemas.memoir_narrative import (
    MemoirClaimConfirmationResolution,
    MemoirClaimEvidenceCreate,
    MemoirClaimRejectionResolution,
    MemoirEntityMergeResolution,
    MemoirFactResolutionPlan,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.narrative import decision_log, decisions


SYSTEM_PROMPT = (
    "你是严谨的中文回忆录事实编辑。你只能把用户明确表达的事实判断映射到给定 ID，"
    "不能创造人物、事实或 ID；不能确定时必须向用户确认。"
)
AI_FACT_RESOLUTION_UNAVAILABLE_MESSAGE = "AI 暂时无法核对这条事实，请稍后重试。"
AI_FACT_RESOLUTION_INVALID_MESSAGE = "AI 未能准确核对这条事实，请重新确认后再试。"

logger = logging.getLogger(__name__)


async def _answer_sources(
    db: AsyncSession,
    *,
    project_id: str,
    relation: MemoirFollowupTaskUtterance,
) -> tuple[Project, MemoirVoiceUtterance, MemoirTranscriptVersion]:
    project = await db.scalar(
        select(Project).where(Project.id == project_id)
    )
    if project is None or not project.user_id:
        raise ValueError("补全回答对应的项目或用户不存在")
    utterance = await db.scalar(
        select(MemoirVoiceUtterance).where(
            MemoirVoiceUtterance.id == relation.utterance_id,
        )
    )
    if utterance is None:
        raise ValueError("补全回答对应的讲述记录不存在")
    transcript_version_id = await db.scalar(
        select(MemoirChatMessage.transcript_version_id).where(
            MemoirChatMessage.id == utterance.chat_message_id,
            MemoirChatMessage.project_id == project_id,
        )
    )
    transcript_version = (
        await db.get(MemoirTranscriptVersion, transcript_version_id)
        if transcript_version_id
        else None
    )
    if (
        transcript_version is None
        or transcript_version.voice_session_id != utterance.voice_session_id
        or transcript_version.source_artifact_id != utterance.id
    ):
        raise ValueError("补全回答缺少与当前讲述匹配的转写版本")
    return project, utterance, transcript_version


async def _allowed_context(
    db: AsyncSession,
    *,
    project_id: str,
    task: MemoirFollowupTask,
) -> tuple[list[MemoirClaim], list[MemoirNarrativeEntity], dict[str, str], dict[str, str]]:
    task_roles = {relation.claim_id: relation.context_role for relation in task.claims}
    gap_rows = (
        await db.execute(
            select(MemoirNarrativeGapClaim.claim_id, MemoirNarrativeGapClaim.role).where(
                MemoirNarrativeGapClaim.project_id == project_id,
                MemoirNarrativeGapClaim.gap_id == task.gap_id,
            )
        )
    ).all()
    gap_roles = {row.claim_id: row.role for row in gap_rows}
    claim_ids = sorted(set(task_roles) | set(gap_roles))
    claims = list(
        (
            await db.scalars(
                select(MemoirClaim)
                .where(
                    MemoirClaim.project_id == project_id,
                    MemoirClaim.id.in_(claim_ids),
                )
                .order_by(MemoirClaim.id)
            )
        ).all()
    ) if claim_ids else []
    entity_ids = sorted(
        {
            entity_id
            for claim in claims
            for entity_id in (
                claim.subject_entity_id,
                claim.object_entity_id,
                claim.place_entity_id,
            )
            if entity_id
        }
    )
    entities = list(
        (
            await db.scalars(
                select(MemoirNarrativeEntity)
                .where(
                    MemoirNarrativeEntity.project_id == project_id,
                    MemoirNarrativeEntity.id.in_(entity_ids),
                )
                .order_by(MemoirNarrativeEntity.id)
            )
        ).all()
    ) if entity_ids else []
    return claims, entities, task_roles, gap_roles


def _build_prompt(
    *,
    task: MemoirFollowupTask,
    fact_text: str,
    claims: list[MemoirClaim],
    entities: list[MemoirNarrativeEntity],
    task_roles: dict[str, str],
    gap_roles: dict[str, str],
) -> str:
    payload = {
        "task": {
            "id": task.id,
            "question": task.question,
            "purpose": task.purpose,
            "known_context_summary": task.known_context_summary,
        },
        "answer": fact_text,
        "allowed_claims": [
            {
                "id": claim.id,
                "claim_type": claim.claim_type,
                "statement": claim.statement,
                "subject_entity_id": claim.subject_entity_id,
                "object_entity_id": claim.object_entity_id,
                "place_entity_id": claim.place_entity_id,
                "verification_status": claim.verification_status,
                "task_role": task_roles.get(claim.id),
                "gap_role": gap_roles.get(claim.id),
            }
            for claim in claims
        ],
        "allowed_entities": [
            {
                "id": entity.id,
                "entity_type": entity.entity_type,
                "canonical_name": entity.canonical_name,
                "aliases": list(entity.aliases_json or []),
                "description": entity.description,
                "merged_into_entity_id": entity.merged_into_entity_id,
            }
            for entity in entities
        ],
    }
    return f"""根据用户回答，判断是否能对当前事实问题作出明确的结构化处理。

input:
{json.dumps(payload, ensure_ascii=False)}

只输出严格 JSON：
{{
  "status": "resolved | needs_confirmation",
  "operations": [
    {{
      "operation": "merge_entities",
      "source_entity_id": "必须来自 allowed_entities.id",
      "retained_entity_id": "必须来自 allowed_entities.id",
      "reason": "该回答为何明确说明二者是同一对象"
    }},
    {{
      "operation": "confirm_claim",
      "claim_id": "必须来自 allowed_claims.id",
      "reason": "该回答为何确认这条事实"
    }},
    {{
      "operation": "reject_claim",
      "claim_id": "必须来自 allowed_claims.id",
      "reason": "该回答为何否定这条事实"
    }}
  ],
  "confirmation_question": "只有无法准确判断时，向用户提出的一句具体中文问题",
  "reason": "一句中文结论"
}}

约束：
- 只能使用 input 中列出的 Claim 和 Entity ID，不得生成或猜测 ID。
- 用户明确说两个称呼是同一人、同一地点或同一组织时，使用 merge_entities。
- 用户明确确认某条 Claim 时使用 confirm_claim；明确否定时使用 reject_claim。
- 同一回答可以产生多个互不重复的操作；实体合并应先于相关 Claim 的确认或否定。
- 如果回答没有明确对应到唯一的输入对象，status=needs_confirmation、operations=[]，并提出能区分对象的具体问题。
- 不得把“最可能”“通常如此”或已有上下文当成用户确认；不得为了关闭任务而选择操作。
- resolved 时 confirmation_question 必须为空；needs_confirmation 时不得返回任何操作。
"""


def _validate_plan_targets(
    plan: MemoirFactResolutionPlan,
    *,
    claims: list[MemoirClaim],
    entities: list[MemoirNarrativeEntity],
) -> None:
    claims_by_id = {claim.id: claim for claim in claims}
    entities_by_id = {entity.id: entity for entity in entities}
    operation_keys: set[tuple[str, ...]] = set()
    decided_claim_ids: set[str] = set()
    merged_entity_ids: set[str] = set()
    for operation in plan.operations:
        if isinstance(operation, MemoirEntityMergeResolution):
            source = entities_by_id.get(operation.source_entity_id)
            retained = entities_by_id.get(operation.retained_entity_id)
            if source is None or retained is None:
                raise ValueError("entity merge target is outside the follow-up context")
            if source.id == retained.id or source.entity_type != retained.entity_type:
                raise ValueError("entity merge target is invalid")
            if source.merged_into_entity_id or retained.merged_into_entity_id:
                raise ValueError("entity merge target was already merged")
            if source.id in merged_entity_ids or retained.id in merged_entity_ids:
                raise ValueError("one fact resolution cannot merge the same entity twice")
            merged_entity_ids.update((source.id, retained.id))
            key = (operation.operation, source.id, retained.id)
        else:
            if not isinstance(
                operation,
                (MemoirClaimConfirmationResolution, MemoirClaimRejectionResolution),
            ):
                raise ValueError("unsupported fact resolution operation")
            if operation.claim_id not in claims_by_id:
                raise ValueError("claim decision target is outside the follow-up context")
            if operation.claim_id in decided_claim_ids:
                raise ValueError("one fact resolution cannot decide the same claim twice")
            decided_claim_ids.add(operation.claim_id)
            key = (operation.operation, operation.claim_id)
        if key in operation_keys:
            raise ValueError("fact resolution contains a duplicate operation")
        operation_keys.add(key)


async def _record_answer_evidence(
    db: AsyncSession,
    *,
    project_id: str,
    claim_id: str,
    relation: MemoirFollowupTaskUtterance,
    utterance: MemoirVoiceUtterance,
    transcript_version: MemoirTranscriptVersion,
    actor_user_id: str,
    fact_text: str,
    evidence_role: MemoirClaimEvidenceRole,
) -> None:
    existing = await db.scalar(
        select(MemoirClaimEvidence.id).where(
            MemoirClaimEvidence.project_id == project_id,
            MemoirClaimEvidence.claim_id == claim_id,
            MemoirClaimEvidence.transcript_version_id == transcript_version.id,
            MemoirClaimEvidence.evidence_role == evidence_role.value,
            MemoirClaimEvidence.quote == fact_text,
        )
    )
    if existing is not None:
        return
    start_ms = utterance.start_ms
    end_ms = utterance.end_ms
    if start_ms is None or end_ms is None or start_ms < 0 or end_ms <= start_ms:
        start_ms = None
        end_ms = None
    await MemoirClaimRepository(db).add_evidence(
        MemoirClaimEvidenceCreate(
            project_id=project_id,
            claim_id=claim_id,
            evidence_role=evidence_role,
            evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
            utterance_id=relation.utterance_id,
            transcript_version_id=transcript_version.id,
            audio_file_id=utterance.audio_file_id,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker_role=MemoirClaimEvidenceSpeakerRole.NARRATOR,
            quote=fact_text,
            source_text_hash=sha256(transcript_version.content.encode("utf-8")).hexdigest(),
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=actor_user_id,
        )
    )


async def resolve_fact_answer(
    db: AsyncSession,
    *,
    project_id: str,
    task: MemoirFollowupTask,
    relation: MemoirFollowupTaskUtterance,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> bool:
    fact_text = relation.fact_resolution_text.strip()
    if not fact_text:
        raise ValueError("事实核对回答为空")
    if task.status == MemoirFollowupTaskStatus.ANSWERED.value:
        applied_decision = await db.scalar(
            select(MemoirNarrativeDecision.id).where(
                MemoirNarrativeDecision.project_id == project_id,
                MemoirNarrativeDecision.source_followup_task_utterance_id == relation.id,
                MemoirNarrativeDecision.action.in_(
                    [
                        MemoirNarrativeDecisionAction.MERGE.value,
                        MemoirNarrativeDecisionAction.CONFIRM.value,
                        MemoirNarrativeDecisionAction.REJECT.value,
                    ]
                ),
            ).limit(1)
        )
        if applied_decision is not None:
            return True
    project, utterance, transcript_version = await _answer_sources(
        db,
        project_id=project_id,
        relation=relation,
    )
    if fact_text not in transcript_version.content:
        raise ValueError("事实核对文本不属于当前转写版本")
    claims, entities, task_roles, gap_roles = await _allowed_context(
        db,
        project_id=project_id,
        task=task,
    )
    client = ai_client or get_ai_service(ai_model)
    try:
        content = await client._call_llm(
            SYSTEM_PROMPT,
            _build_prompt(
                task=task,
                fact_text=fact_text,
                claims=claims,
                entities=entities,
                task_roles=task_roles,
                gap_roles=gap_roles,
            ),
            temperature=0,
            max_tokens=1000,
            timeout=90.0,
        )
        plan = MemoirFactResolutionPlan.model_validate(
            extract_json_payload(str(content))
        )
        _validate_plan_targets(plan, claims=claims, entities=entities)
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception(
            "memoir fact resolution failed provider=%s",
            getattr(client, "provider_name", "unknown"),
        )
        raise HTTPException(status_code=503, detail=AI_FACT_RESOLUTION_UNAVAILABLE_MESSAGE) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        logger.warning(
            "memoir fact resolution returned invalid data provider=%s error=%s",
            getattr(client, "provider_name", "unknown"),
            exc,
        )
        raise HTTPException(status_code=502, detail=AI_FACT_RESOLUTION_INVALID_MESSAGE) from exc
    except Exception as exc:
        logger.exception(
            "memoir fact resolution failed provider=%s",
            getattr(client, "provider_name", "unknown"),
        )
        raise HTTPException(status_code=503, detail=AI_FACT_RESOLUTION_UNAVAILABLE_MESSAGE) from exc

    if plan.status == "needs_confirmation":
        relation.interpretation_status = (
            MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION.value
        )
        relation.answer_kind = None
        relation.direct_narrative_text = ""
        relation.clarification_text = ""
        relation.fact_resolution_text = ""
        relation.confirmation_question = plan.confirmation_question
        relation.interpretation_reason = plan.reason
        db.add(relation)
        await db.flush()
        return False

    await decision_log.validate_source(
        db,
        project_id=project_id,
        source_followup_task_utterance_id=relation.id,
        source_transcript_version_id=transcript_version.id,
    )
    operations = sorted(
        plan.operations,
        key=lambda operation: 0 if isinstance(operation, MemoirEntityMergeResolution) else 1,
    )
    for operation in operations:
        common = {
            "db": db,
            "project_id": project_id,
            "actor_user_id": project.user_id,
            "reason": operation.reason,
            "source_followup_task_utterance_id": relation.id,
            "source_transcript_version_id": transcript_version.id,
        }
        if isinstance(operation, MemoirEntityMergeResolution):
            await decisions.merge_entities(
                **common,
                source_entity_id=operation.source_entity_id,
                retained_entity_id=operation.retained_entity_id,
                idempotency_key=(
                    f"followup-fact:{relation.id}:merge:"
                    f"{operation.source_entity_id}:{operation.retained_entity_id}"
                ),
            )
            continue
        confirmed = isinstance(operation, MemoirClaimConfirmationResolution)
        await _record_answer_evidence(
            db,
            project_id=project_id,
            claim_id=operation.claim_id,
            relation=relation,
            utterance=utterance,
            transcript_version=transcript_version,
            actor_user_id=project.user_id,
            fact_text=fact_text,
            evidence_role=(
                MemoirClaimEvidenceRole.SUPPORTS
                if confirmed
                else MemoirClaimEvidenceRole.CONTRADICTS
            ),
        )
        await decisions.decide_claim_verification(
            **common,
            claim_id=operation.claim_id,
            confirmed=confirmed,
            idempotency_key=(
                f"followup-fact:{relation.id}:claim:{operation.claim_id}:"
                f"{'confirm' if confirmed else 'reject'}"
            ),
        )
    return True
