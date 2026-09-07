
from app.services.ai_base import AITextServiceBase
from sqlalchemy import select

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirChatMessage, MemoirEditingJob, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.models.projects import Project
from tests.memoir_helpers import create_test_voice_transcript


class CapturingCleanupAIClient(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.prompts.append(user_prompt)
        return "我小时候跟小叔去赶场。"


async def _preview_item(client, monkeypatch, *, project_id: str, utterance_id: str) -> dict:
    monkeypatch.setattr(
        "app.services.memoir.voice_transcripts.get_ai_service",
        lambda model=None: CapturingCleanupAIClient(),
    )
    response = await client.post(
        f"/api/memoir/projects/{project_id}/transcript-cleanup/preview",
        json={"utterance_ids": [utterance_id]},
    )
    assert response.status_code == 200
    return response.json()["items"][0]


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
    assert len(preview["items"][0]["source_text_hash"]) == 64
    assert preview["items"][0]["source_transcript_version_id"] == created["transcript_version_id"]
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
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    preview_item = await _preview_item(
        client,
        monkeypatch,
        project_id=project_id,
        utterance_id=created["utterance_id"],
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/transcript-cleanup/confirm",
        json={
            "ai_model": "deepseek",
            "items": [
                {
                    "utterance_id": created["utterance_id"],
                    "source_text_hash": preview_item["source_text_hash"],
                    "source_transcript_version_id": preview_item["source_transcript_version_id"],
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

    organization_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.dedupe_key == f"organize_material:{created['utterance_id']}"
        )
    )
    assert organization_job is None

    topic_map_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.UPDATE_TOPIC_MAP.value
        )
    )
    assert topic_map_job is not None
    assert topic_map_job.payload_json["material_ids"] == [created["utterance_id"]]
    assert topic_map_job.payload_json["ai_model"] == "deepseek"


async def test_memoir_transcript_cleanup_confirm_rejects_stale_preview(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    preview_item = await _preview_item(
        client,
        monkeypatch,
        project_id=project_id,
        utterance_id=created["utterance_id"],
    )

    edit_response = await client.patch(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
        json={"transcript_edited": "用户在预览后补充了新的内容。"},
    )
    assert edit_response.status_code == 200

    response = await client.post(
        f"/api/memoir/projects/{project_id}/transcript-cleanup/confirm",
        json={
            "items": [
                {
                    "utterance_id": created["utterance_id"],
                    "source_text_hash": preview_item["source_text_hash"],
                    "source_transcript_version_id": preview_item["source_transcript_version_id"],
                    "cleaned_text": "基于旧预览生成的整理内容。",
                    "provider": "fake-ai",
                    "created_by": "ai",
                }
            ]
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "讲述内容已更新，请重新生成整理预览"
    await test_db.refresh(utterance)
    assert utterance.transcript_edited == "用户在预览后补充了新的内容。"


async def test_memoir_transcript_cleanup_rejects_entire_batch_when_one_preview_is_stale(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, text="第一段原始讲述。")
    project_id = first["session"]["project_id"]
    second = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="第二段原始讲述。",
    )
    monkeypatch.setattr(
        "app.services.memoir.voice_transcripts.get_ai_service",
        lambda model=None: CapturingCleanupAIClient(),
    )
    preview_response = await client.post(
        f"/api/memoir/projects/{project_id}/transcript-cleanup/preview",
        json={"utterance_ids": [first["utterance_id"], second["utterance_id"]]},
    )
    assert preview_response.status_code == 200
    preview_items = preview_response.json()["items"]

    edit_response = await client.patch(
        f"/api/mobile/memoir/voice-transcripts/{second['utterance_id']}",
        json={"transcript_edited": "第二段在预览后被用户更新。"},
    )
    assert edit_response.status_code == 200

    response = await client.post(
        f"/api/memoir/projects/{project_id}/transcript-cleanup/confirm",
        json={
            "items": [
                {
                    "utterance_id": item["utterance_id"],
                    "source_text_hash": item["source_text_hash"],
                    "source_transcript_version_id": item["source_transcript_version_id"],
                    "cleaned_text": f"整理后的第{index}段。",
                    "provider": "fake-ai",
                    "created_by": "ai",
                }
                for index, item in enumerate(preview_items, start=1)
            ]
        },
    )

    assert response.status_code == 409
    utterances = (
        await test_db.execute(
            select(MemoirVoiceUtterance).where(
                MemoirVoiceUtterance.id.in_([first["utterance_id"], second["utterance_id"]])
            )
        )
    ).scalars().all()
    text_by_id = {utterance.id: utterance.transcript_edited for utterance in utterances}
    assert text_by_id == {
        first["utterance_id"]: "第一段原始讲述。",
        second["utterance_id"]: "第二段在预览后被用户更新。",
    }
    jobs = await test_db.scalars(select(MemoirEditingJob).where(MemoirEditingJob.project_id == project_id))
    assert all(
        (job.payload_json or {}).get("reason") != "transcript_dialect_corrected"
        for job in jobs.all()
    )
