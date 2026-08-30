from __future__ import annotations

import json

from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance
from app.models.memoir_retrieval import (
    MemoirMemoryActivationTrace,
    MemoirRetrievalEntry,
)
from app.services.embeddings import EmbeddingBatchResult
from app.services.memoir.memory_activation import activate_memory_for_turn
from app.services.memoir.transcript_projection import source_text_hash
from tests.memoir_helpers import create_test_voice_transcript


def _vector(axis: int) -> tuple[float, ...]:
    values = [0.0] * 512
    values[axis] = 1.0
    return tuple(values)


class FakeAIClient:
    provider_name = "fake-provider"
    model_name = "fake-flash"

    def __init__(self, payload: dict | None = None, *, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    async def _call_llm(
        self,
        system_prompt,
        user_prompt,
        temperature=0.7,
        max_tokens=4000,
        timeout=None,
        return_raw=False,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "return_raw": return_raw,
            }
        )
        if self.error is not None:
            raise self.error
        return json.dumps(self.payload, ensure_ascii=False), {
            "model": self.model_name,
            "choices": [{"finish_reason": "stop"}],
            "usage": {"total_tokens": 120},
        }


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str]) -> EmbeddingBatchResult:
        self.batches.append(texts)
        embeddings = tuple(_vector(0 if "华西医院" in text else 1) for text in texts)
        return EmbeddingBatchResult(
            requested_model="test-bge-small",
            resolved_model="test-bge-small",
            dimensions=512,
            normalized=True,
            embeddings=embeddings,
            service_elapsed_ms=2,
        )


async def _message_and_utterance(test_db, created: dict):
    message = await test_db.get(MemoirChatMessage, created["message_id"])
    utterance = await test_db.get(MemoirVoiceUtterance, created["utterance_id"])
    assert message is not None
    assert utterance is not None
    return message, utterance


async def _add_entry(test_db, *, created: dict, text: str) -> None:
    _message, utterance = await _message_and_utterance(test_db, created)
    utterance.transcript_edited = text
    test_db.add(
        MemoirRetrievalEntry(
            project_id=created["session"]["project_id"],
            voice_session_id=created["session"]["id"],
            utterance_id=created["utterance_id"],
            transcript_version_id=None,
            audio_file_id=utterance.audio_file_id,
            entry_kind="semantic_unit",
            ordinal=0,
            search_text="李桂芳在华西医院接受手术",
            text_source="edited",
            source_text_hash=source_text_hash(text),
            embedding=list(_vector(0)),
            embedding_model="test-bge-small",
            index_version=1,
        )
    )
    await test_db.commit()


async def test_memory_activation_selects_exact_source_excerpt_and_persists_full_trace(
    client,
    test_db,
):
    current = await create_test_voice_transcript(
        client,
        test_db,
        text="她后来是在华西医院做的手术，我想起当时她很害怕。",
    )
    unrelated_prefix = "那几年我一直在纺织厂上班，每天骑车往返。" * 18
    relevant = "李桂芳去华西医院做手术前很害怕，是姐姐陪她办的住院。"
    unrelated_suffix = "出院很久以后，家里又搬到了城南。" * 18
    historical_text = unrelated_prefix + relevant + unrelated_suffix
    historical = await create_test_voice_transcript(
        client,
        test_db,
        project_id=current["session"]["project_id"],
        text="原始 ASR 文本不会直接注入。",
    )
    await _add_entry(test_db, created=historical, text=historical_text)
    current_message, _utterance = await _message_and_utterance(test_db, current)
    query_ai = FakeAIClient(
        {
            "purpose": "query",
            "units": ["李桂芳在华西医院接受手术"],
            "decision_reason": "当前讲述包含明确人物和治疗事件。",
        }
    )
    selection_ai = FakeAIClient(
        {
            "selected": [
                {
                    "utterance_id": historical["utterance_id"],
                    "reason": "可承接手术前的情绪和陪同人。",
                }
            ],
            "decision_reason": "该历史讲述直接补充当前事件。",
        }
    )
    embedding = FakeEmbeddingClient()

    result = await activate_memory_for_turn(
        test_db,
        project_id=current["session"]["project_id"],
        voice_session_id=current["session"]["id"],
        current_message=current_message,
        query_ai_client=query_ai,
        selection_ai_client=selection_ai,
        embedding_client=embedding,
    )

    assert result.status == "succeeded"
    assert result.selected_count == 1
    assert relevant in result.context
    assert "原始 ASR 文本不会直接注入" not in result.context
    assert "没有固定选择数量" in selection_ai.calls[0]["system_prompt"]
    assert "source_excerpt" in selection_ai.calls[0]["user_prompt"]

    trace = await test_db.get(MemoirMemoryActivationTrace, result.trace_id)
    assert trace is not None
    assert trace.query_count == 2
    assert trace.candidate_count == 1
    assert trace.selected_count == 1
    payload = trace.trace_json
    hybrid = payload["query_planning"]["hybrid_retrieval"]
    assert len(hybrid["queries"]) == 2
    assert hybrid["queries"][0]["vector_hits"]
    assert hybrid["queries"][0]["bm25_hits"]
    assert payload["selection"]["selected"][0]["utterance_id"] == historical["utterance_id"]
    assert payload["selection"]["rejected_utterance_ids"] == []
    package = payload["activation"]["packages"][0]
    assert package["source_excerpt"] in historical_text
    assert package["matched_index_texts"] == ["李桂芳在华西医院接受手术"]
    assert package["voice_session_id"] == historical["session"]["id"]
    assert payload["activation"]["injected_context"] == result.context

    cached = await activate_memory_for_turn(
        test_db,
        project_id=current["session"]["project_id"],
        voice_session_id=current["session"]["id"],
        current_message=current_message,
        query_ai_client=FakeAIClient(error=AssertionError("query should be cached")),
        selection_ai_client=FakeAIClient(error=AssertionError("selection should be cached")),
        embedding_client=embedding,
    )
    assert cached.cached is True
    assert cached.context == result.context
    assert cached.selected_count == 1


async def test_memory_activation_skips_without_historical_index_and_exposes_trace_api(
    client,
    test_db,
):
    current = await create_test_voice_transcript(
        client,
        test_db,
        text="先暂停一下。",
    )
    current_message, _utterance = await _message_and_utterance(test_db, current)

    result = await activate_memory_for_turn(
        test_db,
        project_id=current["session"]["project_id"],
        voice_session_id=current["session"]["id"],
        current_message=current_message,
        query_ai_client=FakeAIClient(error=AssertionError("query must not run")),
    )

    assert result.status == "skipped"
    response = await client.get(
        f"/api/mobile/memoir/projects/{current['session']['project_id']}/memory-activation-traces"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == result.trace_id
    assert "trace_json" not in body["items"][0]
    detail = await client.get(
        f"/api/mobile/memoir/projects/{current['session']['project_id']}/memory-activation-traces/{result.trace_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["trace_json"]["skip_reason"] == ("no_historical_retrieval_entries")


async def test_selection_failure_keeps_retrieval_trace_and_does_not_inject_context(
    client,
    test_db,
):
    current = await create_test_voice_transcript(
        client,
        test_db,
        text="她后来是在华西医院做的手术。",
    )
    historical = await create_test_voice_transcript(
        client,
        test_db,
        project_id=current["session"]["project_id"],
        text="李桂芳去华西医院做过手术。",
    )
    await _add_entry(
        test_db,
        created=historical,
        text="李桂芳去华西医院做过手术。",
    )
    current_message, _utterance = await _message_and_utterance(test_db, current)
    result = await activate_memory_for_turn(
        test_db,
        project_id=current["session"]["project_id"],
        voice_session_id=current["session"]["id"],
        current_message=current_message,
        query_ai_client=FakeAIClient(
            {
                "purpose": "query",
                "units": ["李桂芳在华西医院接受手术"],
                "decision_reason": "需要召回手术经历。",
            }
        ),
        selection_ai_client=FakeAIClient(error=RuntimeError("selector unavailable")),
        embedding_client=FakeEmbeddingClient(),
    )

    assert result.status == "failed"
    assert result.context == ""
    trace = await test_db.get(MemoirMemoryActivationTrace, result.trace_id)
    assert trace is not None
    assert trace.should_retrieve is True
    assert trace.query_count == 2
    assert trace.candidate_count == 1
    assert trace.trace_json["query_planning"]["hybrid_retrieval"]["queries"]
    assert trace.trace_json["candidate_preparation"]["candidates"]
    assert trace.trace_json["failure"]["error_type"] == "RuntimeError"
