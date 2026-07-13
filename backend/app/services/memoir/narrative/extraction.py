from __future__ import annotations

from copy import deepcopy
import json
import logging

from pydantic import ValidationError

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceSpeakerRole,
    MemoirStoryType,
)
from app.schemas.memoir_narrative import MemoirNarrativeExtraction
from app.schemas.memoir_narrative.extraction import (
    MemoirNarrativeExtractedClaim,
    MemoirNarrativeExtractedEntity,
    MemoirNarrativeExtractedStory,
)
from app.schemas.memoir_narrative.story_slots import story_slot_schema
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


logger = logging.getLogger(__name__)

NARRATIVE_EXTRACTION_SYSTEM_PROMPT = (
    "你是一位严谨的中文回忆录事实编辑。只能抽取原文明确表达、可由逐字引文支持的事实。"
)
NARRATIVE_EXTRACTION_MAX_TOKENS = 6000


def build_narrative_extraction_repair_prompt(
    *,
    source_text: str,
    invalid_response: str,
    validation_error: Exception,
) -> str:
    if isinstance(validation_error, ValidationError):
        errors: object = validation_error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    else:
        errors = [{"type": "invalid_json", "message": str(validation_error)}]
    return f"""上一次事实抽取 JSON 未通过服务端 schema 校验。请只修正结构和字段值，
不要新增、猜测或改写 source_text 没有表达的事实。无法合法修正的 claim 或 story 必须删除。

必须遵守：
- 输出完整 JSON，且只能输出 JSON。
- narrative_role 只能是 background、trigger、action、turning_point、outcome、reflection、context。
- narrative_role 和 slot_keys 是两个不同维度，禁止把任何槽位 key 填进 narrative_role。
- event slot_keys：background、trigger、people、difficulty、actions、turning_point、outcome、reflection。
- relationship slot_keys：meeting、person_portrait、shared_experience、relationship_change、representative_scene、reflection。
- life_stage slot_keys：entry_reason、responsibilities、representative_events、ability_and_character、transition、reflection。
- theme slot_keys：theme_definition、supporting_cases、contrast_or_failure、continuity、reflection。
- time_start/time_end 必须同时为 null，或同时为完整 ISO 8601 datetime（例如 1982-01-01T00:00:00）。
- relationship claim 必须同时引用已声明的 subject 和 object person entity。
- 每条 evidence_quote 必须逐字、连续地出现在 source_text 中。
- entities、claims、stories 无法可靠抽取时可以为空数组。

validation_errors:
{json.dumps(errors, ensure_ascii=False)}

source_text:
{source_text}

invalid_response:
{invalid_response}
"""


def _validate_extraction(content: str) -> MemoirNarrativeExtraction:
    return MemoirNarrativeExtraction.model_validate(extract_json_payload(content))


def _filter_story_slot_keys(raw_story: object) -> tuple[object, int]:
    if not isinstance(raw_story, dict):
        return raw_story, 0
    try:
        story_type = MemoirStoryType(raw_story.get("story_type"))
    except (TypeError, ValueError):
        return raw_story, 0
    allowed_slots = set(story_slot_schema(story_type).slot_keys)
    normalized_story = deepcopy(raw_story)
    removed_count = 0
    links = normalized_story.get("claims")
    if not isinstance(links, list):
        return normalized_story, 0
    for link in links:
        if not isinstance(link, dict) or not isinstance(link.get("slot_keys"), list):
            continue
        slot_keys = link["slot_keys"]
        filtered = [
            slot
            for slot in slot_keys
            if isinstance(slot, str) and slot in allowed_slots
        ]
        removed_count += len(slot_keys) - len(filtered)
        link["slot_keys"] = filtered
    return normalized_story, removed_count


def _salvage_valid_extraction_items(
    content: str,
) -> tuple[MemoirNarrativeExtraction, dict[str, int]] | None:
    payload = extract_json_payload(content)
    if not isinstance(payload, dict) or payload.get("schema_version") != "1":
        return None
    raw_entities = payload.get("entities", [])
    raw_claims = payload.get("claims", [])
    raw_stories = payload.get("stories", [])
    if not all(
        isinstance(items, list)
        for items in (raw_entities, raw_claims, raw_stories)
    ):
        return None

    entities: list[MemoirNarrativeExtractedEntity] = []
    entity_keys: set[str] = set()
    for raw_entity in raw_entities:
        try:
            entity = MemoirNarrativeExtractedEntity.model_validate(raw_entity)
        except ValidationError:
            continue
        if entity.key in entity_keys:
            continue
        entity_keys.add(entity.key)
        entities.append(entity)

    claims: list[MemoirNarrativeExtractedClaim] = []
    claim_keys: set[str] = set()
    for raw_claim in raw_claims:
        try:
            claim = MemoirNarrativeExtractedClaim.model_validate(raw_claim)
            MemoirNarrativeExtraction(
                schema_version="1",
                entities=entities,
                claims=[claim],
                stories=[],
            )
        except ValidationError:
            continue
        if claim.key in claim_keys:
            continue
        claim_keys.add(claim.key)
        claims.append(claim)
    if raw_claims and not claims:
        return None

    stories: list[MemoirNarrativeExtractedStory] = []
    story_keys: set[str] = set()
    discarded_story_slot_key_count = 0
    for raw_story in raw_stories:
        normalized_story, removed_slot_count = _filter_story_slot_keys(raw_story)
        try:
            story = MemoirNarrativeExtractedStory.model_validate(normalized_story)
            MemoirNarrativeExtraction(
                schema_version="1",
                entities=entities,
                claims=claims,
                stories=[story],
            )
        except ValidationError:
            continue
        if story.key in story_keys:
            continue
        story_keys.add(story.key)
        stories.append(story)
        discarded_story_slot_key_count += removed_slot_count

    extraction = MemoirNarrativeExtraction(
        schema_version="1",
        entities=entities,
        claims=claims,
        stories=stories,
    )
    return extraction, {
        "discarded_entity_count": len(raw_entities) - len(entities),
        "discarded_claim_count": len(raw_claims) - len(claims),
        "discarded_story_count": len(raw_stories) - len(stories),
        "discarded_story_slot_key_count": discarded_story_slot_key_count,
    }


def _try_salvage_extraction_items(
    content: str,
) -> tuple[MemoirNarrativeExtraction, dict[str, int]] | None:
    try:
        return _salvage_valid_extraction_items(content)
    except (TypeError, ValueError, ValidationError):
        return None


def _candidate_score(
    candidate: tuple[MemoirNarrativeExtraction, dict[str, int]],
) -> tuple[int, int, int, int]:
    extraction, discarded_counts = candidate
    return (
        len(extraction.claims),
        len(extraction.stories),
        len(extraction.entities),
        -sum(discarded_counts.values()),
    )


def build_narrative_extraction_prompt(
    *,
    source_text: str,
    speaker_role: MemoirClaimEvidenceSpeakerRole,
    narrator_name: str,
    narrator_origin: str,
    existing_story_candidates: list[dict[str, object]],
) -> str:
    context = {
        "speaker_role": speaker_role.value,
        "narrator_name": narrator_name,
        "narrator_origin": narrator_origin,
        "existing_story_candidates": existing_story_candidates,
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
  ],
  "stories": [
    {{
      "key": "story_1",
      "title": "概括同一件事或同一段关系的稳定标题",
      "story_type": "event|relationship|life_stage|theme",
      "summary": "只概括本条 source_text 可支持的内容",
      "time_start": null,
      "time_end": null,
      "time_precision": "exact|day|month|year|period|relative|unknown",
      "primary_place_entity_key": null,
      "existing_story_id": null,
      "match_confidence": 0.0,
      "claims": [
        {{
          "claim_key": "claim_1",
          "narrative_role": "background|trigger|action|turning_point|outcome|reflection|context",
          "slot_keys": ["必须来自对应 story_type 的槽位 key"],
          "evidence_role": "supports|contradicts",
          "is_core": true
        }}
      ]
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
- story 必须引用本次 claims，不能脱离有逐字证据的 claim 创建。
- narrative_role 描述叙事作用，slot_keys 描述结构槽位，两者不能混用。
- event 槽位：background, trigger, people, difficulty, actions, turning_point, outcome, reflection。
- relationship 槽位：meeting, person_portrait, shared_experience, relationship_change, representative_scene, reflection。
- life_stage 槽位：entry_reason, responsibilities, representative_events, ability_and_character, transition, reflection。
- theme 槽位：theme_definition, supporting_cases, contrast_or_failure, continuity, reflection。
- 只有明确是同一件事时才填写 existing_story_id；它只是候选提示，不会绕过服务端校验。
"""


async def extract_narrative_state(
    *,
    source_text: str,
    speaker_role: MemoirClaimEvidenceSpeakerRole,
    narrator_name: str,
    narrator_origin: str,
    existing_story_candidates: list[dict[str, object]] | None = None,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> tuple[
    MemoirNarrativeExtraction,
    str | None,
    str | None,
    dict[str, int | bool],
]:
    client = ai_client or get_ai_service(ai_model)
    content = await client._call_llm(
        NARRATIVE_EXTRACTION_SYSTEM_PROMPT,
        build_narrative_extraction_prompt(
            source_text=source_text,
            speaker_role=speaker_role,
            narrator_name=narrator_name,
            narrator_origin=narrator_origin,
            existing_story_candidates=existing_story_candidates or [],
        ),
        temperature=0.0,
        max_tokens=NARRATIVE_EXTRACTION_MAX_TOKENS,
        timeout=60.0,
    )
    response_text = str(content)
    diagnostics: dict[str, int | bool] = {
        "repair_attempted": False,
        "discarded_entity_count": 0,
        "discarded_claim_count": 0,
        "discarded_story_count": 0,
        "discarded_story_slot_key_count": 0,
        "used_initial_response_after_repair": False,
    }
    try:
        extraction = _validate_extraction(response_text)
    except (TypeError, ValueError, ValidationError) as first_error:
        diagnostics["repair_attempted"] = True
        initial_candidate = _try_salvage_extraction_items(response_text)
        logger.warning(
            "AI narrative extraction schema validation failed; requesting one repair"
        )
        repaired_content = await client._call_llm(
            NARRATIVE_EXTRACTION_SYSTEM_PROMPT,
            build_narrative_extraction_repair_prompt(
                source_text=source_text,
                invalid_response=response_text,
                validation_error=first_error,
            ),
            temperature=0.0,
            max_tokens=NARRATIVE_EXTRACTION_MAX_TOKENS,
            timeout=60.0,
        )
        try:
            repaired_candidate = (
                _validate_extraction(str(repaired_content)),
                {
                    "discarded_entity_count": 0,
                    "discarded_claim_count": 0,
                    "discarded_story_count": 0,
                    "discarded_story_slot_key_count": 0,
                },
            )
        except (TypeError, ValueError, ValidationError) as repair_error:
            repaired_candidate = _try_salvage_extraction_items(str(repaired_content))
            if initial_candidate is None and repaired_candidate is None:
                raise ValueError(
                    "AI narrative extraction did not match schema"
                ) from repair_error
        candidates = [
            ("initial", initial_candidate),
            ("repaired", repaired_candidate),
        ]
        usable_candidates = [
            (source, candidate)
            for source, candidate in candidates
            if candidate is not None
        ]
        source, selected = max(
            usable_candidates,
            key=lambda item: _candidate_score(item[1]),
        )
        extraction, discarded_counts = selected
        diagnostics.update(discarded_counts)
        diagnostics["used_initial_response_after_repair"] = source == "initial"
        if any(discarded_counts.values()):
            logger.warning(
                "AI narrative extraction retained valid items after repair; "
                "source=%s discarded_entities=%s discarded_claims=%s discarded_stories=%s "
                "discarded_story_slot_keys=%s",
                source,
                discarded_counts["discarded_entity_count"],
                discarded_counts["discarded_claim_count"],
                discarded_counts["discarded_story_count"],
                discarded_counts["discarded_story_slot_key_count"],
            )
    return (
        extraction,
        getattr(client, "provider_name", None),
        getattr(client, "model_name", ai_model),
        diagnostics,
    )
