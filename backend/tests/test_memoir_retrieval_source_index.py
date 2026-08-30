from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirEditingJob,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.models.memoir_retrieval import MemoirRetrievalEntry
from app.services.embeddings import EmbeddingBatchResult
from app.services.memoir.retrieval_source_index import (
    SOURCE_INDEX_VERSION,
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
        self.texts: list[str] = []

    async def embed(self, texts: list[str]) -> EmbeddingBatchResult:
        self.texts.extend(texts)
        embeddings = tuple(
            _vector(0 if "外婆" in value else 1)
            for value in texts
        )
        return EmbeddingBatchResult(
            requested_model="test-bge-small",
            resolved_model="test-bge-small",
            dimensions=512,
            normalized=True,
            embeddings=embeddings,
            service_elapsed_ms=3,
        )


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


async def _index_created_utterance(test_db, created: dict, fake: FakeEmbeddingClient):
    utterance = await _load_utterance(test_db, created["utterance_id"])
    job = await schedule_source_indexing_job(
        test_db,
        project_id=created["session"]["project_id"],
        utterance=utterance,
        source="test",
    )
    assert job is not None
    result = await run_source_indexing_job(test_db, job, fake)
    assert result.status == MemoirEditingJobStatus.SUCCEEDED
    await test_db.commit()
    return job, result


async def test_source_index_job_upserts_effective_transcript_and_evidence(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候住在外婆家的院子里。",
    )
    raw_utterance = await _load_utterance(test_db, created["utterance_id"])
    raw_utterance.transcript_edited = ""
    await test_db.flush()
    fake = FakeEmbeddingClient()

    first_job, first_result = await _index_created_utterance(test_db, created, fake)
    entry = await test_db.scalar(select(MemoirRetrievalEntry))

    assert entry is not None
    assert entry.project_id == created["session"]["project_id"]
    assert entry.voice_session_id == created["session"]["id"]
    assert entry.utterance_id == created["utterance_id"]
    assert entry.transcript_version_id == created["transcript_version_id"]
    assert entry.audio_file_id == created["audio_file_id"]
    assert entry.entry_kind == "source_text"
    assert entry.search_text == "我小时候住在外婆家的院子里。"
    assert entry.text_source == "raw"
    assert entry.embedding_model == "test-bge-small"
    assert entry.index_version == SOURCE_INDEX_VERSION
    assert list(entry.embedding) == list(_vector(0))
    assert "search_text" not in first_job.payload_json
    assert first_result.result_json["dimensions"] == 512

    utterance = await _load_utterance(test_db, created["utterance_id"])
    message = utterance.chat_message
    assert message is not None
    corrected = "后来我才想起来，那是奶奶家的院子。"
    version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.USER_EDITED.value,
        content=corrected,
        created_by="user",
    )
    test_db.add(version)
    await test_db.flush()
    utterance.transcript_edited = corrected
    message.content_text = corrected
    message.transcript_version_id = version.id
    second_job = await schedule_source_indexing_job(
        test_db,
        project_id=entry.project_id,
        utterance=utterance,
        source="user_edit",
    )
    assert second_job is not None
    assert second_job.id != first_job.id

    second_result = await run_source_indexing_job(test_db, second_job, fake)
    await test_db.commit()
    await test_db.refresh(entry)
    refreshed = entry

    assert second_result.status == MemoirEditingJobStatus.SUCCEEDED
    assert refreshed is not None
    assert refreshed.id == entry.id
    assert refreshed.search_text == corrected
    assert refreshed.transcript_version_id == version.id
    assert list(refreshed.embedding) == list(_vector(1))
    assert fake.texts == ["我小时候住在外婆家的院子里。", corrected]


async def test_stale_source_job_does_not_overwrite_newer_transcript(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="原来的讲述")
    utterance = await _load_utterance(test_db, created["utterance_id"])
    stale_job = await schedule_source_indexing_job(
        test_db,
        project_id=created["session"]["project_id"],
        utterance=utterance,
        source="test",
    )
    assert stale_job is not None

    utterance.transcript_edited = "已经修改的讲述"
    assert utterance.chat_message is not None
    utterance.chat_message.content_text = utterance.transcript_edited
    result = await run_source_indexing_job(test_db, stale_job, FakeEmbeddingClient())
    await test_db.commit()

    assert result.status == MemoirEditingJobStatus.SKIPPED
    assert result.error_message == "source text changed"
    assert await test_db.scalar(select(MemoirRetrievalEntry)) is None
    jobs = (
        await test_db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.job_type
                == MemoirEditingJobType.INDEX_RETRIEVAL_SOURCE.value
            )
        )
    ).all()
    assert len(jobs) == 2
    assert result.result_json["replacement_job_id"] in {job.id for job in jobs}


async def test_wrong_project_job_does_not_delete_existing_source_entry(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="原项目的讲述")
    fake = FakeEmbeddingClient()
    await _index_created_utterance(test_db, created, fake)
    existing = await test_db.scalar(select(MemoirRetrievalEntry))
    assert existing is not None

    other_project_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "另一个回忆录"},
    )
    assert other_project_response.status_code == 201
    other_project_id = other_project_response.json()["id"]
    utterance = await _load_utterance(test_db, created["utterance_id"])
    wrong_project_job = await schedule_source_indexing_job(
        test_db,
        project_id=other_project_id,
        utterance=utterance,
        source="test_wrong_project",
    )
    assert wrong_project_job is not None

    result = await run_source_indexing_job(test_db, wrong_project_job, fake)
    await test_db.commit()

    assert result.status == MemoirEditingJobStatus.SKIPPED
    assert result.error_message == "utterance unavailable"
    preserved = await test_db.get(MemoirRetrievalEntry, existing.id)
    assert preserved is not None
    assert preserved.project_id == created["session"]["project_id"]


async def test_source_entries_support_vector_and_chinese_bm25_queries(client, test_db):
    first = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常住在外婆家的院子里。",
    )
    project_id = first["session"]["project_id"]
    second = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="参加工作以后，我进了上海纺织厂。",
    )
    fake = FakeEmbeddingClient()
    await _index_created_utterance(test_db, first, fake)
    await _index_created_utterance(test_db, second, fake)

    vector_match = await test_db.scalar(
        select(MemoirRetrievalEntry)
        .where(MemoirRetrievalEntry.project_id == project_id)
        .order_by(MemoirRetrievalEntry.embedding.cosine_distance(list(_vector(0))))
        .limit(1)
    )
    bm25_match = await test_db.scalar(
        text(
            """
            SELECT utterance_id
            FROM memoir_retrieval_entries
            WHERE project_id = :project_id
              AND search_text ||| :query
            ORDER BY pdb.score(id) DESC
            LIMIT 1
            """
        ),
        {"project_id": project_id, "query": "上海纺织厂"},
    )

    assert vector_match is not None
    assert vector_match.utterance_id == first["utterance_id"]
    assert bm25_match == second["utterance_id"]
