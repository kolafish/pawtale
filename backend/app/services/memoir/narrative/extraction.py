from __future__ import annotations

from collections import Counter
import json
import logging
import re

from pydantic import ValidationError

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceSpeakerRole,
    MemoirClaimType,
)
from app.schemas.memoir_narrative import MemoirNarrativeExtraction
from app.schemas.memoir_narrative.extraction import (
    MemoirNarrativeExtractedClaim,
    MemoirNarrativeExtractedEntity,
    MemoirNarrativeExtractedStory,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


logger = logging.getLogger(__name__)

NARRATIVE_EXTRACTION_SYSTEM_PROMPT = (
    "你是一位严谨的中文回忆录事实编辑。只能抽取原文明确表达、可由逐字引文支持的事实。"
)
NARRATIVE_EXTRACTION_MAX_TOKENS = 6000
NARRATIVE_CLAIM_STORY_BOUNDARY_GUIDANCE = """- Claim 是由原文证据支持、具有单一语义中心的完整命题，不是按逗号、句号或语法分句拆出的事实片段。
- 同一人物、同一时段或场景中，围绕同一个事实、关系判断、心理判断或行动意图的前提、转折、解释、犹豫、强调、重复和结论，应合并为一条 Claim。
- 对 reflection 或主观 relationship，以最终判断或行动意图为语义中心；支撑它的感受、内在冲突、原因、犹豫、对他人态度的不确定和比喻总结，即使能够分别复述，也属于同一条 Claim。
- 例如“觉得彼此不同、羡慕对方的人生、舍不下自己的生活、像平行线”整体是一个关系判断；“想发消息、不知道说什么、想保持联系、不确定对方心意”整体是一个联系意图，均不要按分句重复拆取。
- 只有当内容属于不同事件、不同时间阶段、不同人物关系，或脱离彼此后仍对核验和成书具有独立意义时，才拆成不同 Claim。输出完整保留原意的最小且不重复的 Claim 集合。
- Story 是可跨多次讲述持续积累的事件、关系、人生阶段或贯穿主题，负责组织一个或多个 Claim；它不是单条 Claim 的改写。先确定 Claim，再把它们归入合适的 Story，已有 Story 候选只影响 Story 归属，不得影响 Claim 的数量和粒度。
- 纯主观反思、瞬时行动意图或孤立的关系结论，本身只形成 Claim，不足以建立新的 Story。上述两个示例都不得据此新建同义 Story；只有能确认属于一个范围更宽、可继续积累的既有关系或事件 Story 时才关联。
- 候选标题和摘要如果只概括“彼此不同或无法相交的认知”或者“想发消息或保持联系的意图”，仍与对应 Claim 同义，不能作为 theme 或 relationship Story 复用；范围更宽的 Story 应包含关系经历、关系演变或多个不同阶段。
- 已有 Story 候选不是必须复用，措辞相似也不是复用依据。只有人物身份和叙事范围都明确吻合时才复用；如果标题和摘要只是复述当前 Claim，或边界明显过窄，就不要复用。无法安全归入更宽 Story 时，stories 应为空。
- 输出前检查是否存在同义重复的 Claim、被拆开的同一判断，以及只是复述单条 Claim 的微型 Story；发现后先合并或删除重复项，再输出 JSON。"""
STORY_CLUSTER_CLAIM_TYPES = {
    MemoirClaimType.EVENT,
    MemoirClaimType.RELATIONSHIP,
    MemoirClaimType.CHRONOLOGY,
    MemoirClaimType.REFLECTION,
}
NARRATIVE_CONTROL_ONLY_PATTERNS = (
    re.compile(
        r"^(?:我们|咱们|我)?(?:接着|继续|再|开始|来|先)?"
        r"(?:聊|说|讲)(?:一说|说|一下|一会儿)?(?:刚才|之前|上次)?"
        r"(?:那个|这个)?(?:人|朋友|话题|事情|事|经历|问题)?"
        r"(?:吧|呢|好了|到这里)?[。！？!?]*$"
    ),
    re.compile(
        r"^(?:我们|咱们|我)?(?:先)?(?:换|换到|转到)"
        r"(?:一个|个|下一个)?(?:话题|人|事情|事)(?:吧|呢)?[。！？!?]*$"
    ),
    re.compile(
        r"^(?:今天|这次|我们|咱们|我)?(?:先)?(?:聊|说|讲)到这里"
        r"(?:吧|了)?[。！？!?]*$"
    ),
)


def is_narrative_control_only(source_text: str) -> bool:
    normalized = re.sub(r"\s+", "", source_text.strip())
    return bool(normalized) and len(normalized) <= 40 and any(
        pattern.fullmatch(normalized) for pattern in NARRATIVE_CONTROL_ONLY_PATTERNS
    )


def _ungrouped_story_cluster_claim_keys(
    extraction: MemoirNarrativeExtraction,
    *,
    source_text: str,
) -> set[str]:
    evidenced_claim_keys = {
        claim.key
        for claim in extraction.claims
        if any(
            quote.strip() and quote.strip() in source_text
            for quote in claim.evidence_quotes
        )
    }
    grouped_claim_keys = {
        link.claim_key
        for story in extraction.stories
        for link in story.claims
    }
    ungrouped_claim_keys = evidenced_claim_keys - grouped_claim_keys
    narrator_entity_keys = {
        entity.key for entity in extraction.entities if entity.is_narrator
    }
    claims_by_anchor: dict[tuple[str, str], set[str]] = {}
    claims_by_key = {claim.key: claim for claim in extraction.claims}
    for claim_key in evidenced_claim_keys:
        claim = claims_by_key[claim_key]
        anchors = {
            ("entity", entity_key)
            for entity_key in (claim.subject_entity_key, claim.object_entity_key)
            if entity_key and entity_key not in narrator_entity_keys
        }
        if claim.place_entity_key:
            anchors.add(("place", claim.place_entity_key))
        for anchor in anchors:
            claims_by_anchor.setdefault(anchor, set()).add(claim.key)

    clustered_claim_keys: set[str] = set()
    for claim_keys in claims_by_anchor.values():
        if len(claim_keys) < 2:
            continue
        if not any(
            claims_by_key[claim_key].claim_type in STORY_CLUSTER_CLAIM_TYPES
            for claim_key in claim_keys
        ):
            continue
        clustered_claim_keys.update(claim_keys & ungrouped_claim_keys)
    return clustered_claim_keys


def _validate_story_coverage(
    extraction: MemoirNarrativeExtraction,
    *,
    source_text: str,
) -> MemoirNarrativeExtraction:
    ungrouped_claim_keys = _ungrouped_story_cluster_claim_keys(
        extraction,
        source_text=source_text,
    )
    if ungrouped_claim_keys:
        raise ValueError(
            "evidence-backed claims sharing a person or place anchor are not grouped into Stories: "
            + ", ".join(sorted(ungrouped_claim_keys))
        )
    return extraction


def _validate_extraction_for_source(
    content: str,
    *,
    source_text: str,
) -> MemoirNarrativeExtraction:
    return _validate_story_coverage(
        _validate_extraction(content),
        source_text=source_text,
    )


def _usable_salvaged_candidate(
    candidate: tuple[MemoirNarrativeExtraction, dict[str, int]] | None,
    *,
    source_text: str,
) -> tuple[MemoirNarrativeExtraction, dict[str, int]] | None:
    if candidate is None:
        return None
    try:
        _validate_story_coverage(candidate[0], source_text=source_text)
    except ValueError:
        return None
    return candidate


def build_narrative_extraction_repair_prompt(
    *,
    source_text: str,
    invalid_response: str,
    validation_error: Exception,
    active_completion_context: dict[str, object] | None = None,
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
- time_start/time_end 必须同时为 null，或同时为完整 ISO 8601 datetime（例如 1982-01-01T00:00:00）。
- relationship claim 必须同时引用已声明的 subject 和 object person entity。
- 每条 evidence_quote 必须逐字、连续地出现在 source_text 中。
- stories 中每个对象只能使用 key、title、story_type、summary、time_start、time_end、
  time_precision、primary_place_entity_key、existing_story_id、match_confidence、claims。
- stories[].claims 必须是对象数组，每项结构为
  {{"claim_key": "claim_1", "narrative_role": "background|trigger|action|turning_point|outcome|reflection|context", "is_core": true}}。
  不得使用旧字段 claim_keys，也不得把 narrative_role、subject_entity_key、
  object_entity_key 或 place_entity_key 放在 Story 对象顶层。
- Story 必须是可独立讲述的事件、持续关系、人生阶段或贯穿主题；孤立的时间、身份、属性、症状或背景事实只保留为 Claim。
- 同一段经历的多条 Claim 应归入同一个 Story，不得拆成一批只有单个事实片段的微型 Story。
{NARRATIVE_CLAIM_STORY_BOUNDARY_GUIDANCE}
- validation_errors 指出共享同一人物或地点锚点的可证实 Claim 未归入 Story 时，必须按真实事件或关系补齐 Story 分组；不得丢 Claim，也不得机械地按素材创建一个万能 Story。
- active_completion 非空且原文确实回答该问题时，保留对 active_completion.story_id 的关联；原文无关时不得强行关联。
- entities、claims、stories 无法可靠抽取时可以为空数组。

active_completion:
{json.dumps(active_completion_context, ensure_ascii=False)}

validation_errors:
{json.dumps(errors, ensure_ascii=False)}

source_text:
{source_text}

invalid_response:
{invalid_response}
"""


def _validate_extraction(content: str) -> MemoirNarrativeExtraction:
    return MemoirNarrativeExtraction.model_validate(extract_json_payload(content))


def _duplicate_item_keys(raw_items: list[object]) -> set[str]:
    keys = [
        raw_item.get("key")
        for raw_item in raw_items
        if isinstance(raw_item, dict) and isinstance(raw_item.get("key"), str)
    ]
    return {key for key, count in Counter(keys).items() if count > 1}


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
    duplicate_entity_keys = _duplicate_item_keys(raw_entities)
    entity_keys: set[str] = set()
    for raw_entity in raw_entities:
        if isinstance(raw_entity, dict) and raw_entity.get("key") in duplicate_entity_keys:
            continue
        try:
            entity = MemoirNarrativeExtractedEntity.model_validate(raw_entity)
        except ValidationError:
            continue
        if entity.key in entity_keys:
            continue
        entity_keys.add(entity.key)
        entities.append(entity)

    claims: list[MemoirNarrativeExtractedClaim] = []
    duplicate_claim_keys = _duplicate_item_keys(raw_claims)
    claim_keys: set[str] = set()
    for raw_claim in raw_claims:
        if isinstance(raw_claim, dict) and raw_claim.get("key") in duplicate_claim_keys:
            continue
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
    duplicate_story_keys = _duplicate_item_keys(raw_stories)
    story_keys: set[str] = set()
    for raw_story in raw_stories:
        if isinstance(raw_story, dict) and raw_story.get("key") in duplicate_story_keys:
            continue
        try:
            story = MemoirNarrativeExtractedStory.model_validate(raw_story)
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
    *,
    source_text: str,
) -> tuple[int, int, int]:
    extraction, discarded_counts = candidate
    supported_claims = [
        claim
        for claim in extraction.claims
        if any(quote in source_text for quote in claim.evidence_quotes)
    ]
    supported_claim_keys = {claim.key for claim in supported_claims}
    supported_stories = [
        story
        for story in extraction.stories
        if any(link.claim_key in supported_claim_keys for link in story.claims)
    ]
    supported_quotes = {
        quote
        for claim in supported_claims
        for quote in claim.evidence_quotes
        if quote in source_text
    }
    return (
        len(supported_stories),
        sum(len(quote) for quote in supported_quotes),
        -sum(discarded_counts.values()),
    )


def build_narrative_extraction_prompt(
    *,
    source_text: str,
    speaker_role: MemoirClaimEvidenceSpeakerRole,
    narrator_name: str,
    narrator_origin: str,
    existing_story_candidates: list[dict[str, object]],
    active_completion_context: dict[str, object] | None = None,
) -> str:
    context = {
        "speaker_role": speaker_role.value,
        "narrator_name": narrator_name,
        "narrator_origin": narrator_origin,
        "existing_story_candidates": existing_story_candidates,
        "active_completion": active_completion_context,
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
      "statement": "忠于原文、具有单一语义中心的完整命题",
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
{NARRATIVE_CLAIM_STORY_BOUNDARY_GUIDANCE}
- 时间不确定时保留 time_text，certainty 使用 approximate 或 uncertain，不猜具体日期。
- speaker_role=interviewer 时，采访者提出的问题不是叙述者事实；采访者自己陈述的新事实可以抽取，但会由系统进入人工核实。
- “接着聊那个朋友吧”“换个话题”等只控制访谈流程的句子不是人物关系或回忆事实；整条 source_text 只有这类话语时，entities、claims、stories 全部返回空数组。
- aliases 只能来自原文，不能因为名称相似自行判断是同一个人。
- story 必须引用本次 claims，不能脱离有逐字证据的 claim 创建。
- Story 是可独立讲述的叙事单元，不是 Claim 的同义包装：孤立的时间范围、身份、属性、症状清单、地点或一句背景事实只创建 Claim，不单独创建 Story。
- event 必须包含明确发生的行动、变化或转折；relationship 表达持续的人物关系；life_stage 表达一段人生阶段及其职责或转变；theme 用多个具体事例共同支撑一个贯穿主题。
- 同一段经历的背景、行动、结果和感受应尽量归入同一个 Story；不要为了覆盖每条 Claim 而拆成多个只有一个事实片段的微型 Story。
- 同一条 source_text 中多条可证实 Claim 共享同一非叙述者人物或地点锚点时，不得把这个候选簇留在 Story 之外；应按真实事件、关系或人生阶段分成一个或多个 Story，不得机械地按素材数量创建 Story。
- 只有明确是同一件事时才填写 existing_story_id；它只是候选提示，不会绕过服务端校验。
- active_completion 非空时，source_text 是用户对该问题的直接回答。若内容确实属于其中的目标故事，必须复用 active_completion.story_id；若内容无关，不得强行归入。
"""


async def extract_narrative_state(
    *,
    source_text: str,
    speaker_role: MemoirClaimEvidenceSpeakerRole,
    narrator_name: str,
    narrator_origin: str,
    existing_story_candidates: list[dict[str, object]] | None = None,
    active_completion_context: dict[str, object] | None = None,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> tuple[
    MemoirNarrativeExtraction,
    str | None,
    str | None,
    dict[str, int | bool],
]:
    if is_narrative_control_only(source_text):
        return (
            MemoirNarrativeExtraction(
                schema_version="1",
                entities=[],
                claims=[],
                stories=[],
            ),
            None,
            None,
            {
                "repair_attempted": False,
                "discarded_entity_count": 0,
                "discarded_claim_count": 0,
                "discarded_story_count": 0,
                "used_initial_response_after_repair": False,
                "control_only_source": True,
            },
        )
    client = ai_client or get_ai_service(ai_model)
    content = await client._call_llm(
        NARRATIVE_EXTRACTION_SYSTEM_PROMPT,
        build_narrative_extraction_prompt(
            source_text=source_text,
            speaker_role=speaker_role,
            narrator_name=narrator_name,
            narrator_origin=narrator_origin,
            existing_story_candidates=existing_story_candidates or [],
            active_completion_context=active_completion_context,
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
        "used_initial_response_after_repair": False,
    }
    try:
        extraction = _validate_extraction_for_source(
            response_text,
            source_text=source_text,
        )
    except (TypeError, ValueError, ValidationError) as first_error:
        diagnostics["repair_attempted"] = True
        initial_candidate = _usable_salvaged_candidate(
            _try_salvage_extraction_items(response_text),
            source_text=source_text,
        )
        logger.warning(
            "AI narrative extraction schema validation failed; requesting one repair"
        )
        repaired_content = await client._call_llm(
            NARRATIVE_EXTRACTION_SYSTEM_PROMPT,
            build_narrative_extraction_repair_prompt(
                source_text=source_text,
                invalid_response=response_text,
                validation_error=first_error,
                active_completion_context=active_completion_context,
            ),
            temperature=0.0,
            max_tokens=NARRATIVE_EXTRACTION_MAX_TOKENS,
            timeout=60.0,
        )
        try:
            repaired_candidate = (
                _validate_extraction_for_source(
                    str(repaired_content),
                    source_text=source_text,
                ),
                {
                    "discarded_entity_count": 0,
                    "discarded_claim_count": 0,
                    "discarded_story_count": 0,
                },
            )
        except (TypeError, ValueError, ValidationError) as repair_error:
            repaired_candidate = _usable_salvaged_candidate(
                _try_salvage_extraction_items(str(repaired_content)),
                source_text=source_text,
            )
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
            key=lambda item: (
                _candidate_score(item[1], source_text=source_text),
                item[0] == "repaired",
            ),
        )
        extraction, discarded_counts = selected
        diagnostics.update(discarded_counts)
        diagnostics["used_initial_response_after_repair"] = source == "initial"
        if any(discarded_counts.values()):
            logger.warning(
                "AI narrative extraction retained valid items after repair; "
                "source=%s discarded_entities=%s discarded_claims=%s discarded_stories=%s",
                source,
                discarded_counts["discarded_entity_count"],
                discarded_counts["discarded_claim_count"],
                discarded_counts["discarded_story_count"],
            )
    return (
        extraction,
        getattr(client, "provider_name", None),
        getattr(client, "model_name", ai_model),
        diagnostics,
    )
