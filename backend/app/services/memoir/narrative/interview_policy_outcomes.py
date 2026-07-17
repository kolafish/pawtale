from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.memoir.narrative_types import (
    MemoirInterviewPolicyExecutionMode,
    MemoirInterviewResponseOutcome,
)
from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirInterviewPolicyDecision,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import interview_policy


OUTCOME_POLICY_VERSION = "interview-policy-outcome-1"
OUTCOME_SETTLE_SECONDS = 20
MAX_OUTCOME_UTTERANCES = 8
MAX_OUTCOME_TEXT_CHARS = 6000


class PolicyOutcomeSelectionError(ValueError):
    pass


async def schedule_policy_outcome_evaluation(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
) -> MemoirEditingJob | None:
    if not utterance.policy_decision_id:
        return None
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_OUTCOME,
        payload_json={
            "decision_id": utterance.policy_decision_id,
            "outcome_policy_version": OUTCOME_POLICY_VERSION,
        },
        dedupe_key=(
            f"interview_policy_outcome:{utterance.policy_decision_id}:"
            f"{OUTCOME_POLICY_VERSION}"
        ),
        run_after=(
            datetime.now(UTC).replace(tzinfo=None)
            + timedelta(seconds=OUTCOME_SETTLE_SECONDS)
        ),
        retry_failed=True,
    )


def _outcome_prompt(
    *,
    decision: MemoirInterviewPolicyDecision,
    generated_message: MemoirChatMessage,
    response_text: str,
) -> str:
    allowed = ", ".join(item.value for item in MemoirInterviewResponseOutcome)
    return f"""判断叙述者对一次回忆录访谈回应的直接反应。

只允许输出 JSON：{{"outcome":"受控枚举"}}。
受控枚举：{allowed}

判断标准：
- continued_story：顺着原来的故事继续展开，不是单纯回答一个明确问题。
- answered_question：主要在回答访谈助手刚提出或确认的问题。
- changed_topic：主动转到不同人物、事件、时间段或主题。
- confused：表示没听懂、反问助手意图，或回应明显显示问题造成困惑。
- stopped：明确暂停、结束、不愿继续或拒绝本轮方向。
- 不得用字数长短判断策略好坏；只判断最主要、最直接的反应。
- 不判断事实真假、内容能否入书、隐私范围或用户决定是否正确。

已执行策略动作：{decision.selected_action}
访谈助手实际回应：
{generated_message.content_text.strip()}

叙述者随后回应：
{response_text.strip()}
"""


async def select_response_outcome(
    *,
    decision: MemoirInterviewPolicyDecision,
    generated_message: MemoirChatMessage,
    response_text: str,
    ai_client: AITextClient,
) -> MemoirInterviewResponseOutcome:
    content = await ai_client._call_llm(
        (
            "你是回忆录访谈结果分类器。"
            "你只返回受控结果，不评价用户，不修改任何事实或业务状态。"
        ),
        _outcome_prompt(
            decision=decision,
            generated_message=generated_message,
            response_text=response_text,
        ),
        temperature=0.0,
        max_tokens=80,
        timeout=15.0,
    )
    payload = extract_json_payload(str(content))
    if not isinstance(payload, dict):
        raise PolicyOutcomeSelectionError("policy_outcome_invalid_json")
    raw_outcome = str(payload.get("outcome") or "").strip()
    try:
        return MemoirInterviewResponseOutcome(raw_outcome)
    except ValueError as exc:
        raise PolicyOutcomeSelectionError("policy_outcome_unknown_value") from exc


async def run_policy_outcome_evaluation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    decision_id = str(payload.get("decision_id") or "").strip()
    if not decision_id:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="policy_outcome_source_unavailable",
        )
    decision = await db.get(MemoirInterviewPolicyDecision, decision_id)
    session = (
        await db.get(MemoirVoiceSession, decision.session_id)
        if decision is not None
        else None
    )
    if (
        decision is None
        or session is None
        or session.project_id != job.project_id
        or decision.execution_mode
        != MemoirInterviewPolicyExecutionMode.ACTIVE.value
        or not decision.generated_message_id
    ):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="policy_outcome_source_unavailable",
        )
    if decision.user_response_outcome is not None:
        return EditingJobResult(
            result_json={
                "decision_id": decision.id,
                "outcome": decision.user_response_outcome,
                "reused": True,
            }
        )
    generated_message = await db.get(
        MemoirChatMessage,
        decision.generated_message_id,
    )
    response_utterances = list(
        (
            await db.scalars(
                select(MemoirVoiceUtterance)
                .where(
                    MemoirVoiceUtterance.policy_decision_id == decision.id,
                    MemoirVoiceUtterance.voice_session_id == decision.session_id,
                )
                .order_by(
                    MemoirVoiceUtterance.created_at,
                    MemoirVoiceUtterance.id,
                )
                .limit(MAX_OUTCOME_UTTERANCES)
            )
        ).all()
    )
    response_text = "\n".join(
        text
        for utterance in response_utterances
        if (
            text := (
                utterance.transcript_edited.strip()
                or utterance.transcript_raw.strip()
            )
        )
    )[:MAX_OUTCOME_TEXT_CHARS]
    if (
        generated_message is None
        or generated_message.voice_session_id != decision.session_id
        or not response_text
    ):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="policy_outcome_source_unavailable",
        )
    client = ai_client
    if client is None:
        settings = get_settings()
        selected_model = (
            settings.memoir_interview_policy_outcome_model.strip()
            or settings.memoir_interview_policy_active_model.strip()
        )
        client = get_ai_service(selected_model or None)
    outcome = await select_response_outcome(
        decision=decision,
        generated_message=generated_message,
        response_text=response_text,
        ai_client=client,
    )
    await interview_policy.record_response_outcome(
        db,
        decision=decision,
        outcome=outcome,
    )
    return EditingJobResult(
        result_json={
            "decision_id": decision.id,
            "outcome": outcome.value,
            "outcome_policy_version": OUTCOME_POLICY_VERSION,
        }
    )
