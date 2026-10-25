import pytest

from sqlalchemy import select

from app.config import DEFAULT_AI_TEXT_MODEL
from app.services.ai_base import AITextServiceBase
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
            "ai_model": DEFAULT_AI_TEXT_MODEL,
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
    assert topic_map_job.payload_json["ai_model"] == DEFAULT_AI_TEXT_MODEL


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


@pytest.mark.parametrize("level", ["low", "medium", "high"])
@pytest.mark.parametrize("batch", [False, True])
async def test_cleanup_entries_use_project_level_and_only_preceding_context(
    client, test_db, monkeypatch, level, batch,
):
    created = await create_test_voice_transcript(
        client, test_db, text="我小时候跟幺爸去赶场。", question_text="您小时候跟谁去赶场？",
    )
    project_id = created["session"]["project_id"]
    project = await test_db.get(Project, project_id)
    project.metadata_json = {**(project.metadata_json or {}), "transcript_polish_level": level}
    test_db.add(MemoirChatMessage(
        project_id=project_id, voice_session_id=created["session"]["id"],
        role="assistant", content_text="后来您退休了吗？", turn_index=99,
    ))
    await test_db.commit()
    ai = CapturingCleanupAIClient()
    monkeypatch.setattr("app.services.memoir.voice_transcripts.get_ai_service", lambda model=None: ai)
    if batch:
        response = await client.post(
            f"/api/memoir/projects/{project_id}/transcript-cleanup/preview",
            json={"utterance_ids": [created["utterance_id"]]},
        )
    else:
        response = await client.post(
            f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/cleanup", json={},
        )
    assert response.status_code == 200
    assert f"“{level}”" in ai.prompts[0]
    assert "您小时候跟谁去赶场？" in ai.prompts[0]
    assert "后来您退休了吗？" not in ai.prompts[0]


@pytest.mark.parametrize("manual_type", ["user_edited", "dialect_corrected"])
async def test_recleanup_uses_stable_source_and_preserves_latest_manual_edit(client, test_db, manual_type):
    from app.services.memoir.transcript_projection import cleanup_source_text
    from app.services.memoir.voice_transcripts import prune_transcript_versions_for_utterance

    created = await create_test_voice_transcript(client, test_db, text="我小时候住在外婆家。")
    utterance = await test_db.get(MemoirVoiceUtterance, created["utterance_id"])
    utterance.transcript_edited = "我的童年在外婆家度过，满是温暖。"
    assert await cleanup_source_text(test_db, utterance) == "我小时候住在外婆家。"
    test_db.add(MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id, source_artifact_id=utterance.id,
        version_type=manual_type, created_by="user", content="我小时候住在奶奶家。",
    ))
    await test_db.flush()
    utterance.transcript_edited = "奶奶家承载着我美好的童年。"
    for _ in range(5):
        test_db.add(MemoirTranscriptVersion(
            voice_session_id=utterance.voice_session_id, source_artifact_id=utterance.id,
            version_type="dialect_corrected", created_by="ai", content=utterance.transcript_edited,
        ))
        await test_db.flush()
        await prune_transcript_versions_for_utterance(
            test_db, utterance_id=utterance.id, version_type="dialect_corrected",
        )
        assert await cleanup_source_text(test_db, utterance) == "我小时候住在奶奶家。"


@pytest.mark.parametrize("role", ["assistant", "interviewer"])
@pytest.mark.parametrize("parent", ["none", "older", "future", "other_session", "answer", "detached"])
async def test_answer_context_follows_valid_parent_or_preceding_question(client, test_db, role, parent):
    from app.services.memoir.material_roles import interviewer_lines

    source = await create_test_voice_transcript(client, test_db, text="五岁。", question_text="发大水那年您多大？")
    other = await create_test_voice_transcript(
        client, test_db, project_id=source["session"]["project_id"], question_text="另一场访谈的问题？",
    )
    old_question = await test_db.scalar(select(MemoirChatMessage).where(
        MemoirChatMessage.voice_session_id == source["session"]["id"], MemoirChatMessage.turn_index == 0,
    ))
    old_question.role = role
    def message(text, turn, speaker, **kwargs):
        return MemoirChatMessage(
            project_id=source["session"]["project_id"], voice_session_id=source["session"]["id"],
            content_text=text, turn_index=turn, role=speaker, content_status="final", **kwargs,
        )
    question = message("谁带您去邮局领工资？", 2, role)
    answer = message("刘师傅。", 3, "user")
    continuation = message("他还让我数了一遍。", 4, "user")
    reminder = message("您可以接着慢慢说。", 5, "assistant", guidance_intent="no_answer_prompt")
    resumed = message("那钱我收在衣兜里。", 6, "user")
    future = message("退休以后呢？", 7, role)
    test_db.add_all([question, answer, continuation, reminder, resumed, future])
    await test_db.flush()
    other_question = await test_db.scalar(select(MemoirChatMessage).where(
        MemoirChatMessage.voice_session_id == other["session"]["id"], MemoirChatMessage.turn_index == 0,
    ))
    answer.parent_message_id = {
        "none": None, "detached": None, "older": old_question.id, "future": future.id,
        "other_session": other_question.id, "answer": source["message_id"],
    }[parent]
    if parent == "detached":
        answer.metadata_json = {"relation_updated_at": "2026-10-25T00:00:00"}
    await test_db.flush()
    lines = await interviewer_lines(test_db, [source["message_id"], answer.id, continuation.id, resumed.id])
    assert lines[source["message_id"]] == old_question.content_text
    if parent == "detached":
        assert answer.id not in lines
    else:
        assert lines[answer.id] == {"older": old_question.content_text, "future": future.content_text}.get(parent, question.content_text)
    assert lines[continuation.id] == lines[resumed.id] == question.content_text
