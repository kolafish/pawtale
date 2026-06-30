from __future__ import annotations

import json

from sqlalchemy import func, select

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirGuidanceMode,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterCard,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.services.memoir.chapter_cards import (
    CHAPTER_CARD_ENGINE,
    build_chapter_card_context,
    generate_or_reuse_chapter_card,
    normalize_chapter_card_payload,
)
from app.services.memoir.semantic_state import upsert_source_card
from app.services.memoir.source_cards import SOURCE_CARD_ENGINE, source_text_hash


class FakeChapterCardAI:
    provider_name = "fake"
    model_name = "fake-chapter-card"

    def __init__(self, responses: list[dict] | None = None, *, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        return_raw: bool = False,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        if self.error:
            raise self.error
        payload = self.responses.pop(0) if self.responses else {
            "summary": "本章围绕外婆家的童年生活，已有厨房片段。",
            "covered": {
                "time": ["小时候"],
                "places": ["外婆家厨房"],
                "people": ["外婆", "我"],
                "events": ["做饭"],
            },
            "gaps": [
                {"type": "event_sequence", "severity": "medium", "message": "事件前后经过还不连贯"}
            ],
            "next_questions": ["那时厨房是什么样子？"],
            "evidence_refs": [
                {"utterance_id": "placeholder", "quote": "小时候常在外婆家厨房帮忙"}
            ],
        }
        return json.dumps(payload, ensure_ascii=False)


async def _create_chapter_setup(
    test_db,
    *,
    source_text: str = "小时候常在外婆家厨房帮忙，我看见外婆用大铁锅做饭。",
    with_source_card: bool = True,
):
    project = Project(title="回忆录项目", type="memoir")
    test_db.add(project)
    await test_db.flush()
    voice_session = MemoirVoiceSession(
        project_id=project.id,
        status="created",
        guidance_mode=MemoirGuidanceMode.FREE_NARRATION.value,
    )
    test_db.add(voice_session)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        transcript_raw=source_text,
        transcript_edited=source_text,
    )
    test_db.add(utterance)
    await test_db.flush()
    plan = MemoirBookPlan(
        project_id=project.id,
        title="童年",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        project_id=project.id,
        plan_id=plan.id,
        title="外婆家的厨房",
        sort_order=0,
        source_refs_json=[
            {
                "voice_session_id": voice_session.id,
                "message_id": "message-1",
                "utterance_id": utterance.id,
                "transcript_version_id": None,
                "audio_file_id": None,
                "start_ms": None,
                "end_ms": None,
                "quote": "小时候常在外婆家厨房帮忙",
                "created_at": utterance.created_at.isoformat(),
            }
        ],
    )
    test_db.add(chapter)
    await test_db.flush()
    draft = MemoirBookChapterDraft(
        project_id=project.id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    draft_version = MemoirBookChapterDraftVersion(
        project_id=project.id,
        chapter_id=chapter.id,
        draft_id=draft.id,
        version=1,
        content="我记得外婆家的厨房，总是有一口热气腾腾的大铁锅。",
        created_by=MemoirCreatedBy.AI.value,
    )
    test_db.add(draft_version)
    await test_db.flush()
    source_card = None
    if with_source_card:
        source_card = await upsert_source_card(
            test_db,
            project_id=project.id,
            utterance_id=utterance.id,
            source_text_hash=source_text_hash(source_text),
            engine=SOURCE_CARD_ENGINE,
            card_json={
                "summary": "讲述者回忆小时候在外婆家厨房帮忙。",
                "people": ["外婆", "我"],
                "places": ["外婆家厨房"],
                "key_facts": [
                    {"text": "小时候常在厨房帮忙", "evidence_quote": "小时候常在外婆家厨房帮忙"}
                ],
            },
        )
    await test_db.flush()
    return project, chapter, utterance, draft, draft_version, source_card


async def test_chapter_card_generation_uses_source_cards_and_records_signature(test_db):
    project, chapter, utterance, _, draft_version, source_card = await _create_chapter_setup(test_db)
    assert source_card is not None
    fake_ai = FakeChapterCardAI([
        {
            "summary": "本章围绕外婆家的厨房记忆。",
            "covered": {
                "time": ["小时候"],
                "places": ["外婆家厨房"],
                "people": ["外婆", "我"],
                "events": ["做饭"],
            },
            "gaps": [
                {"type": "sensory_detail", "severity": "low", "message": "厨房气味和声音还可以补充"}
            ],
            "next_questions": ["那口大铁锅是什么样子？"],
            "evidence_refs": [
                {"utterance_id": utterance.id, "quote": "小时候常在外婆家厨房帮忙"}
            ],
        }
    ])

    card = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=fake_ai,
    )
    await test_db.commit()

    assert card.status == MemoirSemanticCacheStatus.READY.value
    assert card.engine == CHAPTER_CARD_ENGINE
    assert card.draft_version_id == draft_version.id
    assert card.card_json["summary"] == "本章围绕外婆家的厨房记忆。"
    assert card.card_json["source_card_ids"] == [source_card.id]
    assert card.card_json["evidence_refs"] == [
        {"utterance_id": utterance.id, "quote": "小时候常在外婆家厨房帮忙"}
    ]
    assert card.card_json["generation"]["provider"] == "fake"
    assert card.card_json["input_signature"] == card.input_signature
    assert card.card_json["signature_payload"]["chapter_title"] == "外婆家的厨房"


async def test_chapter_card_generation_falls_back_to_source_text_without_source_card(test_db):
    project, chapter, utterance, _, _, _ = await _create_chapter_setup(test_db, with_source_card=False)
    fake_ai = FakeChapterCardAI([
        {
            "summary": "本章已有外婆厨房素材。",
            "covered": {"time": ["小时候"], "places": ["外婆家厨房"], "people": ["外婆"], "events": ["做饭"]},
            "gaps": [],
            "next_questions": [],
            "evidence_refs": [
                {"utterance_id": utterance.id, "quote": "小时候常在外婆家厨房帮忙"}
            ],
        }
    ])

    context = await build_chapter_card_context(test_db, project_id=project.id, chapter_id=chapter.id)
    assert context.sources[0].source_card_id is None
    assert "外婆家厨房" in context.sources[0].source_card_summary

    card = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=fake_ai,
    )
    await test_db.commit()

    assert card.status == MemoirSemanticCacheStatus.READY.value
    assert card.card_json["source_card_ids"] == []
    assert card.card_json["evidence_refs"][0]["utterance_id"] == utterance.id


async def test_chapter_card_generation_reuses_ready_card_when_signature_unchanged(test_db):
    project, chapter, _, _, _, _ = await _create_chapter_setup(test_db)
    fake_ai = FakeChapterCardAI()

    first = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=fake_ai,
    )
    second = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=fake_ai,
    )
    await test_db.commit()

    assert second.id == first.id
    assert len(fake_ai.calls) == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirChapterCard)) == 1


async def test_chapter_card_generation_marks_old_card_stale_when_title_or_draft_changes(test_db):
    project, chapter, _, draft, _, _ = await _create_chapter_setup(test_db)
    first = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=FakeChapterCardAI(),
    )

    chapter.title = "外婆厨房里的童年"
    test_db.add(chapter)
    test_db.add(
        MemoirBookChapterDraftVersion(
            project_id=project.id,
            chapter_id=chapter.id,
            draft_id=draft.id,
            version=2,
            content="我记得外婆厨房里的童年，也记得灶台旁的热气。",
            created_by=MemoirCreatedBy.AI.value,
        )
    )
    await test_db.flush()
    second = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=FakeChapterCardAI(),
    )
    await test_db.commit()

    assert second.id != first.id
    assert second.status == MemoirSemanticCacheStatus.READY.value
    refreshed_first = await test_db.get(MemoirChapterCard, first.id)
    assert refreshed_first is not None
    assert refreshed_first.status == MemoirSemanticCacheStatus.STALE.value
    assert second.card_json["signature_payload"]["chapter_title"] == "外婆厨房里的童年"


async def test_chapter_card_signature_changes_when_source_card_content_changes(test_db):
    project, chapter, utterance, _, _, source_card = await _create_chapter_setup(test_db)
    assert source_card is not None
    first = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=FakeChapterCardAI(),
    )

    await upsert_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        source_text_hash=source_card.source_text_hash,
        engine=SOURCE_CARD_ENGINE,
        card_json={"summary": "更新后的素材摘要", "people": ["外婆"]},
    )
    second = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=FakeChapterCardAI(),
    )
    await test_db.commit()

    assert second.id != first.id
    refreshed_first = await test_db.get(MemoirChapterCard, first.id)
    assert refreshed_first is not None
    assert refreshed_first.status == MemoirSemanticCacheStatus.STALE.value
    assert (
        second.card_json["signature_payload"]["source_refs"][0]["source_card_id"]
        == first.card_json["signature_payload"]["source_refs"][0]["source_card_id"]
    )
    assert (
        second.card_json["signature_payload"]["source_refs"][0]["source_card_signature"]
        != first.card_json["signature_payload"]["source_refs"][0]["source_card_signature"]
    )


async def test_chapter_card_generation_records_failed_card_without_raising(test_db):
    project, chapter, _, _, _, _ = await _create_chapter_setup(test_db)

    failed = await generate_or_reuse_chapter_card(
        test_db,
        project_id=project.id,
        chapter_id=chapter.id,
        ai_client=FakeChapterCardAI(error=RuntimeError("provider down")),
    )
    await test_db.commit()

    assert failed.status == MemoirSemanticCacheStatus.FAILED.value
    assert "provider down" in failed.error_message
    assert failed.card_json["signature_payload"]["chapter_id"] == chapter.id


def test_chapter_card_payload_keeps_high_medium_low_gaps_and_filters_bad_evidence():
    source_context = type(
        "Context",
        (),
        {
            "chapter": type("Chapter", (), {"id": "chapter-1", "title": "外婆家的厨房"})(),
            "draft_version": None,
            "sources": [
                type(
                    "Source",
                    (),
                    {
                        "utterance_id": "utterance-1",
                        "quote": "小时候常在外婆家厨房帮忙",
                        "source_text": "小时候常在外婆家厨房帮忙。",
                        "source_card_summary": "厨房帮忙",
                        "source_card_id": "source-card-1",
                    },
                )()
            ],
            "input_signature": "signature-1",
        },
    )()

    payload = normalize_chapter_card_payload(
        {
            "summary": "章节摘要",
            "covered": {"time": ["小时候"], "places": ["外婆家"]},
            "gaps": [
                {"type": "timeline", "severity": "high", "message": "时间顺序不清"},
                {"type": "detail", "severity": "medium", "message": "细节不足"},
                {"type": "voice", "severity": "low", "message": "口吻还可以更自然"},
            ],
            "next_questions": ["后来发生了什么？"],
            "evidence_refs": [
                {"utterance_id": "utterance-1", "quote": "不存在的引用"},
                {"utterance_id": "utterance-1", "quote": "小时候常在外婆家厨房帮忙"},
            ],
        },
        context=source_context,
    )

    assert [gap["severity"] for gap in payload["gaps"]] == ["high", "medium", "low"]
    assert payload["evidence_refs"] == [
        {"utterance_id": "utterance-1", "quote": "小时候常在外婆家厨房帮忙"}
    ]
