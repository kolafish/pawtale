from __future__ import annotations

import json

from sqlalchemy import func, select

from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirGuidanceMode,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import MemoirEditingJob, MemoirSourceCard, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.services.memoir.editing_jobs import run_editing_job
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.source_cards import (
    SOURCE_CARD_ENGINE,
    generate_or_reuse_source_card,
    normalize_source_card_payload,
    schedule_source_card_generation_job,
    source_text_hash,
)


class FakeSourceCardAI:
    provider_name = "fake"
    model_name = "fake-source-card"

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
            "short_title": "外婆家的厨房",
            "summary": "回忆小时候在外婆家厨房帮忙。",
            "people": ["外婆", "我"],
            "places": ["外婆家厨房"],
            "time_hints": ["小时候"],
            "event_tags": ["做饭", "家庭生活"],
            "emotion_tags": ["怀念"],
            "key_facts": [
                {"text": "小时候常在外婆家厨房帮忙", "evidence_quote": "小时候常在外婆家厨房帮忙"}
            ],
            "open_questions": ["厨房是什么样子？"],
        }
        return json.dumps(payload, ensure_ascii=False)


async def _create_utterance(test_db, *, text: str) -> tuple[Project, MemoirVoiceUtterance]:
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
        transcript_raw=text,
        transcript_edited=text,
    )
    test_db.add(utterance)
    await test_db.flush()
    return project, utterance


async def test_source_card_generation_normalizes_schema_and_requires_evidence(test_db):
    source_text = "小时候常在外婆家厨房帮忙，我看见外婆用大铁锅做饭。"
    payload = {
        "short_title": "外婆家的厨房",
        "summary": "讲述者回忆小时候在外婆家厨房帮忙。",
        "people": ["外婆", "我"],
        "places": ["外婆家厨房"],
        "time_hints": ["小时候"],
        "event_tags": ["做饭", "家庭生活"],
        "emotion_tags": ["怀念"],
        "key_facts": [
            {"text": "真实事实", "evidence_quote": "小时候常在外婆家厨房帮忙"},
            {"text": "编造事实", "evidence_quote": "院子里有一棵梨树"},
        ],
        "open_questions": ["那口锅是什么样子？"],
    }
    project, utterance = await _create_utterance(test_db, text=source_text)
    fake_ai = FakeSourceCardAI([payload])

    card = await generate_or_reuse_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        ai_client=fake_ai,
    )
    await test_db.commit()

    assert card.status == MemoirSemanticCacheStatus.READY.value
    assert card.source_text_hash == source_text_hash(source_text)
    assert card.engine == SOURCE_CARD_ENGINE
    assert card.card_json["summary"] == "讲述者回忆小时候在外婆家厨房帮忙。"
    assert card.card_json["key_facts"] == [
        {"text": "真实事实", "evidence_quote": "小时候常在外婆家厨房帮忙"}
    ]
    assert card.card_json["generation"] == {
        "provider": "fake",
        "model": "fake-source-card",
        "engine": SOURCE_CARD_ENGINE,
    }


async def test_source_card_generation_reuses_ready_card_when_text_unchanged(test_db):
    project, utterance = await _create_utterance(test_db, text="小时候常在外婆家厨房帮忙。")
    fake_ai = FakeSourceCardAI()

    first = await generate_or_reuse_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        ai_client=fake_ai,
    )
    second = await generate_or_reuse_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        ai_client=fake_ai,
    )
    await test_db.commit()

    assert second.id == first.id
    assert len(fake_ai.calls) == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirSourceCard)) == 1


async def test_source_card_generation_marks_old_card_stale_when_text_changes(test_db):
    project, utterance = await _create_utterance(test_db, text="小时候常在外婆家厨房帮忙。")
    fake_ai = FakeSourceCardAI()
    first = await generate_or_reuse_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        ai_client=fake_ai,
    )

    utterance.transcript_edited = "小时候常在外婆家厨房帮忙，我还记得灶台旁边很暖。"
    test_db.add(utterance)
    second = await generate_or_reuse_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        ai_client=fake_ai,
    )
    await test_db.commit()

    assert second.id != first.id
    assert second.status == MemoirSemanticCacheStatus.READY.value
    refreshed_first = await test_db.get(MemoirSourceCard, first.id)
    assert refreshed_first is not None
    assert refreshed_first.status == MemoirSemanticCacheStatus.STALE.value
    assert await test_db.scalar(select(func.count()).select_from(MemoirSourceCard)) == 2


async def test_source_card_generation_records_failed_card_without_raising(test_db):
    project, utterance = await _create_utterance(test_db, text="小时候常在外婆家厨房帮忙。")
    ready_card = await generate_or_reuse_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        ai_client=FakeSourceCardAI(),
    )
    utterance.transcript_edited = "后来我又想起外婆会让我在旁边剥青豆。"
    test_db.add(utterance)

    failed_card = await generate_or_reuse_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        ai_client=FakeSourceCardAI(error=RuntimeError("provider down")),
    )
    await test_db.commit()

    assert failed_card.status == MemoirSemanticCacheStatus.FAILED.value
    assert "provider down" in failed_card.error_message
    refreshed_ready = await test_db.get(MemoirSourceCard, ready_card.id)
    assert refreshed_ready is not None
    assert refreshed_ready.status == MemoirSemanticCacheStatus.STALE.value


async def test_source_card_generation_job_schedules_deduped_work(test_db, monkeypatch):
    project, utterance = await _create_utterance(test_db, text="小时候常在外婆家厨房帮忙。")
    first_job = await schedule_source_card_generation_job(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        reason="new_source",
    )
    second_job = await schedule_source_card_generation_job(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        reason="transcript_changed",
    )
    await test_db.commit()

    assert second_job.id == first_job.id
    assert second_job.job_type == MemoirEditingJobType.GENERATE_SOURCE_CARD.value
    assert second_job.payload_json["reason"] == "transcript_changed"

    async def fake_generate_or_reuse_source_card(db, *, project_id, utterance_id, ai_model=None):
        return await generate_or_reuse_source_card(
            db,
            project_id=project_id,
            utterance_id=utterance_id,
            ai_client=FakeSourceCardAI(),
        )

    monkeypatch.setattr(
        "app.services.memoir.source_cards.generate_or_reuse_source_card",
        fake_generate_or_reuse_source_card,
    )
    register_memoir_editing_job_handlers()
    assert await run_editing_job(test_db, first_job.id)

    job = await test_db.get(MemoirEditingJob, first_job.id)
    assert job is not None
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.result_json["status"] == MemoirSemanticCacheStatus.READY.value
    assert await test_db.scalar(select(func.count()).select_from(MemoirSourceCard)) == 1


def test_source_card_payload_falls_back_to_real_quote_when_ai_omits_valid_evidence():
    source_text = "小时候常在外婆家厨房帮忙。"
    normalized = normalize_source_card_payload(
        {
            "summary": "厨房回忆",
            "key_facts": [{"text": "不在原文里的事实", "evidence_quote": "不存在的引用"}],
        },
        source_text=source_text,
    )

    assert normalized["key_facts"] == [
        {"text": source_text, "evidence_quote": source_text}
    ]
