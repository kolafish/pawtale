from __future__ import annotations

from hashlib import sha256
import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimEvidenceSpeakerRole,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirNarrativeGapActionReasonCode,
    MemoirNarrativeActorType,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirNarrativeEntityType,
)
from app.domain.memoir.types import (
    MemoirCreatedBy,
    MemoirChatMessageRole,
    MemoirEditingJobStatus,
    MemoirTranscriptVersionType,
)
from app.models.memoir import MemoirTranscriptVersion, MemoirVoiceUtterance
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
)
from app.models.projects import Project
from app.repositories.memoir_narrative import MemoirClaimRepository, MemoirNarrativeEntityRepository
from app.schemas.memoir_narrative import (
    MemoirClaimCreate,
    MemoirClaimEvidenceCreate,
    MemoirNarrativeGapCandidate,
    MemoirNarrativeExtraction,
    MemoirNarrativeEntityCreate,
)
from app.services.memoir.narrative import completion_items, decisions
from app.services.memoir.narrative.ingestion import (
    ingest_transcript_version,
)
from app.services.memoir.narrative.ingestion_jobs import (
    run_narrative_ingestion_job,
    schedule_narrative_ingestion_job,
)
from app.services.memoir.narrative.gap_rules import load_chapter_audit_context
from app.services.memoir.narrative.extraction import (
    build_narrative_extraction_prompt,
    build_narrative_extraction_repair_prompt,
    is_narrative_control_only,
)
from app.services.memoir.voice_transcripts import prune_transcript_versions_for_utterance
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    memoir_source_ref,
)


class FakeNarrativeAI:
    provider_name = "fake"
    model_name = "fake-narrative"

    def __init__(self, responses: list[dict | str]):
        self.responses = list(responses)
        self.calls = 0

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        return_raw: bool = False,
    ) -> str:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, str):
            return response
        return json.dumps(response, ensure_ascii=False)


def test_extraction_prompt_defines_claim_story_semantic_boundaries():
    prompt = build_narrative_extraction_prompt(
        source_text="最近十年我一直腿疼。",
        speaker_role=MemoirClaimEvidenceSpeakerRole.NARRATOR,
        narrator_name="讲述者",
        narrator_origin="",
        existing_story_candidates=[],
    )

    assert "Story 是可独立讲述的叙事单元，不是 Claim 的同义包装" in prompt
    assert "孤立的时间范围、身份、属性、症状清单" in prompt
    assert "不要为了覆盖每条 Claim 而拆成多个" in prompt
    assert "Claim 是由原文证据支持、具有单一语义中心的完整命题" in prompt
    assert "不是按逗号、句号或语法分句拆出的事实片段" in prompt
    assert "以最终判断或行动意图为语义中心" in prompt
    assert "即使能够分别复述，也属于同一条 Claim" in prompt
    assert "最小且不重复的 Claim 集合" in prompt
    assert "均不要按分句重复拆取" in prompt
    assert "已有 Story 候选只影响 Story 归属，不得影响 Claim 的数量和粒度" in prompt
    assert "纯主观反思、瞬时行动意图或孤立的关系结论" in prompt
    assert "不得据此新建同义 Story" in prompt
    assert "彼此不同或无法相交的认知" in prompt
    assert "范围更宽的 Story 应包含关系经历、关系演变或多个不同阶段" in prompt
    assert "已有 Story 候选不是必须复用" in prompt
    assert "措辞相似也不是复用依据" in prompt
    assert "标题和摘要只是复述当前 Claim" in prompt
    assert "无法安全归入更宽 Story 时，stories 应为空" in prompt
    assert '"statement": "忠于原文、具有单一语义中心的完整命题"' in prompt
    assert "1～3" not in prompt
    assert "最多 4" not in prompt


def test_extraction_repair_prompt_preserves_claim_story_semantic_boundaries():
    prompt = build_narrative_extraction_repair_prompt(
        source_text="想联系他，但不知道说什么，只想保持联系。",
        invalid_response='{"schema_version":"1"}',
        validation_error=ValueError("missing extraction arrays"),
    )

    assert "Claim 是由原文证据支持、具有单一语义中心的完整命题" in prompt
    assert "前提、转折、解释、犹豫、强调、重复和结论，应合并" in prompt
    assert "想发消息、不知道说什么、想保持联系、不确定对方心意" in prompt
    assert "最小且不重复的 Claim 集合" in prompt
    assert "它不是单条 Claim 的改写" in prompt
    assert "本身只形成 Claim，不足以建立新的 Story" in prompt
    assert "已有 Story 候选不是必须复用" in prompt
    assert "发现后先合并或删除重复项" in prompt


def _extraction_payload(source_text: str) -> dict:
    return {
        "schema_version": "1",
        "entities": [
            {
                "key": "narrator",
                "entity_type": "person",
                "canonical_name": "余爷爷",
                "aliases": ["我"],
                "description": "讲述者",
                "is_narrator": True,
            },
            {
                "key": "grandmother",
                "entity_type": "person",
                "canonical_name": "外婆",
                "aliases": [],
                "description": "原文提到的外婆",
                "is_narrator": False,
            },
            {
                "key": "chengdu",
                "entity_type": "place",
                "canonical_name": "成都",
                "aliases": [],
                "description": "原文提到的地点",
                "is_narrator": False,
            },
        ],
        "claims": [
            {
                "key": "move",
                "claim_type": "event",
                "statement": "余爷爷在七二年前后搬到成都。",
                "subject_entity_key": "narrator",
                "predicate_code": "occurred_at",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": "chengdu",
                "time_text": "七二年前后",
                "time_start": "1972-01-01T00:00:00",
                "time_end": "1972-12-31T23:59:59",
                "time_precision": "year",
                "certainty": "approximate",
                "evidence_quotes": ["七二年前后，我搬到了成都"],
            },
            {
                "key": "relationship",
                "claim_type": "relationship",
                "statement": "外婆是余爷爷的外婆。",
                "subject_entity_key": "narrator",
                "predicate_code": "family_relationship",
                "object_entity_key": "grandmother",
                "object_text": None,
                "place_entity_key": None,
                "time_text": None,
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "certainty": "exact",
                "evidence_quotes": ["我和外婆一起生活"],
            },
            {
                "key": "fabricated",
                "claim_type": "event",
                "statement": "院子里有一棵梨树。",
                "subject_entity_key": "narrator",
                "predicate_code": "narrative_fact",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": None,
                "time_text": None,
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "certainty": "uncertain",
                "evidence_quotes": ["院子里有一棵梨树"],
            },
        ],
    }


def test_extraction_rejects_non_place_entity_as_place_reference():
    payload = _extraction_payload("七二年前后，我搬到了成都。我和外婆一起生活。")
    payload["entities"][2]["entity_type"] = "person"

    with pytest.raises(ValueError, match="place_entity_key must reference a place entity"):
        MemoirNarrativeExtraction.model_validate(payload)


async def _load_source(test_db, created: dict):
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == created["utterance_id"])
        .options(
            selectinload(MemoirVoiceUtterance.chat_message),
            selectinload(MemoirVoiceUtterance.audio_file),
        )
    )
    version = await test_db.get(MemoirTranscriptVersion, created["transcript_version_id"])
    project = await test_db.get(Project, created["session"]["project_id"])
    assert utterance and version and project
    return project, utterance, version


async def test_ingestion_requires_exact_evidence_and_is_idempotent(client, test_db):
    source_text = "七二年前后，我搬到了成都，那时我和外婆一起生活。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    project.metadata_json = {
        "memoir_profile": {"narrator_name": "余爷爷", "narrator_origin": "四川成都"}
    }
    fake_ai = FakeNarrativeAI([
        _extraction_payload(source_text),
        _extraction_payload(source_text),
    ])

    first = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=fake_ai,
    )
    second = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=fake_ai,
    )
    await test_db.commit()

    assert len(first.created_claim_ids) == 2
    assert first.skipped_claim_keys == ("fabricated",)
    assert second.created_claim_ids == ()
    assert len(second.reused_claim_ids) == 2
    assert second.created_evidence_ids == ()
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == 2
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaimEvidence)) == 2
    relationship = await test_db.scalar(
        select(MemoirClaim).where(MemoirClaim.claim_type == MemoirClaimType.RELATIONSHIP.value)
    )
    assert relationship is not None
    assert relationship.subject_entity_id
    assert relationship.object_entity_id
    move = await test_db.scalar(select(MemoirClaim).where(MemoirClaim.time_text == "七二年前后"))
    assert move is not None
    assert move.time_precision == "year"
    assert move.certainty == "approximate"
    evidence = await test_db.scalar(
        select(MemoirClaimEvidence).where(MemoirClaimEvidence.claim_id == move.id)
    )
    assert evidence is not None
    assert evidence.quote == "七二年前后，我搬到了成都"
    assert evidence.source_text_hash == sha256(source_text.encode("utf-8")).hexdigest()


async def test_ingestion_reports_resolved_completion_answer(client, test_db):
    source_text = "她叫小林，我们从小学三年级起就是朋友。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project.id,
        chapter_titles=["一生的朋友"],
    )
    items, _ = await completion_items.upsert_candidates(
        test_db,
        project_id=project.id,
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=chapters[0].id,
                gap_type=MemoirNarrativeGapType.RELATIONSHIP,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                target_slot="semantic:person_introduction:test-evidence",
                title="主要朋友没有明确身份",
                description="正文没有说明主要朋友是谁。",
                detected_by=MemoirNarrativeGapDetectedBy.AI,
                action_reason_signal=(
                    MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE
                ),
            )
        ],
    )
    utterance.completion_item_id = items[0].id

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI([_extraction_payload(source_text)]),
    )

    assert result.completion_item_id == items[0].id
    assert result.completion_item_status == "resolved"
    assert result.answered_completion_item_ids == (items[0].id,)


async def test_ingestion_adds_new_story_to_unmodified_system_chapter_plan(
    client,
    test_db,
):
    source_text = "七二年前后，我搬到了成都，那时我和外婆一起生活。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project.id,
        chapter_titles=["来到成都"],
        source_refs_by_chapter=[[memoir_source_ref(created)]],
    )
    extraction = _extraction_payload(source_text)
    extraction["stories"] = [
        {
            "key": "move_story",
            "title": "搬到成都",
            "story_type": "event",
            "summary": "七二年前后搬到成都。",
            "time_start": None,
            "time_end": None,
            "time_precision": "year",
            "primary_place_entity_key": "chengdu",
            "existing_story_id": None,
            "match_confidence": 0.0,
            "claims": [
                {
                    "claim_key": "move",
                    "narrative_role": "action",
                    "is_core": True,
                }
            ],
        }
    ]
    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI([extraction]),
    )

    assert len(result.created_story_ids) == 1
    context = await load_chapter_audit_context(
        test_db,
        project_id=project.id,
        chapter_id=chapters[0].id,
    )
    assert set(context.stories) == set(result.created_story_ids)
    assert set(context.claims).issubset(set(result.created_claim_ids))
    assert len(context.claims) == 1


async def test_delete_transcript_rejects_ingested_claim_evidence(client, test_db):
    source_text = "七二年前后，我搬到了成都，那时我和外婆一起生活。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    project.metadata_json = {
        "memoir_profile": {"narrator_name": "余爷爷", "narrator_origin": "四川成都"}
    }
    await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI([_extraction_payload(source_text)]),
    )
    await test_db.commit()

    response = await client.delete(
        f"/api/mobile/memoir/voice-transcripts/{utterance.id}"
    )

    assert response.status_code == 409, response.text
    assert "已用于回忆录证据" in response.json()["detail"]
    assert await test_db.get(MemoirVoiceUtterance, utterance.id) is not None
    assert await test_db.scalar(
        select(func.count())
        .select_from(MemoirClaimEvidence)
        .where(MemoirClaimEvidence.utterance_id == utterance.id)
    ) == 2


async def test_control_only_utterance_creates_no_claim_or_story(client, test_db):
    source_text = "我们接着聊那个朋友吧"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    fake_ai = FakeNarrativeAI([])

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=fake_ai,
    )

    assert fake_ai.calls == 0
    assert result.created_claim_ids == ()
    assert result.created_story_ids == ()
    assert result.extraction_diagnostics["control_only_source"] is True
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == 0
    assert is_narrative_control_only("继续说刚才那个话题。") is True
    assert is_narrative_control_only("我们接着聊那个朋友吧，我三年级转学时她来找我。") is False


async def test_incompletely_grouped_evidenced_claim_cluster_requests_one_repair(
    client,
    test_db,
):
    source_text = (
        "李阅是我的高中同学，现在也是朋友。"
        "每次回成都，我都会找她玩。"
        "我们认识二十多年了。"
    )
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    initial = {
        "schema_version": "1",
        "entities": [
            {
                "key": "narrator",
                "entity_type": "person",
                "canonical_name": "讲述者",
                "aliases": ["我"],
                "description": "讲述者本人",
                "is_narrator": True,
            },
            {
                "key": "li_yue",
                "entity_type": "person",
                "canonical_name": "李阅",
                "aliases": ["她"],
                "description": "讲述者的高中同学和朋友",
                "is_narrator": False,
            },
        ],
        "claims": [
            {
                "key": "friendship",
                "claim_type": "relationship",
                "statement": "李阅是讲述者的高中同学，现在也是朋友。",
                "subject_entity_key": "narrator",
                "predicate_code": "social_relationship",
                "object_entity_key": "li_yue",
                "object_text": None,
                "place_entity_key": None,
                "time_text": None,
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "certainty": "exact",
                "evidence_quotes": ["李阅是我的高中同学，现在也是朋友"],
            },
            {
                "key": "visits",
                "claim_type": "event",
                "statement": "讲述者每次回成都都会找李阅玩。",
                "subject_entity_key": "narrator",
                "predicate_code": "narrative_fact",
                "object_entity_key": "li_yue",
                "object_text": None,
                "place_entity_key": None,
                "time_text": None,
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "certainty": "exact",
                "evidence_quotes": ["每次回成都，我都会找她玩"],
            },
            {
                "key": "duration",
                "claim_type": "chronology",
                "statement": "讲述者和李阅认识二十多年了。",
                "subject_entity_key": "narrator",
                "predicate_code": "narrative_fact",
                "object_entity_key": "li_yue",
                "object_text": None,
                "place_entity_key": None,
                "time_text": "二十多年",
                "time_start": None,
                "time_end": None,
                "time_precision": "period",
                "certainty": "approximate",
                "evidence_quotes": ["我们认识二十多年了"],
            },
        ],
        "stories": [
            {
                "key": "partial_friendship",
                "title": "与李阅的友谊",
                "story_type": "relationship",
                "summary": "讲述者与高中同学李阅保持联系。",
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "primary_place_entity_key": None,
                "existing_story_id": None,
                "match_confidence": 0.0,
                "claims": [
                    {
                        "claim_key": "friendship",
                        "narrative_role": "background",
                        "is_core": True,
                    },
                    {
                        "claim_key": "visits",
                        "narrative_role": "action",
                        "is_core": True,
                    },
                ],
            }
        ],
    }
    repaired = {
        **initial,
        "stories": [
            {
                "key": "li_yue_friendship",
                "title": "与李阅二十多年的友谊",
                "story_type": "relationship",
                "summary": "讲述者与高中同学李阅保持了二十多年的友谊。",
                "time_start": None,
                "time_end": None,
                "time_precision": "period",
                "primary_place_entity_key": None,
                "existing_story_id": None,
                "match_confidence": 0.0,
                "claims": [
                    {
                        "claim_key": "friendship",
                        "narrative_role": "background",
                        "is_core": True,
                    },
                    {
                        "claim_key": "visits",
                        "narrative_role": "action",
                        "is_core": True,
                    },
                    {
                        "claim_key": "duration",
                        "narrative_role": "outcome",
                        "is_core": True,
                    },
                ],
            }
        ],
    }
    fake_ai = FakeNarrativeAI([initial, repaired])

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=fake_ai,
    )

    assert fake_ai.calls == 2
    assert len(result.created_claim_ids) == 3
    assert len(result.created_story_ids) == 1
    assert result.extraction_diagnostics["repair_attempted"] is True


async def test_real_provider_schema_errors_are_repaired_once(client, test_db):
    source_text = "八十年代初，我搬到了成都。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    invalid = {
        "schema_version": "1",
        "entities": [
            {
                "key": "narrator",
                "entity_type": "person",
                "canonical_name": "讲述者",
                "aliases": ["我"],
                "description": "讲述者本人",
                "is_narrator": True,
            }
        ],
        "claims": [
            {
                "key": "move",
                "claim_type": "event",
                "statement": "讲述者在八十年代初搬到成都。",
                "subject_entity_key": "narrator",
                "predicate_code": "occurred_at",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": None,
                "time_text": "八十年代初",
                "time_start": "1982-01-01",
                "time_end": "1982-12-31",
                "time_precision": "decade",
                "certainty": "approximate",
                "evidence_quotes": [source_text],
            }
        ],
        "stories": [
            {
                "key": "move_story",
                "title": "搬到成都",
                "story_type": "theme",
                "summary": "讲述者搬到成都。",
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "primary_place_entity_key": None,
                "existing_story_id": None,
                "match_confidence": 0.0,
                "claims": [
                    {
                        "claim_key": "move",
                        "narrative_role": "theme_definition",
                        "is_core": True,
                    }
                ],
            }
        ],
    }
    repaired = {
        **invalid,
        "claims": [
            {
                **invalid["claims"][0],
                "time_start": "1982-01-01T00:00:00",
                "time_end": "1982-12-31T23:59:59",
                "time_precision": "period",
            }
        ],
        "stories": [
            {
                **invalid["stories"][0],
                "claims": [
                    {
                        **invalid["stories"][0]["claims"][0],
                        "narrative_role": "background",
                    }
                ],
            }
        ],
    }
    fake_ai = FakeNarrativeAI([invalid, repaired])

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=fake_ai,
    )

    assert fake_ai.calls == 2
    assert len(result.created_claim_ids) == 1
    assert len(result.created_story_ids) == 1
    assert result.extraction_diagnostics["repair_attempted"] is True


async def test_invalid_ai_schema_after_repair_creates_no_narrative_state(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我小时候住在成都。")
    project, utterance, version = await _load_source(test_db, created)
    ambiguous = _extraction_payload("我小时候住在成都。")
    ambiguous["claims"] = [
        ambiguous["claims"][0],
        {**ambiguous["claims"][0]},
    ]
    fake_ai = FakeNarrativeAI(
        [
            "not-json",
            ambiguous,
        ]
    )

    with pytest.raises(ValueError, match="did not match schema"):
        await ingest_transcript_version(
            test_db,
            project=project,
            utterance=utterance,
            version=version,
            ai_client=fake_ai,
        )
    await test_db.rollback()

    assert fake_ai.calls == 2
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == 0
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaimEvidence)) == 0
    assert await test_db.scalar(select(func.count()).select_from(MemoirNarrativeEntity)) == 0


async def test_repair_retains_valid_items_without_coercing_invalid_semantics(
    client,
    test_db,
):
    source_text = "七二年前后，我搬到了成都，那时我和外婆一起生活。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    repaired = _extraction_payload(source_text)
    repaired["claims"].append(
        {
            "key": "invalid_relationship",
            "claim_type": "relationship",
            "statement": "关系不完整。",
            "subject_entity_key": "narrator",
            "predicate_code": "social_relationship",
            "object_entity_key": None,
            "object_text": None,
            "place_entity_key": None,
            "time_text": None,
            "time_start": None,
            "time_end": None,
            "time_precision": "unknown",
            "certainty": "uncertain",
            "evidence_quotes": ["我和外婆一起生活"],
        }
    )
    repaired["stories"] = [
        {
            "key": "move_story",
            "title": "搬到成都",
            "story_type": "event",
            "summary": "搬到成都。",
            "time_start": None,
            "time_end": None,
            "time_precision": "unknown",
            "primary_place_entity_key": "chengdu",
            "existing_story_id": None,
            "match_confidence": 0.0,
            "claims": [
                {
                    "claim_key": "move",
                    "narrative_role": "action",
                    "is_core": True,
                }
            ],
        }
    ]
    fake_ai = FakeNarrativeAI(
        [
            {"schema_version": "1", "claims": "not-a-list"},
            repaired,
        ]
    )

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=fake_ai,
    )

    assert len(result.created_claim_ids) == 2
    assert len(result.created_story_ids) == 1
    assert result.extraction_diagnostics == {
        "repair_attempted": True,
        "discarded_entity_count": 0,
        "discarded_claim_count": 1,
        "discarded_story_count": 0,
        "used_initial_response_after_repair": False,
    }


async def test_valid_initial_extraction_does_not_request_repair(
    client,
    test_db,
):
    source_text = "七二年前后，我搬到了成都，那时我和外婆一起生活。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    initial = _extraction_payload(source_text)
    initial["stories"] = [
        {
            "key": "move_story",
            "title": "搬到成都",
            "story_type": "event",
            "summary": "搬到成都。",
            "time_start": None,
            "time_end": None,
            "time_precision": "unknown",
            "primary_place_entity_key": "chengdu",
            "existing_story_id": None,
            "match_confidence": 0.0,
            "claims": [
                {
                    "claim_key": "move",
                    "narrative_role": "action",
                    "is_core": True,
                }
            ],
        }
    ]
    repaired = _extraction_payload(source_text)
    repaired["claims"] = repaired["claims"][:1]

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI([initial, repaired]),
    )

    assert len(result.created_claim_ids) == 2
    assert len(result.created_story_ids) == 1
    assert result.extraction_diagnostics["repair_attempted"] is False


async def test_repair_prefers_evidence_supported_claims_over_schema_item_count(
    client,
    test_db,
):
    source_text = "七二年前后，我搬到了成都。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    initial = _extraction_payload(source_text)
    initial["entities"].append({**initial["entities"][0]})
    unsupported = initial["claims"][2]
    initial["claims"] = [
        unsupported,
        {**unsupported, "key": "fabricated_again"},
    ]
    repaired = _extraction_payload(source_text)
    repaired["claims"] = repaired["claims"][:1]

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI([initial, repaired]),
    )

    assert len(result.created_claim_ids) == 1
    assert result.skipped_claim_keys == ()
    assert result.extraction_diagnostics[
        "used_initial_response_after_repair"
    ] is False


async def test_interviewer_fact_preserves_speaker_and_requires_review(client, test_db):
    source_text = "我记得他是在七二年搬到成都的。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    utterance.chat_message.role = MemoirChatMessageRole.INTERVIEWER.value
    payload = {
        "schema_version": "1",
        "entities": [],
        "claims": [
            {
                "key": "interviewer_fact",
                "claim_type": "event",
                "statement": "采访者记得讲述者在七二年搬到成都。",
                "subject_entity_key": None,
                "predicate_code": "narrative_fact",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": None,
                "time_text": "七二年",
                "time_start": None,
                "time_end": None,
                "time_precision": "year",
                "certainty": "uncertain",
                "evidence_quotes": [source_text],
            }
        ],
    }

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI([payload]),
    )
    await test_db.commit()

    claim = await test_db.get(MemoirClaim, result.created_claim_ids[0])
    evidence = await test_db.scalar(
        select(MemoirClaimEvidence).where(MemoirClaimEvidence.claim_id == claim.id)
    )
    assert claim.verification_status == MemoirClaimVerificationStatus.NEEDS_VERIFICATION.value
    assert evidence is not None
    assert evidence.speaker_role == "interviewer"
    assert evidence.speaker_entity_id is None


async def test_transcript_change_marks_unsupported_claim_for_review(client, test_db):
    source_text = "七二年前后，我搬到了成都，那时我和外婆一起生活。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, first_version = await _load_source(test_db, created)
    first = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=first_version,
        ai_client=FakeNarrativeAI([_extraction_payload(source_text)]),
    )
    old_claim_id = first.created_claim_ids[0]

    revised_text = "我后来搬到了重庆。"
    revised_version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.USER_EDITED.value,
        content=revised_text,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(revised_version)
    await test_db.flush()
    utterance.chat_message.transcript_version_id = revised_version.id
    utterance.chat_message.content_text = revised_text
    utterance.transcript_edited = revised_text
    await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=revised_version,
        ai_client=FakeNarrativeAI([{"schema_version": "1", "entities": [], "claims": []}]),
    )
    await test_db.commit()

    old_claim = await test_db.get(MemoirClaim, old_claim_id)
    assert old_claim is not None
    assert old_claim.verification_status == MemoirClaimVerificationStatus.NEEDS_VERIFICATION.value
    decision = await test_db.scalar(
        select(MemoirNarrativeDecision).where(MemoirNarrativeDecision.target_id == old_claim_id)
    )
    assert decision is not None
    assert decision.action == "needs_review"
    assert decision.before_state_json["verification_status"] == "unreviewed"


async def test_ingestion_job_skips_stale_transcript_without_calling_ai(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="最初的转写。")
    project, utterance, _version = await _load_source(test_db, created)
    job = await schedule_narrative_ingestion_job(
        test_db,
        project_id=project.id,
        utterance=utterance,
        source="test",
    )
    assert job is not None
    replacement = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.USER_EDITED.value,
        content="用户已经改过的转写。",
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(replacement)
    await test_db.flush()
    utterance.chat_message.transcript_version_id = replacement.id
    utterance.chat_message.content_text = replacement.content

    result = await run_narrative_ingestion_job(test_db, job)

    assert result.status.value == "skipped"
    assert result.error_message == "narrative ingestion source changed"
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == 0


async def test_ingestion_scheduler_retries_failures_but_keeps_success_idempotent(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我小时候住在成都。")
    project, utterance, _version = await _load_source(test_db, created)
    job = await schedule_narrative_ingestion_job(
        test_db,
        project_id=project.id,
        utterance=utterance,
        source="initial",
    )
    assert job is not None
    job.status = MemoirEditingJobStatus.FAILED.value
    job.attempts = 3
    job.error_message = "provider unavailable"
    await test_db.flush()

    retried = await schedule_narrative_ingestion_job(
        test_db,
        project_id=project.id,
        utterance=utterance,
        source="retry",
    )

    assert retried is job
    assert retried.status == MemoirEditingJobStatus.PENDING.value
    assert retried.attempts == 0
    assert retried.error_message == ""
    assert retried.payload_json["source"] == "retry"

    retried.status = MemoirEditingJobStatus.SUCCEEDED.value
    retried.result_json = {"created_claim_ids": ["claim-1"]}
    await test_db.flush()
    repeated = await schedule_narrative_ingestion_job(
        test_db,
        project_id=project.id,
        utterance=utterance,
        source="should-not-replace-success",
    )

    assert repeated is job
    assert repeated.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert repeated.payload_json["source"] == "retry"
    assert repeated.result_json == {"created_claim_ids": ["claim-1"]}


async def test_claim_confirmation_requires_evidence_and_decisions_are_append_only(client, test_db):
    project_id = await create_test_voice_transcript(client, test_db, text="测试素材")
    user = (await client.get("/api/auth/me")).json()
    project = await test_db.get(Project, project_id["session"]["project_id"])
    assert project is not None
    claim = await MemoirClaimRepository(test_db).create(
        MemoirClaimCreate(
            project_id=project.id,
            claim_type=MemoirClaimType.EVENT,
            statement="一条尚无证据的事实。",
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    project_id = project.id
    claim_id = claim.id
    await test_db.commit()

    no_evidence = await client.post(
        f"/api/memoir/projects/{project_id}/claims/{claim_id}/confirm",
        json={"idempotency_key": "confirm-without-evidence", "reason": "测试"},
    )
    assert no_evidence.status_code == 409

    await MemoirClaimRepository(test_db).add_evidence(
        MemoirClaimEvidenceCreate(
            project_id=project_id,
            claim_id=claim_id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
            evidence_source_type=MemoirClaimEvidenceSourceType.MANUAL,
            evidence_note="用户本人录入并确认。",
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    await test_db.commit()
    payload = {"idempotency_key": "confirm-with-evidence", "reason": "本人确认"}
    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/claims/{claim_id}/confirm",
        json=payload,
    )
    repeated = await client.post(
        f"/api/memoir/projects/{project_id}/claims/{claim_id}/confirm",
        json=payload,
    )
    assert confirmed.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["id"] == confirmed.json()["id"]

    with pytest.raises(IntegrityError, match="append-only"):
        await test_db.execute(
            text("UPDATE memoir_narrative_decisions SET reason = 'tampered' WHERE id = :id"),
            {"id": confirmed.json()["id"]},
        )
        await test_db.flush()
    await test_db.rollback()


async def test_entity_merge_keeps_historical_id_and_records_decision(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我提到了李主任。")
    user = (await client.get("/api/auth/me")).json()
    project_id = created["session"]["project_id"]
    repository = MemoirNarrativeEntityRepository(test_db)
    retained = await repository.create(
        MemoirNarrativeEntityCreate(
            project_id=project_id,
            entity_type=MemoirNarrativeEntityType.PERSON,
            canonical_name="李明",
            aliases=["老李"],
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    source = await repository.create(
        MemoirNarrativeEntityCreate(
            project_id=project_id,
            entity_type=MemoirNarrativeEntityType.PERSON,
            canonical_name="李主任",
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    retained_id = retained.id
    source_id = source.id
    await test_db.commit()

    response = await client.post(
        f"/api/memoir/projects/{project_id}/entities/{source_id}/merge",
        json={
            "retained_entity_id": retained_id,
            "idempotency_key": "merge-li-director",
            "reason": "用户确认是同一个人",
        },
    )
    assert response.status_code == 200
    assert response.json()["action"] == "merge"
    await test_db.refresh(source)
    await test_db.refresh(retained)
    assert source.merged_into_entity_id == retained_id
    assert "李主任" in retained.aliases_json


async def test_pruning_preserves_evidence_referenced_transcript(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="最早的人工版本。")
    project, utterance, pinned_version = await _load_source(test_db, created)
    user = (await client.get("/api/auth/me")).json()
    claim = await MemoirClaimRepository(test_db).create(
        MemoirClaimCreate(
            project_id=project.id,
            claim_type=MemoirClaimType.EVENT,
            statement="最早版本支持的事实。",
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    await MemoirClaimRepository(test_db).add_evidence(
        MemoirClaimEvidenceCreate(
            project_id=project.id,
            claim_id=claim.id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
            evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
            utterance_id=utterance.id,
            transcript_version_id=pinned_version.id,
            audio_file_id=utterance.audio_file_id,
            quote=pinned_version.content,
            source_text_hash=sha256(pinned_version.content.encode("utf-8")).hexdigest(),
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    for index in range(4):
        test_db.add(
            MemoirTranscriptVersion(
                voice_session_id=utterance.voice_session_id,
                source_artifact_id=utterance.id,
                version_type=pinned_version.version_type,
                content=f"后续版本 {index}",
                created_by=MemoirCreatedBy.USER.value,
            )
        )
        await test_db.flush()

    deleted = await prune_transcript_versions_for_utterance(
        test_db,
        utterance_id=utterance.id,
        version_type=pinned_version.version_type,
        keep=1,
    )
    await test_db.commit()

    remaining_ids = set(
        (
            await test_db.scalars(
                select(MemoirTranscriptVersion.id).where(
                    MemoirTranscriptVersion.source_artifact_id == utterance.id,
                    MemoirTranscriptVersion.version_type == pinned_version.version_type,
                )
            )
        ).all()
    )
    assert deleted == 3
    assert pinned_version.id in remaining_ids
    assert len(remaining_ids) == 2
