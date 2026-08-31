from __future__ import annotations

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


class FakeEmbeddingClient:
    model = "test-bge-small"

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str]) -> EmbeddingBatchResult:
        self.batches.append(texts)
        embeddings = tuple(
            _vector(0 if "华西医院" in text else 1) for text in texts
        )
        return EmbeddingBatchResult(
            requested_model=self.model,
            resolved_model=self.model,
            dimensions=512,
            normalized=True,
            embeddings=embeddings,
            service_elapsed_ms=2,
        )


class FailingEmbeddingClient:
    model = "test-bge-small"

    async def embed(self, texts: list[str]) -> EmbeddingBatchResult:
        raise RuntimeError("embedding unavailable")


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


async def test_memory_activation_injects_ranked_exact_excerpts_and_full_trace(
    client,
    test_db,
):
    current_text = "她后来是在华西医院做的手术，我想起当时她很害怕。"
    current = await create_test_voice_transcript(
        client,
        test_db,
        text=current_text,
    )
    unrelated_prefix = "那几年我一直在纺织厂上班，每天骑车往返。" * 18
    relevant = "李桂芳去华西医院做手术前很害怕，是姐姐陪她办的住院。"
    unrelated_suffix = "出院很久以后，家里又搬到了城南。" * 18
    historical_text = unrelated_prefix + "\n\n" + relevant + "\n" + unrelated_suffix
    historical = await create_test_voice_transcript(
        client,
        test_db,
        project_id=current["session"]["project_id"],
        text="原始 ASR 文本不会直接注入。",
    )
    await _add_entry(test_db, created=historical, text=historical_text)
    current_message, _utterance = await _message_and_utterance(test_db, current)
    embedding = FakeEmbeddingClient()

    result = await activate_memory_for_turn(
        test_db,
        project_id=current["session"]["project_id"],
        voice_session_id=current["session"]["id"],
        current_message=current_message,
        embedding_client=embedding,
    )

    assert result.status == "succeeded"
    assert result.injected_count == 1
    assert relevant in result.context
    assert "原始 ASR 文本不会直接注入" not in result.context
    assert "不相关时全部忽略" in result.context
    assert embedding.batches[0] == [current_text]

    trace = await test_db.get(MemoirMemoryActivationTrace, result.trace_id)
    assert trace is not None
    assert trace.candidate_count == 1
    assert trace.injected_count == 1
    assert trace.provider == "local_embedding"
    payload = trace.trace_json
    retrieval = payload["retrieval"]
    assert retrieval["query_text"] == current_text
    assert retrieval["vector_hits"]
    assert retrieval["bm25_hits"]
    assert "query_planning" not in payload
    assert "selection" not in payload
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
        embedding_client=FailingEmbeddingClient(),
    )
    assert cached.cached is True
    assert cached.context == result.context
    assert cached.injected_count == 1


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
        embedding_client=FailingEmbeddingClient(),
    )

    assert result.status == "skipped"
    response = await client.get(
        f"/api/mobile/memoir/projects/{current['session']['project_id']}"
        "/memory-activation-traces"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == result.trace_id
    assert body["items"][0]["injected_count"] == 0
    assert "trace_json" not in body["items"][0]
    detail = await client.get(
        f"/api/mobile/memoir/projects/{current['session']['project_id']}"
        f"/memory-activation-traces/{result.trace_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["trace_json"]["skip_reason"] == (
        "no_historical_retrieval_entries"
    )


async def test_embedding_failure_is_logged_without_injecting_context(
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
        embedding_client=FailingEmbeddingClient(),
    )

    assert result.status == "failed"
    assert result.context == ""
    trace = await test_db.get(MemoirMemoryActivationTrace, result.trace_id)
    assert trace is not None
    assert trace.candidate_count == 0
    assert trace.injected_count == 0
    assert trace.trace_json["failure"]["error_type"] == "RuntimeError"
    assert trace.trace_json["failure"]["stage"] == "hybrid_retrieval"
