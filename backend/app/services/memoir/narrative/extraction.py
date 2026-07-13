from __future__ import annotations

import json

from pydantic import ValidationError

from app.domain.memoir.narrative_types import MemoirClaimEvidenceSpeakerRole
from app.schemas.memoir_narrative import MemoirNarrativeExtraction
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


NARRATIVE_EXTRACTION_SYSTEM_PROMPT = (
    "你是一位严谨的中文回忆录事实编辑。只能抽取原文明确表达、可由逐字引文支持的事实。"
)


def build_narrative_extraction_prompt(
    *,
    source_text: str,
    speaker_role: MemoirClaimEvidenceSpeakerRole,
    narrator_name: str,
    narrator_origin: str,
) -> str:
    context = {
        "speaker_role": speaker_role.value,
        "narrator_name": narrator_name,
        "narrator_origin": narrator_origin,
    }
    return f"""请从下面一条回忆录转写中抽取人物、地点、机构和事实命题。

context:
{json.dumps(context, ensure_ascii=False)}

source_text:
{source_text}

只输出严格 JSON，不要 Markdown，不要解释。结构必须是：
{{
  "schema_version": "1",
  "entities": [
    {{
      "key": "person_1",
      "entity_type": "person|place|organization",
      "canonical_name": "原文中的名称",
      "aliases": ["原文明确出现的别称"],
      "description": "不引入新事实的简短说明",
      "is_narrator": false
    }}
  ],
  "claims": [
    {{
      "key": "claim_1",
      "claim_type": "event|relationship|attribute|chronology|reflection",
      "statement": "忠于原文的事实描述",
      "subject_entity_key": "person_1 或 null",
      "predicate_code":
        "narrative_fact|family_relationship|social_relationship|identity_attribute|occurred_at|before|after",
      "object_entity_key": null,
      "object_text": null,
      "place_entity_key": null,
      "time_text": "保留原始时间表达或 null",
      "time_start": "只有原文可确定时才填写 ISO 8601，否则 null",
      "time_end": "只有原文可确定时才填写 ISO 8601，否则 null",
      "time_precision": "exact|day|month|year|period|relative|unknown",
      "certainty": "exact|approximate|uncertain",
      "evidence_quotes": ["必须逐字、连续地出现在 source_text 中"]
    }}
  ]
}}

约束：
- 不推断原文没有说出的人物关系、年份、地点、因果或身份。
- “我”指叙述者时，创建 is_narrator=true 的 person entity，并让相关 claim 引用它。
- 每条 claim 至少提供一段逐字引文；无法找到逐字引文就不要输出该 claim。
- 时间不确定时保留 time_text，certainty 使用 approximate 或 uncertain，不猜具体日期。
- speaker_role=interviewer 时，采访者提出的问题不是叙述者事实；采访者自己陈述的新事实可以抽取，但会由系统进入人工核实。
- aliases 只能来自原文，不能因为名称相似自行判断是同一个人。
"""


async def extract_narrative_state(
    *,
    source_text: str,
    speaker_role: MemoirClaimEvidenceSpeakerRole,
    narrator_name: str,
    narrator_origin: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> tuple[MemoirNarrativeExtraction, str | None, str | None]:
    client = ai_client or get_ai_service(ai_model)
    content = await client._call_llm(
        NARRATIVE_EXTRACTION_SYSTEM_PROMPT,
        build_narrative_extraction_prompt(
            source_text=source_text,
            speaker_role=speaker_role,
            narrator_name=narrator_name,
            narrator_origin=narrator_origin,
        ),
        temperature=0.0,
        max_tokens=2600,
        timeout=60.0,
    )
    try:
        extraction = MemoirNarrativeExtraction.model_validate(
            extract_json_payload(str(content))
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("AI narrative extraction did not match schema") from exc
    return (
        extraction,
        getattr(client, "provider_name", None),
        getattr(client, "model_name", ai_model),
    )
