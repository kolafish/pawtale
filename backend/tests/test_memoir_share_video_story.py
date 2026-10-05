from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from PIL import ImageChops

from app.services.ai_usage_records import AIUsageContext, current_ai_usage_context
from app.services.memoir import share_video_story_plan as planning
from app.services.memoir.share_video_story_animation import create_story_renderer


TEXT = "今天我们约起，吃了饭走路去耍。大家坐在车里看窗外。"
OUTING = "新都县附近所有的街，所有的老街新街，所有这些名胜古迹都去耍。今天吃了饭约起，约起有有一二十个人，二三十个人，约起。那阵车子也撇脱，搭起车就走。"


def proposal():
    base = {
        "sentence_ids": [0], "setting": "street", "action": "gather", "gesture": "invite",
        "camera": "pan", "framing": "wide", "actor_ids": ["narrator", "companions"],
        "active_actor_id": "narrator", "props": [], "movement_scale": 1.4,
        "evidence": [{"sentence_id": 0, "quote": "今天我们约起，吃了饭走路去耍"}],
        "start_phase": 0, "end_phase": 0.5,
    }
    return {
        "actors": [
            {"id": "narrator", "figure": "person", "evidence": {"sentence_id": 0, "quote": "我们"}},
            {"id": "companions", "figure": "group", "evidence": {"sentence_id": 1, "quote": "大家"}},
        ],
        "shots": [base,
                  dict(base, action="walk", gesture="relaxed", camera="track", framing="medium", start_phase=0.5, end_phase=1),
                  dict(base, sentence_ids=[1], setting="vehicle", action="ride", gesture="relaxed", camera="slow_push",
                       evidence=[{"sentence_id": 1, "quote": "大家坐在车里看窗外"}]),
                  dict(base, sentence_ids=[1], setting="vehicle", action="look_outside", gesture="relaxed", camera="still",
                       framing="medium", start_phase=0.5, end_phase=1,
                       evidence=[{"sentence_id": 1, "quote": "大家坐在车里看窗外"}])],
    }


def mapped(payload=None):
    payload = payload or proposal()
    timings = [{"start_ms": round(i * 24000 / len(TEXT)), "end_ms": round((i + 1) * 24000 / len(TEXT))} for i in range(len(TEXT))]
    return planning._map_proposal(
        planning._ProposedPlan.model_validate(payload), TEXT,
        planning.share_video_sentence_units(TEXT, timings, 24000), 24000, "test", "project-model",
    )


@pytest.mark.parametrize("mutation,reason", [
    (lambda p: p["shots"][0]["evidence"][0].update(quote="我们在北京旅游"), "verbatim"),
    (lambda p: p["actors"][0].update(figure="child"), "actor type"),
    (lambda p: p["shots"][0].update(props=[{"kind": "bowl", "evidence": {"sentence_id": 0, "quote": "吃了饭"}}]), "prop"),
    (lambda p: p["shots"][1].update(start_phase=0.6), "gap or overlap"),
    (lambda p: p["shots"][2].update(sentence_ids=[0]), "original sentence"),
])
def test_story_rejects_invented_facts_and_uncovered_source(mutation, reason):
    payload = proposal()
    mutation(payload)
    with pytest.raises(planning.ShareVideoStoryPlanningError, match=reason):
        mapped(payload)


def test_persisted_story_reuses_only_same_source_and_complete_audio():
    plan = mapped()
    restored = planning.ShareVideoStoryPlan.model_validate(plan.model_dump(mode="json"))
    planning.validate_story_plan_source(restored, TEXT, 24000)
    assert [(shot.start_ms, shot.end_ms) for shot in restored.shots] == [
        (0, 7200), (7200, 14400), (14400, 19200), (19200, 24000),
    ]
    with pytest.raises(planning.ShareVideoStoryPlanningError, match="another narration"):
        planning.validate_story_plan_source(restored, TEXT + "后来我们回家了。", 24000)
    payload = restored.model_dump(mode="json")
    payload["shots"][1]["start_ms"] += 1
    with pytest.raises(ValidationError, match="continuously"):
        planning.ShareVideoStoryPlan.model_validate(payload)


@pytest.mark.parametrize("mutation,reason", [
    (lambda p: p["shots"][2].update(action="walk"), "Vehicle setting"),
    (lambda p: p["shots"][1].update(actor_ids=[], active_actor_id=None), "visible actor"),
])
def test_cached_story_cannot_request_an_unrenderable_action(mutation, reason):
    payload = mapped().model_dump(mode="json")
    mutation(payload)
    with pytest.raises(ValidationError, match=reason):
        planning.ShareVideoStoryPlan.model_validate(payload)


def test_negated_object_mention_does_not_become_a_prop():
    text = "我看了半天，那块石头里面没有水晶。"
    payload = proposal()
    payload["actors"] = [{"id": "narrator", "figure": "person", "evidence": {"sentence_id": 0, "quote": "我看了"}}]
    payload["shots"] = [dict(payload["shots"][0], sentence_ids=[0], setting="plain_room", action="talk", gesture="relaxed",
                             actor_ids=["narrator"], start_phase=0, end_phase=1,
                             evidence=[{"sentence_id": 0, "quote": text}],
                             props=[{"kind": "crystal", "evidence": {"sentence_id": 0, "quote": "没有水晶"}}])]
    with pytest.raises(planning.ShareVideoStoryPlanningError, match="prop"):
        planning._map_proposal(planning._ProposedPlan.model_validate(payload), text,
                              planning.share_video_sentence_units(text, [], 8000), 8000, "test", "test")


async def test_story_uses_project_model_once_and_preserves_usage_context(monkeypatch):
    context = AIUsageContext("story-operation", "memoir-share-video", None, "project")
    client = SimpleNamespace(provider_name="test", model_name="selected-project-model")
    calls = []

    def get_client(model):
        assert model == "selected-project-model"
        return client

    async def generate(actual_client, system, prompt, *, output_model, **kwargs):
        assert actual_client is client
        assert TEXT in prompt
        assert current_ai_usage_context() == context
        assert kwargs["requested_model"] == "selected-project-model"
        assert kwargs["max_attempts"] == 1
        calls.append(1)
        return SimpleNamespace(value=output_model.model_validate(proposal()),
                               response=SimpleNamespace(provider="test", response_model=None, resolved_model="selected-project-model"))

    monkeypatch.setattr(planning, "get_ai_service", get_client)
    monkeypatch.setattr(planning, "generate_structured_with_client", generate)
    previous_context = current_ai_usage_context()
    plan = await planning.plan_share_video_story(TEXT, [], 24000, ai_model="selected-project-model", usage_context=context)
    assert plan.model == "selected-project-model"
    assert plan.source_text_hash == sha256(TEXT.encode()).hexdigest()
    assert len(calls) == 1
    assert current_ai_usage_context() == previous_context


async def test_invalid_model_response_is_reported_without_replacement_plan(monkeypatch):
    payload = proposal()
    payload["shots"][0]["props"] = [{"kind": "crystal", "evidence": {"sentence_id": 0, "quote": "吃了饭"}}]

    async def generate(client, system, prompt, *, output_model, **kwargs):
        return SimpleNamespace(value=output_model.model_validate(payload),
                               response=SimpleNamespace(provider="test", response_model="test", resolved_model="test"))

    monkeypatch.setattr(planning, "get_ai_service", lambda model: SimpleNamespace(provider_name="test", model_name="test"))
    monkeypatch.setattr(planning, "generate_structured_with_client", generate)
    with pytest.raises(planning.ShareVideoStoryPlanningError, match="prop"):
        await planning.plan_share_video_story(TEXT, [], 24000, ai_model="test")


async def test_real_group_narration_exposes_supported_choices_before_model_call(monkeypatch):
    # A production DeepSeek smoke proposed an unsupported actor type. This
    # source states no ages: offer exact candidates before generation while
    # still rejecting a proposal that invents an adult or meal utensil.
    text = OUTING
    seen = []

    async def generate(client, system, prompt, *, output_model, **kwargs):
        payload = json.loads(prompt.rsplit("\n", 1)[-1])
        units = payload["sentences"]
        candidates = [candidate for unit in units for candidate in unit["capabilities"]["actor_candidates"]]
        assert payload["global_actor_candidates"] == candidates
        assert {candidate["figure"] for candidate in candidates} == {"person", "group"}
        assert all(candidate["evidence"]["quote"] == units[candidate["evidence"]["sentence_id"]]["text"] for candidate in candidates)
        assert all(not unit["capabilities"]["prop_candidates"] for unit in units)
        assert units[0]["capabilities"]["preferred_settings"] == ["street"]
        assert units[-1]["capabilities"]["preferred_settings"] == ["vehicle"]
        assert "walk" not in units[-1]["capabilities"]["actions"]
        seen.append(1)
        age_guess = next(candidate for candidate in candidates if candidate["figure"] == "person")
        value = {"actors": [{"id": "narrator", "figure": "adult", "evidence": age_guess["evidence"]}],
                 "shots": [dict(proposal()["shots"][0], sentence_ids=list(range(len(units))), setting="plain_room",
                                 action="talk", gesture="relaxed", actor_ids=["narrator"], start_phase=0, end_phase=1,
                                 evidence=[age_guess["evidence"]])]}
        return SimpleNamespace(value=output_model.model_validate(value),
                               response=SimpleNamespace(provider="test", response_model="test", resolved_model="test"))

    monkeypatch.setattr(planning, "get_ai_service", lambda model: SimpleNamespace(provider_name="test", model_name="test"))
    monkeypatch.setattr(planning, "generate_structured_with_client", generate)
    with pytest.raises(planning.ShareVideoStoryPlanningError, match="actor=narrator, figure=adult"):
        await planning.plan_share_video_story(text, [], 27705, ai_model="test")
    assert seen == [1]


async def test_people_named_earlier_can_perform_later_boarding_action(monkeypatch):
    async def generate(client, system, prompt, *, output_model, **kwargs):
        source = json.loads(prompt.rsplit("\n", 1)[-1])
        units = source["sentences"]
        assert not units[-1]["capabilities"]["actor_candidates"]
        assert "ride" in units[-1]["capabilities"]["actions"]
        group = next(candidate for candidate in source["global_actor_candidates"] if candidate["figure"] == "group")
        actor = dict(group, id="travellers")
        base = dict(proposal()["shots"][0], actor_ids=["travellers"], active_actor_id="travellers",
                    gesture="relaxed", start_phase=0, end_phase=1)
        shots = [dict(base, sentence_ids=[unit["id"]], action="talk", setting="plain_room",
                      evidence=[{"sentence_id": unit["id"], "quote": unit["text"]}]) for unit in units]
        shots[-1].update(action="ride", setting="vehicle")
        return SimpleNamespace(value=output_model.model_validate({"actors": [actor], "shots": shots}),
                               response=SimpleNamespace(provider="test", response_model="test", resolved_model="test"))

    monkeypatch.setattr(planning, "get_ai_service", lambda model: SimpleNamespace(provider_name="test", model_name="test"))
    monkeypatch.setattr(planning, "generate_structured_with_client", generate)
    plan = await planning.plan_share_video_story(OUTING, [], 27705, ai_model="test")
    assert plan.shots[-1].action == "ride"
    assert plan.shots[-1].actor_ids == [plan.actors[0].id]
    assert plan.actors[0].evidence.sentence_id < plan.shots[-1].sentence_ids[0]


async def test_story_with_no_source_people_offers_only_executable_empty_stage(monkeypatch):
    text = "那阵车子也撇脱，搭起车就走。"

    async def generate(client, system, prompt, *, output_model, **kwargs):
        source = json.loads(prompt.rsplit("\n", 1)[-1])
        assert source["global_actor_candidates"] == []
        assert all(unit["capabilities"]["actions"] == ["talk"] for unit in source["sentences"])
        shot = dict(proposal()["shots"][0], actor_ids=[], active_actor_id=None, gesture="relaxed",
                    action="talk", setting="vehicle", start_phase=0, end_phase=1,
                    evidence=[{"sentence_id": 0, "quote": text}])
        return SimpleNamespace(value=output_model.model_validate({"actors": [], "shots": [shot]}),
                               response=SimpleNamespace(provider="test", response_model="test", resolved_model="test"))

    monkeypatch.setattr(planning, "get_ai_service", lambda model: SimpleNamespace(provider_name="test", model_name="test"))
    monkeypatch.setattr(planning, "generate_structured_with_client", generate)
    plan = await planning.plan_share_video_story(text, [], 8000, ai_model="test")
    assert plan.actors == []
    assert plan.shots[0].action == "talk"


def test_long_unpunctuated_sentence_offers_valid_verbatim_evidence_windows():
    # Real units limit visible characters, so long internal spacing can leave
    # a unit above the evidence schema's character limit without punctuation.
    text = "那时" + " " * 120 + "没有水晶后来另一块发现了水晶我们拿着一块石头"
    units = planning.share_video_sentence_units(text, [], 12000)
    assert len(units) == 1 and len(units[0].text) > 100
    capability = planning._sentence_capabilities(units[0])
    candidates = capability["actor_candidates"] + capability["prop_candidates"]
    assert candidates
    for candidate in candidates:
        evidence = planning.StoryEvidence.model_validate(candidate["evidence"])
        assert len(evidence.quote) <= 100
        assert evidence.quote in text
    crystal = next(candidate for candidate in capability["prop_candidates"] if candidate["kind"] == "crystal")
    match = planning._support_match(planning._PROP_WORDS["crystal"], text)
    assert match.start() == text.rindex("晶")
    assert text[match.start() - 5:match.end()] in crystal["evidence"]["quote"]
    assert "没有水晶" in crystal["evidence"]["quote"]
    assert planning._supports(planning._PROP_WORDS["crystal"], crystal["evidence"]["quote"])
    negative_only = planning.share_video_sentence_units("那时" + " " * 120 + "没有水晶", [], 8000)[0]
    assert all(candidate["kind"] != "crystal" for candidate in planning._sentence_capabilities(negative_only)["prop_candidates"])


def test_original_boarding_phrase_cannot_be_staged_as_walking():
    units = planning.share_video_sentence_units(OUTING, [], 27705)
    payload = proposal()
    payload["actors"] = [{"id": "travellers", "figure": "group",
                          "evidence": {"sentence_id": 1, "quote": units[1].text}}]
    base = dict(payload["shots"][0], actor_ids=["travellers"], active_actor_id="travellers", gesture="relaxed",
                start_phase=0, end_phase=1)
    payload["shots"] = [dict(base, sentence_ids=[unit.index], action="talk", setting="plain_room",
                             evidence=[{"sentence_id": unit.index, "quote": unit.text}]) for unit in units]
    payload["shots"][-1].update(setting="vehicle", action="ride")
    plan = planning._map_proposal(planning._ProposedPlan.model_validate(payload), OUTING, units, 27705, "test", "test")
    assert plan.shots[-1].action == "ride"
    payload["shots"][-1].update(setting="plain_room", action="walk")
    with pytest.raises(planning.ShareVideoStoryPlanningError, match="action is unsupported"):
        planning._map_proposal(planning._ProposedPlan.model_validate(payload), OUTING, units, 27705, "test", "test")


@pytest.mark.parametrize("dimensions", [(1280, 720), (720, 1280)])
def test_story_frames_are_reproducible_and_actions_and_camera_move(dimensions):
    plan = mapped()
    render = create_story_renderer(plan, *dimensions)
    first, later = render(8), render(8.5)
    assert first.mode == later.mode == "RGB"
    assert first.size == later.size == dimensions
    assert first.tobytes() == render(8).tobytes()
    assert first.tobytes() != later.tobytes()
    # Both the continuous gait and moving vehicle remain visible to a viewer.
    assert render(17).tobytes() != render(17.5).tobytes()
    assert render(0).size == render(24).size == dimensions
    with pytest.raises(ValueError, match="finite"):
        render(float("nan"))


@pytest.mark.parametrize("framing", ["wide", "medium", "detail"])
def test_people_and_gestures_leave_shared_subtitle_area_clear(framing):
    payload = mapped().model_dump(mode="json")
    for shot in payload["shots"]:
        shot["framing"] = framing
    plan = planning.ShareVideoStoryPlan.model_validate(payload)
    empty_payload = deepcopy(payload)
    for shot in empty_payload["shots"]:
        shot.update(actor_ids=[], active_actor_id=None, action="talk", gesture="relaxed")
    empty = planning.ShareVideoStoryPlan.model_validate(empty_payload)
    render = create_story_renderer(plan, 1280, 720)
    scenery_only = create_story_renderer(empty, 1280, 720)
    # The difference against the same camera/scenery reveals actual visible
    # actor pixels, including invitation hands and moving/seated shoes.
    for t in (1, 2, 5, 8, 9, 11, 15, 17, 20, 21, 23):
        visible = ImageChops.difference(render(t), scenery_only(t)).getbbox()
        assert visible is not None
        assert visible[1] >= 50  # Branding above the faces stays clear too.
        assert visible[3] < 550


def test_source_supported_objects_render_without_external_assets():
    text = "我拿着一块石头坐在院子里。"
    payload = deepcopy(proposal())
    payload["actors"] = [{"id": "narrator", "figure": "person", "evidence": {"sentence_id": 0, "quote": "我拿着"}}]
    payload["shots"] = [dict(payload["shots"][0], setting="courtyard", action="hold_object", gesture="relaxed",
                             actor_ids=["narrator"], start_phase=0, end_phase=1,
                             evidence=[{"sentence_id": 0, "quote": text}],
                             props=[{"kind": "stone", "evidence": {"sentence_id": 0, "quote": "一块石头"}}])]
    plan = planning._map_proposal(planning._ProposedPlan.model_validate(payload), text,
                                 planning.share_video_sentence_units(text, [], 8000), 8000, "test", "test")
    assert create_story_renderer(plan, 640, 360)(4).size == (640, 360)
