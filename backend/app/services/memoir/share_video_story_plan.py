"""Grounded, finite instructions for the memoir's illustrated stage.

Only sentence-group phases are timed. These instructions do not claim word-level
action alignment and can never contain executable drawing or video code.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_usage_records import AIUsageContext, reset_ai_usage_context, set_ai_usage_context
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.share_video_scene_planner import share_video_sentence_units


SHARE_VIDEO_STORY_PLAN_VERSION = "memoir-share-video-story-plan-v1"
_EVIDENCE_MAX_CHARS = 100
StorySetting = Literal["plain_room", "street", "vehicle", "courtyard", "workshop"]
StoryAction = Literal["talk", "walk", "gather", "sit", "ride", "look_outside", "hold_object"]
StoryGesture = Literal["relaxed", "invite", "nod"]
StoryCamera = Literal["still", "pan", "track", "slow_push", "reveal"]
StoryFraming = Literal["wide", "medium", "detail"]
StoryPropKind = Literal["bowl", "stone", "crystal", "table", "steps"]


class ShareVideoStoryPlanningError(ValueError):
    """The proposed stage cannot be traced to the selected narration."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoryEvidence(_Strict):
    sentence_id: int = Field(ge=0)
    quote: str = Field(min_length=2, max_length=_EVIDENCE_MAX_CHARS)


class StoryActor(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    figure: Literal["person", "adult", "child", "group"]
    evidence: StoryEvidence


class StoryProp(_Strict):
    kind: StoryPropKind
    evidence: StoryEvidence


class _ShotDescription(_Strict):
    sentence_ids: list[int] = Field(min_length=1)
    setting: StorySetting
    action: StoryAction
    gesture: StoryGesture
    camera: StoryCamera
    framing: StoryFraming
    actor_ids: list[str] = Field(max_length=4)
    active_actor_id: str | None
    props: list[StoryProp] = Field(max_length=3)
    evidence: list[StoryEvidence] = Field(min_length=1, max_length=3)
    movement_scale: float = Field(ge=0.8, le=1.6)


class _ProposedShot(_ShotDescription):
    start_phase: float = Field(ge=0, lt=1)
    end_phase: float = Field(gt=0, le=1)


class _ProposedPlan(_Strict):
    actors: list[StoryActor] = Field(max_length=4)
    shots: list[_ProposedShot] = Field(min_length=1, max_length=6)


class StoryShot(_ShotDescription):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class ShareVideoStoryPlan(_Strict):
    version: Literal["memoir-share-video-story-plan-v1"] = SHARE_VIDEO_STORY_PLAN_VERSION
    source_text_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration_ms: int = Field(gt=0)
    provider: str
    model: str
    actors: list[StoryActor] = Field(max_length=4)
    shots: list[StoryShot] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_timeline(self) -> ShareVideoStoryPlan:
        ids = {actor.id for actor in self.actors}
        if len(ids) != len(self.actors):
            raise ValueError("Story actors must have unique IDs")
        cursor = 0
        for shot in self.shots:
            if shot.start_ms != cursor or shot.end_ms <= cursor:
                raise ValueError("Story shots must cover the audio continuously")
            cursor = shot.end_ms
            if len(set(shot.actor_ids)) != len(shot.actor_ids) or set(shot.actor_ids) - ids:
                raise ValueError("Story shot references an unknown or duplicate actor")
            if shot.active_actor_id is not None and shot.active_actor_id not in shot.actor_ids:
                raise ValueError("Story performer must appear in the shot")
            if shot.action in ("ride", "look_outside") and shot.setting != "vehicle":
                raise ValueError("Travel actions require a vehicle setting")
            if shot.setting == "vehicle" and shot.action in ("walk", "gather"):
                raise ValueError("Vehicle setting does not support walking or gathering")
            if shot.action != "talk" and not shot.actor_ids:
                raise ValueError("Story action requires a visible actor")
            if shot.gesture != "relaxed" and shot.active_actor_id is None:
                raise ValueError("Story gesture requires a declared performer")
            if shot.action == "hold_object" and (
                shot.active_actor_id is None or not any(prop.kind in ("bowl", "stone", "crystal") for prop in shot.props)
            ):
                raise ValueError("Holding requires a declared performer and handheld prop")
        if cursor != self.duration_ms:
            raise ValueError("Story timeline does not match the audio duration")
        return self


_FIGURE_WORDS = {
    "adult": r"父|母|爸|妈|叔|姨|伯|姑|爷|奶|婆|公|老师|师傅|工人|同事|成年|结婚|上班",
    "child": r"孩|童|娃|小时候|小的时候|小学|幼儿|少年",
    # Require plural wording: a bare "个人" or "同学" can describe one person.
    "group": (
        r"我们|咱们|你们|大家|大伙|他们|她们|人们|众人|多人|一群|一伙|一帮|一家人|全家|两口子|"
        r"[我咱你他她]俩|(?:兄弟|姐妹|姊妹|夫妻)俩|"
        # Counts such as 十一个人, 二十多个人, 十来个人 and 几十号人; a leading 一
        # alone stays singular.
        r"(?:这些|那些|一些|有些|好些|[几两二三四五六七八九十百千][一几两二三四五六七八九十百千]*|[2-9]\d*|1\d+)"
        r"(?:多|来|把|余)?(?:个|位|名|号)?"
        r"(?:人(?!转)|[男女]的|[男女]人|同学|同伴|伙伴|朋友|同事|工友|老乡|孩子|娃)|"
        r"(?:同学|同伴|伙伴|朋友|同事|工友|老乡|乡亲|姐妹|兄弟|孩子|娃娃)们"
    ),
    "person": r"我|你|他|她|人|父|母|爸|妈|叔|姨|伯|姑|爷|奶|婆|公|老师|师傅|同学|同事|孩|娃",
}
_SETTING_WORDS = {
    "street": r"街|巷|路|步行|出门",
    "vehicle": r"车|火车|公交|巴士|客运",
    "courtyard": r"院|门口|天井",
    "workshop": r"厂|车间|工地|作坊|工场",
}
_ACTION_WORDS = {
    # Bare “走” means leaving too: “搭车就走” is riding, not walking.
    "walk": r"步行|徒步|散步|走路|走着|走在|走街串巷|走来走去|走动|迈步|迈开步|脚步|步子|逛街|逛了逛|巷子里转了?一圈",
    "gather": r"约|聚|一起|一块|大家|集合",
    # “一座鼓楼” and “坐落” describe buildings, not seated people.
    "sit": r"坐(?!落|标)|入座|落座",
    "ride": r"车|乘|搭",
    "look_outside": r"窗|看|望|车",
    "hold_object": r"拿|捧|抱|端|握|手|吃|饭|碗|石|晶",
}
_PROP_WORDS = {"bowl": r"碗", "stone": r"石|岩", "crystal": r"晶", "table": r"桌|台子", "steps": r"台阶|阶梯|石阶"}


def _support_match(terms: str, quote: str) -> re.Match[str] | None:
    # A directly negated mention is not evidence to draw that person/object.
    # This narrow check complements the model's grounding; it is not a parser
    # for every kind of historical statement or correction in spoken Chinese.
    for match in re.finditer(terms, quote):
        prefix = re.split(r"[，。；！？,;!?]", quote[:match.start()])[-1][-5:]
        if not re.search(r"没有|没得|没见|不见|无|不是|并非|未有", prefix):
            return match
    return None


def _supports(terms: str, quote: str) -> bool:
    return _support_match(terms, quote) is not None


def _candidate_quote(text: str, terms: str) -> str | None:
    match = _support_match(terms, text)
    if match is None or len(text) < 2:
        return None
    if len(text) <= _EVIDENCE_MAX_CHARS:
        return text
    # Preserve at least the five characters used to inspect negation before
    # the first admissible mention. Never truncate a negated mention into an
    # apparently positive candidate, or feed a quote the schema cannot accept.
    start = max(0, min(match.start() - 5, len(text) - _EVIDENCE_MAX_CHARS))
    return text[start:start + _EVIDENCE_MAX_CHARS]


def _check_quote(evidence: StoryEvidence, sentences: list, *, allowed_ids: list[int] | None = None) -> None:
    if evidence.sentence_id >= len(sentences) or (
        allowed_ids is not None and evidence.sentence_id not in allowed_ids
    ) or evidence.quote not in sentences[evidence.sentence_id].text:
        raise ShareVideoStoryPlanningError("Story evidence must quote its original sentence verbatim")


def _sentence_capabilities(sentence) -> dict[str, object]:
    """Expose the same admissible choices used by the source validator.

    Giving a model only an abstract schema encourages guesses about a narrator's
    age or a meal's utensils. Verbatim source candidates make the finite
    renderer's supported facts actionable without correcting invalid output.
    """
    specific_settings = [setting for setting, terms in _SETTING_WORDS.items() if _supports(terms, sentence.text)]
    return {
        "actor_candidates": [
            {"figure": figure, "evidence": {"sentence_id": sentence.index, "quote": quote}}
            for figure, terms in _FIGURE_WORDS.items() if (quote := _candidate_quote(sentence.text, terms)) is not None
        ],
        "settings": ["plain_room"] + specific_settings,
        "preferred_settings": specific_settings or ["plain_room"],
        "actions": ["talk"] + [action for action, terms in _ACTION_WORDS.items() if _supports(terms, sentence.text)],
        "prop_candidates": [
            {"kind": kind, "evidence": {"sentence_id": sentence.index, "quote": quote}}
            for kind, terms in _PROP_WORDS.items() if (quote := _candidate_quote(sentence.text, terms)) is not None
        ],
    }


def _check_grounding(actors: list[StoryActor], shots: list[_ShotDescription], sentences: list) -> None:
    for actor in actors:
        _check_quote(actor.evidence, sentences)
        if not _supports(_FIGURE_WORDS[actor.figure], actor.evidence.quote):
            raise ShareVideoStoryPlanningError(
                f"Story actor type is unsupported by its source evidence: actor={actor.id}, figure={actor.figure}"
            )
    for shot in shots:
        for evidence in shot.evidence:
            _check_quote(evidence, sentences, allowed_ids=shot.sentence_ids)
        quoted = "".join(evidence.quote for evidence in shot.evidence)
        if shot.setting != "plain_room" and not _supports(_SETTING_WORDS[shot.setting], quoted):
            raise ShareVideoStoryPlanningError("Story setting is unsupported by its source evidence")
        if shot.action != "talk" and not _supports(_ACTION_WORDS[shot.action], quoted):
            raise ShareVideoStoryPlanningError("Story action is unsupported by its source evidence")
        if shot.gesture == "invite" and shot.action != "gather":
            raise ShareVideoStoryPlanningError("Inviting requires a source-supported gathering")
        for prop in shot.props:
            _check_quote(prop.evidence, sentences, allowed_ids=shot.sentence_ids)
            if not _supports(_PROP_WORDS[prop.kind], prop.evidence.quote):
                raise ShareVideoStoryPlanningError("Story prop is unsupported by its specific source evidence")


def validate_story_plan_source(plan: ShareVideoStoryPlan, selected_text: str, duration_ms: int) -> None:
    """Check a persisted plan against the exact source before reusing it."""
    if plan.duration_ms != duration_ms or plan.source_text_hash != sha256(selected_text.encode()).hexdigest():
        raise ShareVideoStoryPlanningError("Story plan belongs to another narration or audio duration")
    sentences = share_video_sentence_units(selected_text, [], duration_ms)
    _check_grounding(plan.actors, plan.shots, sentences)
    expected = 0
    previous = None
    for shot in plan.shots:
        ids = shot.sentence_ids
        if ids != list(range(ids[0], ids[-1] + 1)) or ids[-1] >= len(sentences):
            raise ShareVideoStoryPlanningError("Story sentence group must be consecutive")
        if ids != previous:
            if ids[0] != expected:
                raise ShareVideoStoryPlanningError("Story sentence groups must cover the source in order")
            expected = ids[-1] + 1
        previous = ids
    if expected != len(sentences):
        raise ShareVideoStoryPlanningError("Story sentence groups do not cover the source")


def _map_proposal(proposal: _ProposedPlan, text: str, sentences: list, duration_ms: int, provider: str, model: str) -> ShareVideoStoryPlan:
    _check_grounding(proposal.actors, proposal.shots, sentences)
    # Gaps between ASR units belong to the preceding group; first/last include
    # original lead-in/tail silence. No word-level accuracy is inferred here.
    boundaries = [0] + [min(duration_ms, max(0, unit.start_ms)) for unit in sentences[1:]] + [duration_ms]
    shots = []
    expected_sentence, previous_group, phase = 0, None, 1.0
    for shot in proposal.shots:
        ids = shot.sentence_ids
        if ids != list(range(ids[0], ids[-1] + 1)) or ids[-1] >= len(sentences):
            raise ShareVideoStoryPlanningError("Story sentence group must be consecutive")
        if ids != previous_group:
            if abs(phase - 1) > 1e-6 or ids[0] != expected_sentence or abs(shot.start_phase) > 1e-6:
                raise ShareVideoStoryPlanningError("Story sentence groups have a gap or overlap")
            expected_sentence = ids[-1] + 1
        elif abs(shot.start_phase - phase) > 1e-6:
            raise ShareVideoStoryPlanningError("Story phases have a gap or overlap")
        if shot.end_phase <= shot.start_phase:
            raise ShareVideoStoryPlanningError("Story phase interval is empty")
        a, b = boundaries[ids[0]], boundaries[ids[-1] + 1]
        shots.append(StoryShot(
            **shot.model_dump(exclude={"start_phase", "end_phase"}),
            start_ms=round(a + (b - a) * shot.start_phase),
            end_ms=round(a + (b - a) * shot.end_phase),
        ))
        previous_group, phase = ids, shot.end_phase
    if expected_sentence != len(sentences) or abs(phase - 1) > 1e-6:
        raise ShareVideoStoryPlanningError("Story phases do not cover the entire narration")
    return ShareVideoStoryPlan(
        source_text_hash=sha256(text.encode()).hexdigest(), duration_ms=duration_ms,
        provider=provider, model=model, actors=proposal.actors, shots=shots,
    )


async def plan_share_video_story(
    selected_text: str,
    char_timings: list[dict[str, int]],
    duration_ms: int,
    *,
    ai_model: str | None = None,
    usage_context: AIUsageContext | None = None,
) -> ShareVideoStoryPlan:
    if not selected_text.strip() or duration_ms <= 0:
        raise ShareVideoStoryPlanningError("A story requires narration and a positive audio duration")
    if char_timings and len(char_timings) != len(selected_text):
        raise ShareVideoStoryPlanningError("Story timing must correspond to every source character")
    timings = char_timings or [
        {"start_ms": round(i * duration_ms / len(selected_text)), "end_ms": round((i + 1) * duration_ms / len(selected_text))}
        for i in range(len(selected_text))
    ]
    sentences = share_video_sentence_units(selected_text, timings, duration_ms)
    capabilities = [_sentence_capabilities(sentence) for sentence in sentences]
    global_actor_candidates = [candidate for capability in capabilities for candidate in capability["actor_candidates"]]
    if not global_actor_candidates:
        for capability in capabilities:
            capability["actions"] = ["talk"]
    model = (ai_model or get_settings().memoir_share_video_scene_planner_model).strip() or None
    client = get_ai_service(model)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    prompt = (
        "为原声回忆制作往事小剧场，只用有限绘图能力，输出严格JSON。原声是真实来源，"
        "画面是情景示意，不能增加对白、人物关系、地点身份、物件或事件。人物造型和群体仅示意，"
        "group用两位代表而不还原人数。年龄不明确用person，只有原文明示才用adult/child。"
        "每句capabilities是程序按原文实际算出的合法能力，不是参考建议："
        "actors是整段故事唯一的全局人物表，只能从global_actor_candidates选figure/evidence并给稳定id，"
        "禁止凭讲述语气猜成年或童年。句内actor_candidates用于提供原文身份依据，不限制该句镜头的演员。"
        "person的成年人式剪纸外观只是无年龄身份的示意，不表示原文确认了年龄。"
        "已在actors声明的人物或群体，后续镜头actor_ids可继续引用其相同id，不要求本句重复人物证据，"
        "例如前句一二十个人已确立group，下一句搭车就走仍用这个group乘车，不能因为本句没有个人二字就清空演员。"
        "只有整段global_actor_candidates为空才不能新增任何人物，此时actions已限制为talk。"
        "每个非talk动作至少引用一个actors中的id，选择动作前先确认有已声明的合法演员。"
        "场景和动作只能从组内句子的settings/actions选择，"
        "有preferred_settings具体环境时优先使用它，例如老街/新街用street、搭车用vehicle；"
        "plain_room只用于原文无法判断场景的段落，不用它占据已明确街景或车内的时间。"
        "走不等于步行：搭车就走/坐车走是ride，walk必须有走路、散步、徒步等明确步行依据。"
        "props只能从组内prop_candidates选kind/evidence；候选quote应完整照抄。"
        "所有evidence.quote包括shots中的证据最多100字，必须是相应原句的连续片段；长句不能整句照抄超过上限。"
        "actors与props都必须有对应原文quote；prop需明确碗/石/晶/桌/台阶，不把吃饭猜成碗。"
        "未知或不支持的内容用plain_room、talk、relaxed自然讲述，只有talk的actor_ids可以为空。"
        "约20秒以上尽量4到6个取景，短片减少。不要靠新增剧情丰富画面。"
        "同一事件可重复同一连续sentence_ids组，start_phase/end_phase从0连续覆盖到1，"
        "相邻组按句子顺序完整覆盖一次。phase只是整组内的表演阶段，不是逐字动作时间。"
        "walk/gather/sit/ride/look_outside/hold_object必须有对应原文依据；ride和look_outside只能在vehicle。"
        "vehicle不支持walk/gather，搭车就走应选择ride；所有非talk动作必须有actor_ids，"
        "非relaxed姿态必须指定active_actor_id，原文明说没有/不是的人物和物件不要画。"
        "hold_object需active_actor_id和手持物件；invite只能用于gather。所有字段必须填写，"
        "actor ID整个故事保持一致；在同一场景用track/slow_push/reveal衔接人物动作。\n"
        + json.dumps({"duration_ms": duration_ms, "selected_text": selected_text,
                      "global_actor_candidates": global_actor_candidates,
                      "sentences": [{"id": s.index, "text": s.text, "start_ms": s.start_ms, "end_ms": s.end_ms,
                                     "capabilities": capability} for s, capability in zip(sentences, capabilities)],
                      "schema": _ProposedPlan.model_json_schema()}, ensure_ascii=False)
    )
    token = set_ai_usage_context(
        user_id=usage_context.user_id, operation=usage_context.operation,
        project_id=usage_context.project_id, operation_id=usage_context.operation_id,
    ) if usage_context else None
    try:
        generated = await generate_structured_with_client(
            client, "你是尊重口述事实的回忆视频分镜师，只输出符合schema的JSON。", prompt,
            output_model=_ProposedPlan, requested_model=model, temperature=0.2,
            max_tokens=policy.max_tokens, timeout=ai_text_timeout_seconds(policy.timeout_profile), max_attempts=1,
        )
        return _map_proposal(
            generated.value, selected_text, sentences, duration_ms,
            generated.response.provider or client.provider_name,
            generated.response.response_model or generated.response.resolved_model or client.model_name,
        )
    finally:
        if token is not None:
            reset_ai_usage_context(token)
