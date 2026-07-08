from sqlalchemy import select

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirChatMessage, MemoirEditingJob, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.models.projects import Project
from app.services.memoir.utterance_editing_pipeline import ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY
from tests.memoir_helpers import create_test_voice_transcript


class CapturingCleanupAIClient:
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.prompts.append(user_prompt)
        return "我小时候跟小叔去赶场。"


async def test_memoir_transcript_cleanup_preview_uses_saved_dialect_glossary(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="嗯，我小时候就是跟幺爸去赶场。",
    )
    project_id = created["session"]["project_id"]

    glossary_response = await client.put(
        f"/api/memoir/projects/{project_id}/transcript-cleanup/dialect-glossary",
        json={
            "entries": [
                {"source": "幺爸", "target": "小叔", "note": "四川话亲属称呼"},
                {"source": "幺爸", "target": "小叔", "note": "重复项会被折叠"},
            ]
        },
    )
    assert glossary_response.status_code == 200
    assert glossary_response.json()["entries"] == [
        {"source": "幺爸", "target": "小叔", "note": "四川话亲属称呼"}
    ]

    project = await test_db.scalar(select(Project).where(Project.id == project_id))
    assert project.metadata_json["memoir_dialect_glossary_entries"] == [
        {"source": "幺爸", "target": "小叔", "note": "四川话亲属称呼"}
    ]

    ai_client = CapturingCleanupAIClient()
    monkeypatch.setattr(
        "app.services.memoir.voice_transcripts.get_ai_service",
        lambda model=None: ai_client,
    )
    preview_response = await client.post(
        f"/api/memoir/projects/{project_id}/transcript-cleanup/preview",
        json={"utterance_ids": [created["utterance_id"]], "ai_model": "deepseek"},
    )

    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["glossary_entries"] == glossary_response.json()["entries"]
    assert preview["items"][0]["utterance_id"] == created["utterance_id"]
    assert preview["items"][0]["source_text"] == "嗯，我小时候就是跟幺爸去赶场。"
    assert preview["items"][0]["cleaned_text"] == "我小时候跟小叔去赶场。"
    assert preview["items"][0]["changed"] is True
    assert "幺爸 => 小叔（四川话亲属称呼）" in ai_client.prompts[0]

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    assert utterance.transcript_edited == "嗯，我小时候就是跟幺爸去赶场。"


async def test_memoir_transcript_cleanup_confirm_writes_corrected_versions_and_jobs(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]

    response = await client.post(
        f"/api/memoir/projects/{project_id}/transcript-cleanup/confirm",
        json={
            "ai_model": "deepseek",
            "items": [
                {
                    "utterance_id": created["utterance_id"],
                    "cleaned_text": "我小时候住在外婆家。",
                    "provider": "fake-ai",
                    "created_by": "ai",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["updated_count"] == 1
    assert response.json()["items"][0]["transcript_edited"] == "我小时候住在外婆家。"

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    version = await test_db.scalar(
        select(MemoirTranscriptVersion).where(MemoirTranscriptVersion.id == message.transcript_version_id)
    )
    assert utterance.transcript_edited == "我小时候住在外婆家。"
    assert message.content_text == "我小时候住在外婆家。"
    assert version.version_type == "dialect_corrected"
    assert version.created_by == "ai"
    assert version.provider == "fake-ai"
    assert version.content == "我小时候住在外婆家。"

    route_job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"route_source:{created['utterance_id']}")
    )
    assert route_job is not None
    assert route_job.job_type == MemoirEditingJobType.ROUTE_SOURCES.value
    assert route_job.payload_json["source"] == "transcript_dialect_corrected"
    assert route_job.payload_json["ai_model"] == "deepseek"
    assert route_job.payload_json[ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY] == {
        created["utterance_id"]: version.id,
    }

    source_card_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.dedupe_key == f"generate_source_card:{created['utterance_id']}"
        )
    )
    assert source_card_job is not None
    assert source_card_job.job_type == MemoirEditingJobType.GENERATE_SOURCE_CARD.value
    assert source_card_job.payload_json["reason"] == "transcript_dialect_corrected"
    assert source_card_job.payload_json["ai_model"] == "deepseek"
