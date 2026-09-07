from __future__ import annotations

from app.services.ai_base import AITextServiceBase

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.memoir.framework_templates import (
    FrameworkTemplateItemDefinition,
    canonical_template_snapshot,
    framework_template_hash,
    load_framework_template,
)
from app.domain.memoir.narrative_types import (
    MemoirFrameworkEvidenceState,
    MemoirFrameworkMatchSource,
)
from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirVoiceUtterance
from app.models.memoir_narrative import (
    MemoirFrameworkItem,
    MemoirFrameworkItemMaterial,
)
from app.models.projects import Project
from app.services.memoir.narrative import framework_matching, frameworks
from tests.memoir_helpers import create_test_voice_transcript


class FakeFrameworkAI(AITextServiceBase):
    provider_name = "test"
    model_name = "topic-map-test"

    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.prompts: list[str] = []

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.prompts.append(user_prompt)
        return json.dumps(self.payload, ensure_ascii=False)


def test_framework_template_v1_is_stable_and_self_contained():
    template = load_framework_template()
    snapshot = canonical_template_snapshot(template)

    assert template.template_key == "memoir_life_topics"
    assert template.version == "1"
    assert len(template.modules) == 5
    assert len(template.items) == 12
    assert framework_template_hash(snapshot) == (
        "76cf0ea635aa601ec5244d9c52454d14a44a4cd0af74ed22fdcccd19590d96d4"
    )


def test_framework_template_rejects_blank_evidence_hints():
    with pytest.raises(ValueError, match="evidence hints must not be blank"):
        FrameworkTemplateItemDefinition(
            item_key="childhood",
            module_key="life_stage",
            title="童年",
            intent="了解童年生活",
            evidence_hints=("小时候", "  "),
            sort_order=10,
        )


async def test_framework_initialization_is_idempotent(test_db):
    project = Project(type="memoir", title="话题地图")
    test_db.add(project)
    await test_db.flush()

    first = await frameworks.initialize_project_framework(
        test_db,
        project_id=project.id,
    )
    second = await frameworks.initialize_project_framework(
        test_db,
        project_id=project.id,
    )

    assert second.id == first.id
    assert len(first.items) == 12
    assert all(
        item.evidence_state == MemoirFrameworkEvidenceState.NONE.value
        for item in first.items
    )


async def test_material_matching_preserves_manual_relation(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="小时候我一直住在外婆家的院子里。",
    )
    framework = await frameworks.framework_for_project(
        test_db,
        project_id=created["session"]["project_id"],
    )
    material = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == created["utterance_id"])
        .options(selectinload(MemoirVoiceUtterance.chat_message))
    )
    childhood = next(
        item
        for item in framework.items
        if item.item_key == "childhood_and_growth"
    )
    family = next(
        item
        for item in framework.items
        if item.item_key == "important_family_members"
    )
    test_db.add(
        MemoirFrameworkItemMaterial(
            framework_item_id=family.id,
            utterance_id=material.id,
            match_source=MemoirFrameworkMatchSource.MANUAL.value,
        )
    )
    await test_db.flush()

    first = await frameworks.reconcile_automatic_material_matches(
        test_db,
        project_framework=framework,
        materials=[material],
        matches=(
            frameworks.FrameworkMaterialMatch(
                utterance_id=material.id,
                item_key=childhood.item_key,
                confidence=0.91,
            ),
        ),
        match_source=MemoirFrameworkMatchSource.EVALUATOR,
        matcher_version="material-test-1",
    )
    second = await frameworks.reconcile_automatic_material_matches(
        test_db,
        project_framework=framework,
        materials=[material],
        matches=(),
        match_source=MemoirFrameworkMatchSource.EVALUATOR,
        matcher_version="material-test-2",
    )

    assert first.created == 1
    assert second.removed == 1
    assert childhood.evidence_state == MemoirFrameworkEvidenceState.NONE.value
    assert family.evidence_state == MemoirFrameworkEvidenceState.HAS_MATERIAL.value
    assert await test_db.get(
        MemoirFrameworkItemMaterial,
        (family.id, material.id),
    ) is not None


async def test_framework_matcher_rejects_unknown_topic(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    framework = await frameworks.framework_for_project(
        test_db,
        project_id=created["session"]["project_id"],
    )
    material = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == created["utterance_id"])
        .options(selectinload(MemoirVoiceUtterance.chat_message))
    )
    ai = FakeFrameworkAI(
        {
            "matches": [
                {
                    "utterance_id": "M1",
                    "item_key": "F999",
                    "confidence": 0.9,
                }
            ]
        }
    )

    with pytest.raises(
        framework_matching.FrameworkMatchSelectionError,
        match="outside_candidates",
    ):
        await framework_matching.select_framework_matches(
            project_framework=framework,
            materials=[material],
            ai_client=ai,
        )
    assert material.id not in ai.prompts[0]
    assert "childhood_and_growth" not in ai.prompts[0]


async def test_topic_map_job_uses_material_ids_and_updates_state(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="第一份工作是在供销社。",
    )
    job = await framework_matching.schedule_framework_matching_job(
        test_db,
        project_id=created["session"]["project_id"],
        material_ids=[created["utterance_id"]],
    )

    assert job.job_type == MemoirEditingJobType.UPDATE_TOPIC_MAP.value
    assert "供销社" not in json.dumps(job.payload_json, ensure_ascii=False)
    result = await framework_matching.run_framework_matching_job(
        test_db,
        job,
        ai_client=FakeFrameworkAI(
            {
                "matches": [
                    {
                        "utterance_id": "M1",
                        "item_key": "F3",
                        "confidence": 0.88,
                    }
                ]
            }
        ),
    )
    relation = await test_db.scalar(
        select(MemoirFrameworkItemMaterial).where(
            MemoirFrameworkItemMaterial.utterance_id
            == created["utterance_id"]
        )
    )
    item = await test_db.get(
        MemoirFrameworkItem,
        relation.framework_item_id,
    )

    assert result.result_json["created"] == 1
    assert relation.matcher_version == (
        framework_matching.FRAMEWORK_MATCHER_VERSION
    )
    assert item.evidence_state == MemoirFrameworkEvidenceState.HAS_MATERIAL.value
