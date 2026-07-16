from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.database import Base, register_models
from app.domain.memoir.framework_templates import (
    FrameworkTemplateItemDefinition,
    canonical_template_snapshot,
    framework_template_hash,
    load_framework_template,
)
from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimPublicationStatus,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirFrameworkEvidenceState,
    MemoirFrameworkMatchSource,
    MemoirFrameworkUserResolution,
    MemoirNarrativeActorType,
)
from app.domain.memoir.types import MemoirEditingJobType
from app.migrations.memoir_narrative import _ensure_assignment_framework_schema
from app.models.memoir import MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirFrameworkItem,
    MemoirFrameworkItemClaim,
    MemoirFollowupTask,
    MemoirNarrativeGap,
    MemoirUtteranceInterviewAssignment,
)
from app.models.projects import Project
from app.models.users import User
from app.services.memoir.narrative import (
    decisions,
    framework_matching,
    frameworks,
    interview_policy,
)
from app.services.memoir.narrative.ingestion import NarrativeIngestionResult
from app.services.memoir.narrative.ingestion_jobs import (
    run_narrative_ingestion_job,
    schedule_narrative_ingestion_job,
)
from tests.memoir_helpers import create_test_voice_transcript


class FakeFrameworkAI:
    provider_name = "test"
    model_name = "topic-map-test"

    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.prompts: list[str] = []

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.prompts.append(user_prompt)
        return json.dumps(self.payload, ensure_ascii=False)


def _claim(
    *,
    project_id: str,
    statement: str,
    publication_status: MemoirClaimPublicationStatus = (
        MemoirClaimPublicationStatus.ELIGIBLE
    ),
    actor_user_id: str | None = None,
) -> MemoirClaim:
    actor = (
        MemoirNarrativeActorType.USER
        if actor_user_id
        else MemoirNarrativeActorType.AI
    )
    return MemoirClaim(
        project_id=project_id,
        claim_type=MemoirClaimType.EVENT.value,
        statement=statement,
        verification_status=MemoirClaimVerificationStatus.UNREVIEWED.value,
        publication_status=publication_status.value,
        created_by=actor.value,
        created_by_user_id=actor_user_id,
        updated_by=actor.value,
        updated_by_user_id=actor_user_id,
    )


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
    assert {item.module_key for item in template.items} <= {
        module.module_key for module in template.modules
    }


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


async def test_framework_initialization_is_idempotent_and_saves_snapshot(test_db):
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
    assert first.template_hash == framework_template_hash(
        first.template_snapshot_json
    )
    assert len(first.items) == 12
    assert all(
        item.evidence_state == MemoirFrameworkEvidenceState.NONE.value
        for item in first.items
    )
    assert all(
        item.user_resolution == MemoirFrameworkUserResolution.NONE.value
        for item in first.items
    )
    assert await test_db.scalar(
        select(MemoirNarrativeGap.id).where(
            MemoirNarrativeGap.project_id == project.id
        )
    ) is None
    assert await test_db.scalar(
        select(MemoirFollowupTask.id).where(
            MemoirFollowupTask.project_id == project.id
        )
    ) is None

    first.template_hash = "0" * 64
    with pytest.raises(ValueError, match="snapshot is invalid"):
        await frameworks.framework_for_project(
            test_db,
            project_id=project.id,
        )


async def test_new_mobile_memoir_project_initializes_topic_map(client, test_db):
    response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "自动初始化话题地图"},
    )
    assert response.status_code == 201

    project_framework = await frameworks.framework_for_project(
        test_db,
        project_id=response.json()["id"],
    )

    assert project_framework is not None
    assert len(project_framework.items) == 12


async def test_automatic_matching_preserves_manual_links_and_claim_policy(test_db):
    user = User(
        email="framework-owner@example.com",
        password_hash="test",
        display_name="话题地图用户",
    )
    project = Project(type="memoir", title="话题关系", user_id=user.id)
    test_db.add_all([user, project])
    await test_db.flush()
    project_framework = await frameworks.initialize_project_framework(
        test_db,
        project_id=project.id,
    )
    claim = _claim(
        project_id=project.id,
        statement="小时候我一直住在外婆家的院子里。",
        publication_status=MemoirClaimPublicationStatus.PRIVATE,
        actor_user_id=user.id,
    )
    test_db.add(claim)
    await test_db.flush()

    result = await frameworks.reconcile_automatic_claim_matches(
        test_db,
        project_framework=project_framework,
        claims=[claim],
        matches=(
            frameworks.FrameworkClaimMatch(
                claim_id=claim.id,
                item_key="childhood_and_growth",
                evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                confidence=0.91,
            ),
        ),
        match_source=MemoirFrameworkMatchSource.EVALUATOR,
        matcher_version="matcher-test-1",
    )
    assert result.created == 1
    childhood = next(
        item
        for item in project_framework.items
        if item.item_key == "childhood_and_growth"
    )
    assert childhood.evidence_state == MemoirFrameworkEvidenceState.HAS_MATERIAL.value

    family = next(
        item
        for item in project_framework.items
        if item.item_key == "important_family_members"
    )
    family.user_resolution = MemoirFrameworkUserResolution.ENOUGH_FOR_NOW.value
    family.resolved_by_user_id = user.id
    family.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    test_db.add(
        MemoirFrameworkItemClaim(
            framework_item_id=family.id,
            claim_id=claim.id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
            match_source=MemoirFrameworkMatchSource.MANUAL.value,
            matcher_version=None,
            confidence=None,
        )
    )
    await test_db.flush()

    removed = await frameworks.reconcile_automatic_claim_matches(
        test_db,
        project_framework=project_framework,
        claims=[claim],
        matches=(),
        match_source=MemoirFrameworkMatchSource.EVALUATOR,
        matcher_version="matcher-test-2",
    )
    assert removed.removed == 1
    assert childhood.evidence_state == MemoirFrameworkEvidenceState.NONE.value
    assert family.evidence_state == MemoirFrameworkEvidenceState.HAS_MATERIAL.value
    assert family.user_resolution == MemoirFrameworkUserResolution.ENOUGH_FOR_NOW.value

    await decisions.decide_claim_verification(
        test_db,
        project_id=project.id,
        claim_id=claim.id,
        confirmed=False,
        actor_user_id=user.id,
        idempotency_key="reject-framework-claim",
    )
    assert family.evidence_state == MemoirFrameworkEvidenceState.NONE.value
    assert family.user_resolution == MemoirFrameworkUserResolution.ENOUGH_FOR_NOW.value
    manual = await test_db.get(
        MemoirFrameworkItemClaim,
        (family.id, claim.id),
    )
    assert manual is not None
    assert manual.match_source == MemoirFrameworkMatchSource.MANUAL.value


async def test_framework_matcher_rejects_unknown_candidates(test_db):
    project = Project(type="memoir", title="匹配边界")
    test_db.add(project)
    await test_db.flush()
    project_framework = await frameworks.initialize_project_framework(
        test_db,
        project_id=project.id,
    )
    claim = _claim(project_id=project.id, statement="我小时候在村里读书。")
    test_db.add(claim)
    await test_db.flush()
    ai = FakeFrameworkAI(
        {
            "matches": [
                {
                    "claim_id": claim.id,
                    "item_key": "not-a-topic",
                    "evidence_role": "supports",
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
            project_framework=project_framework,
            claims=[claim],
            ai_client=ai,
        )


async def test_framework_matcher_rejects_more_than_four_items_per_claim(test_db):
    project = Project(type="memoir", title="匹配数量边界")
    test_db.add(project)
    await test_db.flush()
    project_framework = await frameworks.initialize_project_framework(
        test_db,
        project_id=project.id,
    )
    claim = _claim(project_id=project.id, statement="这段话被错误匹配得太宽。")
    test_db.add(claim)
    await test_db.flush()
    item_keys = [item.item_key for item in project_framework.items[:5]]
    ai = FakeFrameworkAI(
        {
            "matches": [
                {
                    "claim_id": claim.id,
                    "item_key": item_key,
                    "evidence_role": "supports",
                    "confidence": 0.9,
                }
                for item_key in item_keys
            ]
        }
    )

    with pytest.raises(
        framework_matching.FrameworkMatchSelectionError,
        match="too_many_items",
    ):
        await framework_matching.select_framework_matches(
            project_framework=project_framework,
            claims=[claim],
            ai_client=ai,
        )


async def test_shadow_matching_job_uses_ids_only_and_updates_evidence(test_db):
    project = Project(type="memoir", title="Shadow 匹配")
    test_db.add(project)
    await test_db.flush()
    await frameworks.initialize_project_framework(
        test_db,
        project_id=project.id,
    )
    claim = _claim(project_id=project.id, statement="第一份工作是在供销社。")
    test_db.add(claim)
    await test_db.flush()

    job = await framework_matching.schedule_framework_matching_job(
        test_db,
        project_id=project.id,
        claim_ids=[claim.id],
        match_source=MemoirFrameworkMatchSource.EVALUATOR,
        enabled=True,
    )
    assert job is not None
    assert job.job_type == MemoirEditingJobType.EVALUATE_TOPIC_MAP_CLAIMS.value
    assert claim.statement not in json.dumps(job.payload_json, ensure_ascii=False)

    result = await framework_matching.run_framework_matching_job(
        test_db,
        job,
        ai_client=FakeFrameworkAI(
            {
                "matches": [
                    {
                        "claim_id": claim.id,
                        "item_key": "work_and_career",
                        "evidence_role": "supports",
                        "confidence": 0.88,
                    }
                ]
            }
        ),
    )
    relation = await test_db.scalar(
        select(MemoirFrameworkItemClaim).where(
            MemoirFrameworkItemClaim.claim_id == claim.id
        )
    )
    item = await test_db.get(MemoirFrameworkItem, relation.framework_item_id)

    assert result.result_json["match_count"] == 1
    assert result.result_json["matches"] == [
        {
            "claim_id": claim.id,
            "item_key": "work_and_career",
            "evidence_role": "supports",
            "confidence": 0.88,
        }
    ]
    assert relation.matcher_version == framework_matching.FRAMEWORK_MATCHER_VERSION
    assert item.evidence_state == MemoirFrameworkEvidenceState.HAS_MATERIAL.value


async def test_shadow_scheduler_batches_claims_without_silent_truncation(test_db):
    project = Project(type="memoir", title="批量 Shadow 匹配")
    test_db.add(project)
    await test_db.flush()

    jobs = await framework_matching.schedule_framework_matching_jobs(
        test_db,
        project_id=project.id,
        claim_ids=[f"claim-{index}" for index in range(21)],
        match_source=MemoirFrameworkMatchSource.BACKFILL,
        enabled=True,
    )

    assert len(jobs) == 2
    assert [len(job.payload_json["claim_ids"]) for job in jobs] == [20, 1]


async def test_successful_ingestion_schedules_passive_topic_matching(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候在外婆家住过很多年。",
    )
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == created["utterance_id"])
        .options(selectinload(MemoirVoiceUtterance.chat_message))
    )
    project = await test_db.get(Project, created["session"]["project_id"])
    job = await schedule_narrative_ingestion_job(
        test_db,
        project_id=project.id,
        utterance=utterance,
        source="topic-map-test",
    )
    captured: dict[str, object] = {}

    async def fake_ingest(*_args, **_kwargs):
        return NarrativeIngestionResult(
            created_claim_ids=("claim-created",),
            reused_claim_ids=("claim-reused",),
            created_evidence_ids=(),
            skipped_claim_keys=(),
            entity_review_candidates={},
            created_story_ids=(),
            reused_story_ids=(),
            story_review_candidates={},
            changed_story_ids=(),
            followup_task_id=None,
            followup_task_status=None,
            answered_followup_task_ids=(),
            provider="test",
            model="test",
            extraction_diagnostics={},
        )

    async def capture_matching(_db, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "app.services.memoir.narrative.ingestion_jobs.ingest_transcript_version",
        fake_ingest,
    )
    monkeypatch.setattr(
        "app.services.memoir.narrative.framework_matching."
        "schedule_framework_matching_jobs",
        capture_matching,
    )

    result = await run_narrative_ingestion_job(test_db, job)

    assert result.status.value == "succeeded"
    assert captured["project_id"] == project.id
    assert captured["claim_ids"] == ["claim-created", "claim-reused"]
    assert captured["match_source"] == MemoirFrameworkMatchSource.EVALUATOR


async def test_framework_assignment_validates_project_and_is_immutable(test_db):
    first_project = Project(type="memoir", title="第一个项目")
    second_project = Project(type="memoir", title="第二个项目")
    test_db.add_all([first_project, second_project])
    await test_db.flush()
    first_framework = await frameworks.initialize_project_framework(
        test_db,
        project_id=first_project.id,
    )
    second_framework = await frameworks.initialize_project_framework(
        test_db,
        project_id=second_project.id,
    )
    session = MemoirVoiceSession(project_id=first_project.id)
    test_db.add(session)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        transcript_raw="我想讲童年的事情。",
    )
    test_db.add(utterance)
    await test_db.flush()

    with pytest.raises(ValueError, match="不属于讲述记录所在项目"):
        await interview_policy.assign_utterance(
            test_db,
            utterance=utterance,
            followup_task=None,
            framework_item=second_framework.items[0],
        )

    assignment = await interview_policy.assign_utterance(
        test_db,
        utterance=utterance,
        followup_task=None,
        framework_item=first_framework.items[0],
    )
    repeated = await interview_policy.assign_utterance(
        test_db,
        utterance=utterance,
        followup_task=None,
        framework_item=first_framework.items[0],
    )

    assert repeated.id == assignment.id
    assert assignment.framework_item_id == first_framework.items[0].id
    assert assignment.followup_task_id is None


async def test_assignment_framework_migration_preserves_existing_targets():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "DROP TABLE memoir_utterance_interview_assignments"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE memoir_utterance_interview_assignments ("
                    "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                    "utterance_id VARCHAR(36) NOT NULL UNIQUE, "
                    "followup_task_id VARCHAR(36), "
                    "policy_decision_id VARCHAR(36), "
                    "created_at DATETIME NOT NULL, "
                    "FOREIGN KEY(utterance_id) "
                    "REFERENCES memoir_voice_utterances(id) ON DELETE CASCADE, "
                    "FOREIGN KEY(followup_task_id) "
                    "REFERENCES memoir_followup_tasks(id), "
                    "FOREIGN KEY(policy_decision_id) "
                    "REFERENCES memoir_interview_policy_decisions(id)"
                    ")"
                )
            )
        async with session_maker() as db:
            project = Project(type="memoir", title="迁移")
            db.add(project)
            await db.flush()
            session = MemoirVoiceSession(project_id=project.id)
            db.add(session)
            await db.flush()
            utterance = MemoirVoiceUtterance(
                voice_session_id=session.id,
                transcript_raw="迁移前回答",
            )
            db.add(utterance)
            await db.flush()
            await db.execute(
                text(
                    "INSERT INTO memoir_utterance_interview_assignments "
                    "(id, utterance_id, followup_task_id, policy_decision_id, created_at) "
                    "VALUES ('assignment-1', :utterance_id, NULL, NULL, CURRENT_TIMESTAMP)"
                ),
                {"utterance_id": utterance.id},
            )
            await db.commit()

        assert await _ensure_assignment_framework_schema(engine) is True
        async with engine.begin() as conn:
            columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text(
                            "PRAGMA table_info("
                            "memoir_utterance_interview_assignments)"
                        )
                    )
                ).all()
            }
            row = (
                await conn.execute(
                    text(
                        "SELECT id, framework_item_id, followup_task_id "
                        "FROM memoir_utterance_interview_assignments"
                    )
                )
            ).one()
            table_sql = str(
                (
                    await conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master "
                            "WHERE type='table' AND "
                            "name='memoir_utterance_interview_assignments'"
                        )
                    )
                ).scalar_one()
            )
        assert "framework_item_id" in columns
        assert tuple(row) == ("assignment-1", None, None)
        assert "ck_memoir_utterance_interview_assignments_one_target" in table_sql

        async with session_maker() as db:
            assignment = await db.get(
                MemoirUtteranceInterviewAssignment,
                "assignment-1",
            )
            assignment.framework_item_id = "framework-item"
            assignment.followup_task_id = "followup-task"
            with pytest.raises(IntegrityError):
                await db.flush()
    finally:
        await engine.dispose()
