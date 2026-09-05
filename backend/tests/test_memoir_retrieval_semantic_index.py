from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirRetrievalEntryKind,
)
from app.models.memoir import MemoirEditingJob, MemoirVoiceUtterance
from app.models.memoir_retrieval import MemoirRetrievalEntry
from app.services.embeddings import EmbeddingBatchResult
from app.services.memoir.editing_jobs import claim_editing_job
from app.services.memoir.retrieval_source_index import (
    SEMANTIC_INDEX_MODEL,
    SEMANTIC_INDEX_VERSION,
    run_semantic_indexing_job,
    run_source_indexing_job,
    schedule_source_indexing_job,
)
from tests.memoir_helpers import create_test_voice_transcript


def _vector(axis: int) -> tuple[float, ...]:
    values = [0.0] * 512
    values[axis] = 1.0
    return tuple(values)


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> EmbeddingBatchResult:
        self.calls.append(list(texts))
        return EmbeddingBatchResult(
            requested_model="test-bge-small",
            resolved_model="test-bge-small",
            dimensions=512,
            normalized=True,
            embeddings=tuple(_vector(index) for index, _ in enumerate(texts)),
            service_elapsed_ms=3,
        )


class FakeAIClient:
    provider_name = "fake-provider"
    model_name = "fake-index-model"

    def __init__(self, units: list[str], reason: str = "原文包含可检索信息") -> None:
        self.units = units
        self.reason = reason
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
        payload = {
            "units": self.units,
            "decision_reason": self.reason,
        }
        return json.dumps(payload, ensure_ascii=False), {
            "model": self.model_name,
            "choices": [{"finish_reason": "stop"}],
            "usage": {"total_tokens": 90},
        }


class FailingAIClient:
    provider_name = "fake-provider"
    model_name = "fake-index-model"

    async def _call_llm(self, *args, **kwargs):
        raise RuntimeError("simulated provider failure")


class FailingEmbeddingClient:
    model = "test-bge-small"
    timeout_seconds = 5.0

    async def embed(self, texts: list[str]) -> EmbeddingBatchResult:
        raise RuntimeError("simulated embedding failure")


async def _load_utterance(test_db, utterance_id: str) -> MemoirVoiceUtterance:
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(
            selectinload(MemoirVoiceUtterance.voice_session),
            selectinload(MemoirVoiceUtterance.chat_message),
        )
    )
    assert utterance is not None
    return utterance


async def _create_source_and_semantic_job(test_db, created: dict):
    utterance = await _load_utterance(test_db, created["utterance_id"])
    source_job = await schedule_source_indexing_job(
        test_db,
        project_id=created["session"]["project_id"],
        utterance=utterance,
        source="test",
    )
    assert source_job is not None
    source_result = await run_source_indexing_job(
        test_db,
        source_job,
        FakeEmbeddingClient(),
    )
    assert source_result.status is MemoirEditingJobStatus.SUCCEEDED
    semantic_job = await test_db.get(
        MemoirEditingJob,
        source_result.result_json["semantic_job_id"],
    )
    assert semantic_job is not None
    await claim_editing_job(test_db, semantic_job.id, now=semantic_job.run_after)
    await test_db.refresh(semantic_job)
    return semantic_job


async def test_source_index_schedules_separate_semantic_job(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候住在外婆家的院子里。",
    )

    semantic_job = await _create_source_and_semantic_job(test_db, created)
    entries = (await test_db.scalars(select(MemoirRetrievalEntry))).all()

    assert [entry.entry_kind for entry in entries] == [
        MemoirRetrievalEntryKind.SOURCE_TEXT.value
    ]
    assert semantic_job.job_type == MemoirEditingJobType.INDEX_RETRIEVAL_SEMANTICS.value
    assert semantic_job.payload_json["utterance_id"] == created["utterance_id"]
    assert "search_text" not in semantic_job.payload_json


async def test_semantic_provider_failure_does_not_rollback_source_index(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="即使语义提取失败，原文索引也必须保留。",
    )
    semantic_job = await _create_source_and_semantic_job(test_db, created)
    await test_db.commit()

    result = await run_semantic_indexing_job(
        test_db,
        semantic_job,
        embedding_client=FakeEmbeddingClient(),
        ai_client=FailingAIClient(),
    )
    await test_db.commit()

    entries = (await test_db.scalars(select(MemoirRetrievalEntry))).all()
    assert result.status is MemoirEditingJobStatus.FAILED
    assert result.error_message == "语义索引提取失败"
    assert result.result_json["stage"] == "generate_semantic_units"
    assert result.result_json["requested_model"] == SEMANTIC_INDEX_MODEL
    assert result.result_json["model"] == FailingAIClient.model_name
    assert result.result_json["provider"] == FailingAIClient.provider_name
    assert result.result_json["timeout_seconds"] > 0
    assert result.result_json["elapsed_ms"] >= 0
    assert result.result_json["error_type"] == "RuntimeError"
    assert len(entries) == 1
    assert entries[0].entry_kind == MemoirRetrievalEntryKind.SOURCE_TEXT.value
    assert entries[0].utterance_id == created["utterance_id"]


async def test_semantic_job_persists_multiple_units_with_source_evidence(
    client,
    test_db,
):
    source_text = "退休以后，我又去印刷厂帮了几年忙，也认识了师傅周明。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    semantic_job = await _create_source_and_semantic_job(test_db, created)
    ai = FakeAIClient(
        [
            "讲述者退休后到印刷厂帮忙",
            "讲述者在印刷厂认识师傅周明",
        ]
    )
    embeddings = FakeEmbeddingClient()

    result = await run_semantic_indexing_job(
        test_db,
        semantic_job,
        embedding_client=embeddings,
        ai_client=ai,
    )
    await test_db.commit()
    entries = (
        await test_db.scalars(
            select(MemoirRetrievalEntry)
            .where(
                MemoirRetrievalEntry.entry_kind
                == MemoirRetrievalEntryKind.SEMANTIC_UNIT.value
            )
            .order_by(MemoirRetrievalEntry.ordinal)
        )
    ).all()

    assert result.status is MemoirEditingJobStatus.SUCCEEDED
    assert [entry.search_text for entry in entries] == ai.units
    assert [entry.ordinal for entry in entries] == [0, 1]
    assert all(entry.utterance_id == created["utterance_id"] for entry in entries)
    assert all(
        entry.transcript_version_id == created["transcript_version_id"]
        for entry in entries
    )
    assert all(entry.audio_file_id == created["audio_file_id"] for entry in entries)
    assert all(entry.index_version == SEMANTIC_INDEX_VERSION for entry in entries)
    assert embeddings.calls == [ai.units]
    assert result.result_json["requested_model"] == SEMANTIC_INDEX_MODEL
    assert result.result_json["resolved_model"] == ai.model_name
    assert result.result_json["unit_count"] == 2
    assert result.result_json["generation_usage"] == {"total_tokens": 90}
    assert len(ai.calls) == 1
    assert "语义索引生成器" in ai.calls[0]["system_prompt"]
    assert f"source_text:\n{source_text}" in ai.calls[0]["user_prompt"]
    assert "context_anchor" not in ai.calls[0]["user_prompt"]
    assert ai.calls[0]["max_tokens"] == 2400


async def test_semantic_embedding_failure_keeps_source_and_records_diagnostics(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="语义生成成功后，本地向量服务暂时失败。",
    )
    semantic_job = await _create_source_and_semantic_job(test_db, created)
    await test_db.commit()

    result = await run_semantic_indexing_job(
        test_db,
        semantic_job,
        embedding_client=FailingEmbeddingClient(),
        ai_client=FakeAIClient(["本地向量服务暂时失败"]),
    )
    await test_db.commit()
    entries = (await test_db.scalars(select(MemoirRetrievalEntry))).all()

    assert result.status is MemoirEditingJobStatus.FAILED
    assert result.error_message == "语义索引向量化失败"
    assert result.result_json["stage"] == "embed_semantic_units"
    assert result.result_json["requested_model"] == FailingEmbeddingClient.model
    assert result.result_json["resolved_model"] is None
    assert result.result_json["provider"] == "local_embedding"
    assert result.result_json["timeout_seconds"] == 5.0
    assert result.result_json["elapsed_ms"] >= 0
    assert result.result_json["error_type"] == "RuntimeError"
    assert len(entries) == 1
    assert entries[0].entry_kind == MemoirRetrievalEntryKind.SOURCE_TEXT.value


async def test_empty_semantic_decision_removes_existing_units(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="这是一段可以建立语义入口的讲述。",
    )
    semantic_job = await _create_source_and_semantic_job(test_db, created)
    completed = await run_semantic_indexing_job(
        test_db,
        semantic_job,
        embedding_client=FakeEmbeddingClient(),
        ai_client=FakeAIClient(["已有语义入口"]),
    )
    semantic_job.result_json = completed.result_json
    await test_db.commit()
    no_embedding = FakeEmbeddingClient()

    result = await run_semantic_indexing_job(
        test_db,
        semantic_job,
        embedding_client=no_embedding,
        ai_client=FakeAIClient([], "没有独立检索价值"),
    )
    await test_db.commit()
    semantic_entries = (
        await test_db.scalars(
            select(MemoirRetrievalEntry).where(
                MemoirRetrievalEntry.entry_kind
                == MemoirRetrievalEntryKind.SEMANTIC_UNIT.value
            )
        )
    ).all()

    assert result.status is MemoirEditingJobStatus.SUCCEEDED
    assert result.result_json["unit_count"] == 0
    assert semantic_entries == []
    assert no_embedding.calls == []


async def test_source_change_invalidates_old_units_and_stale_job_cannot_restore_them(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db, text="原来的讲述")
    old_semantic_job = await _create_source_and_semantic_job(test_db, created)
    await run_semantic_indexing_job(
        test_db,
        old_semantic_job,
        embedding_client=FakeEmbeddingClient(),
        ai_client=FakeAIClient(["原来的语义入口"]),
    )
    utterance = await _load_utterance(test_db, created["utterance_id"])
    utterance.transcript_edited = "已经修改的讲述"
    assert utterance.chat_message is not None
    utterance.chat_message.content_text = utterance.transcript_edited
    new_source_job = await schedule_source_indexing_job(
        test_db,
        project_id=created["session"]["project_id"],
        utterance=utterance,
        source="user_edit",
    )
    assert new_source_job is not None

    await run_source_indexing_job(test_db, new_source_job, FakeEmbeddingClient())
    stale_result = await run_semantic_indexing_job(
        test_db,
        old_semantic_job,
        embedding_client=FakeEmbeddingClient(),
        ai_client=FakeAIClient(["不应写入"]),
    )
    await test_db.commit()
    entries = (await test_db.scalars(select(MemoirRetrievalEntry))).all()

    assert stale_result.status is MemoirEditingJobStatus.SKIPPED
    assert stale_result.error_message == "source text changed"
    assert [entry.entry_kind for entry in entries] == [
        MemoirRetrievalEntryKind.SOURCE_TEXT.value
    ]
    assert entries[0].search_text == "已经修改的讲述"
    assert stale_result.result_json["replacement_job_id"] is not None


async def test_embedding_retry_reuses_persisted_semantic_generation(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="小时候我们在成都的院子里生活。")
    job = await _create_source_and_semantic_job(test_db, created)
    result = await run_semantic_indexing_job(test_db, job, embedding_client=FailingEmbeddingClient(),
                                           ai_client=FakeAIClient(["小时候住在成都"]))
    assert result.status is MemoirEditingJobStatus.FAILED
    job.status = "failed"
    job.result_json = result.result_json
    await test_db.commit()
    from app.services.memoir.retrieval_source_index import schedule_semantic_indexing_job
    utterance = await _load_utterance(test_db, created["utterance_id"])
    automatic = await schedule_semantic_indexing_job(test_db, project_id=job.project_id,
        utterance=utterance, source="source_indexed")
    assert automatic.status == "failed"
    retry = await schedule_semantic_indexing_job(test_db, project_id=job.project_id, utterance=utterance,
                                                source="retry")
    await claim_editing_job(test_db, retry.id, now=retry.run_after)
    await test_db.refresh(retry)
    completed = await run_semantic_indexing_job(test_db, retry, embedding_client=FakeEmbeddingClient(),
                                               ai_client=FailingAIClient())
    assert completed.status is MemoirEditingJobStatus.SUCCEEDED
    assert completed.result_json["units"] == ["小时候住在成都"]
