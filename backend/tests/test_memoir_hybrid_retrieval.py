from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.memoir import MemoirVoiceUtterance
from app.models.memoir_retrieval import MemoirRetrievalEntry
from app.services.embeddings import EmbeddingBatchResult
from app.services.memoir.hybrid_retrieval import DEFAULT_CANDIDATE_LIMIT, retrieve_hybrid_candidates
from app.services.memoir.retrieval_source_index import (
    run_source_indexing_job,
    schedule_source_indexing_job,
)
from tests.memoir_helpers import create_test_voice_transcript


def _vector(axis: int) -> tuple[float, ...]:
    values = [0.0] * 512
    values[axis] = 1.0
    return tuple(values)


def test_default_hybrid_candidate_limit_is_six():
    assert DEFAULT_CANDIDATE_LIMIT == 6


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str]) -> EmbeddingBatchResult:
        self.batches.append(texts)
        embeddings = tuple(
            _vector(0 if "外婆" in text else 1)
            for text in texts
        )
        return EmbeddingBatchResult(
            requested_model="test-bge-small",
            resolved_model="test-bge-small",
            dimensions=512,
            normalized=True,
            embeddings=embeddings,
            service_elapsed_ms=2,
        )


async def _index_transcript(test_db, created: dict, client: FakeEmbeddingClient) -> None:
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == created["utterance_id"])
        .options(
            selectinload(MemoirVoiceUtterance.voice_session),
            selectinload(MemoirVoiceUtterance.chat_message),
        )
    )
    assert utterance is not None
    job = await schedule_source_indexing_job(
        test_db,
        project_id=created["session"]["project_id"],
        utterance=utterance,
        source="hybrid_retrieval_test",
    )
    assert job is not None
    await run_source_indexing_job(test_db, job, client)
    await test_db.commit()


async def test_hybrid_retrieval_uses_raw_narration_and_keeps_source_trace(
    client,
    test_db,
):
    current = await create_test_voice_transcript(
        client,
        test_db,
        text="我刚才正在讲外婆家的院子。",
    )
    project_id = current["session"]["project_id"]
    family = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="我小时候常住在外婆家的院子里。",
    )
    work = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="参加工作以后，我进了上海纺织厂。",
    )
    other_project_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "另一个回忆录"},
    )
    assert other_project_response.status_code == 201
    other = await create_test_voice_transcript(
        client,
        test_db,
        project_id=other_project_response.json()["id"],
        text="另一本回忆录也提到了外婆家的院子。",
    )

    index_client = FakeEmbeddingClient()
    for created in (current, family, work, other):
        await _index_transcript(test_db, created, index_client)

    family_entry = await test_db.scalar(
        select(MemoirRetrievalEntry).where(
            MemoirRetrievalEntry.utterance_id == family["utterance_id"]
        )
    )
    assert family_entry is not None
    family_entry.embedding_model = "retired-embedding-model"
    await test_db.commit()

    query_client = FakeEmbeddingClient()
    trace = await retrieve_hybrid_candidates(
        test_db,
        project_id=project_id,
        excluded_voice_session_id=current["session"]["id"],
        query_text="我想起外婆家的院子，也想起后来工作的上海纺织厂。",
        vector_limit=8,
        bm25_limit=8,
        candidate_limit=12,
        embedding_client=query_client,
    )

    assert query_client.batches == [
        ["我想起外婆家的院子，也想起后来工作的上海纺织厂。"]
    ]
    assert trace.requested_embedding_model == "test-bge-small"
    assert trace.embedding_dimensions == 512
    assert trace.query_text == "我想起外婆家的院子，也想起后来工作的上海纺织厂。"
    assert trace.retrieved_entry_count == 2
    assert trace.retrieved_source_count == 2
    assert {item.utterance_id for item in trace.candidates} == {
        family["utterance_id"],
        work["utterance_id"],
    }

    all_hits = [*trace.vector_hits, *trace.bm25_hits]
    assert current["utterance_id"] not in {hit.utterance_id for hit in all_hits}
    assert other["utterance_id"] not in {hit.utterance_id for hit in all_hits}
    assert any(
        hit.channel == "bm25"
        and hit.utterance_id == family["utterance_id"]
        for hit in all_hits
    )
    assert any(
        hit.channel == "bm25"
        and hit.utterance_id == work["utterance_id"]
        for hit in all_hits
    )
    candidates = {candidate.utterance_id: candidate for candidate in trace.candidates}
    family_candidate = candidates[family["utterance_id"]]
    work_candidate = candidates[work["utterance_id"]]
    assert {evidence.channel for evidence in family_candidate.evidence} == {"bm25"}
    assert {evidence.channel for evidence in work_candidate.evidence} == {
        "vector",
        "bm25",
    }
    for candidate in candidates.values():
        assert candidate.rank in {1, 2}
        assert candidate.fused_score > 0
        assert candidate.representative_hit.utterance_id == candidate.utterance_id
        assert candidate.representative_hit.index_version == 1


async def test_hybrid_retrieval_returns_empty_trace_without_query(test_db):
    trace = await retrieve_hybrid_candidates(
        test_db,
        project_id="unused-project",
        query_text="  ",
    )

    assert trace.query_text == ""
    assert trace.vector_hits == ()
    assert trace.bm25_hits == ()
    assert trace.candidates == ()
    assert trace.requested_embedding_model is None
    assert trace.retrieved_entry_count == 0


async def test_channel_limits_apply_to_distinct_source_utterances(client, test_db):
    first = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候住在外婆家的院子里。",
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        project_id=first["session"]["project_id"],
        text="后来我们搬到了另一座城市。",
    )
    index_client = FakeEmbeddingClient()
    await _index_transcript(test_db, first, index_client)
    await _index_transcript(test_db, second, index_client)
    first_entry = await test_db.scalar(
        select(MemoirRetrievalEntry).where(
            MemoirRetrievalEntry.utterance_id == first["utterance_id"]
        )
    )
    second_entry = await test_db.scalar(
        select(MemoirRetrievalEntry).where(
            MemoirRetrievalEntry.utterance_id == second["utterance_id"]
        )
    )
    assert first_entry is not None
    assert second_entry is not None
    second_entry.embedding = list(_vector(1))
    for ordinal in range(2):
        test_db.add(
            MemoirRetrievalEntry(
                project_id=first_entry.project_id,
                voice_session_id=first_entry.voice_session_id,
                utterance_id=first_entry.utterance_id,
                transcript_version_id=first_entry.transcript_version_id,
                audio_file_id=first_entry.audio_file_id,
                entry_kind="semantic_unit",
                ordinal=ordinal,
                search_text=f"外婆家的院子语义入口{ordinal}",
                text_source=first_entry.text_source,
                source_text_hash=first_entry.source_text_hash,
                embedding=list(_vector(0)),
                embedding_model=first_entry.embedding_model,
                index_version=1,
            )
        )
    await test_db.commit()

    trace = await retrieve_hybrid_candidates(
        test_db,
        project_id=first["session"]["project_id"],
        query_text="外婆家的院子",
        vector_limit=2,
        bm25_limit=2,
        candidate_limit=2,
        embedding_client=FakeEmbeddingClient(),
    )

    assert [hit.utterance_id for hit in trace.vector_hits] == [
        first["utterance_id"],
        second["utterance_id"],
    ]
