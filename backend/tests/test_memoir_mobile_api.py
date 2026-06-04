from sqlalchemy import select

from app.models.memoir import AudioFile, MemoirAnswer
from app.services.asr import ASRTranscriptionResult, StepFunASRError
from app.services.memoir.audio_files import audio_file_path


class FakeAIClient:
    provider_name = "fake"
    model_name = "fake-model"

    async def _call_llm(self, *args, **kwargs):
        return "外婆家的院子，是我童年里最清晰的地方。"

    def _wrap_debug(self, *args, **kwargs):
        return {}


class FailingAIClient(FakeAIClient):
    async def _call_llm(self, *args, **kwargs):
        raise RuntimeError(
            "Server error '503 Service Unavailable' for url "
            "'https://cursor.scihub.edu.kg/api/v1/chat/completions'"
        )


async def test_mobile_memoir_routes_require_login(anonymous_client):
    response = await anonymous_client.get("/api/mobile/memoir/project")

    assert response.status_code == 401

    upload_response = await anonymous_client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.mp3", b"audio bytes", "audio/mpeg")},
    )
    assert upload_response.status_code == 401

    transcribe_response = await anonymous_client.post(
        "/api/mobile/memoir/audio-files/missing/transcribe",
    )
    assert transcribe_response.status_code == 401


async def test_mobile_memoir_project_is_created_once(client):
    first = await client.get("/api/mobile/memoir/project")
    second = await client.get("/api/mobile/memoir/project")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["type"] == "memoir"
    assert first.json()["title"] == "我的回忆录"
    assert first.json()["groups"][0]["name"] == "回忆片段"
    assert first.json()["groups"][0]["documents"][0]["name"] == "第一次记录"


async def test_mobile_memoir_current_session_initializes_questions(client):
    response = await client.get("/api/mobile/memoir/sessions/current")

    assert response.status_code == 200
    data = response.json()
    assert data["session"]["status"] == "active"
    assert data["current_question"]["id"] == data["session"]["current_question_id"]
    assert data["completed_question_count"] == 0
    assert data["pending_question_count"] == 5
    assert len(data["questions"]) == 5


async def test_mobile_memoir_can_create_custom_session(client):
    response = await client.post(
        "/api/mobile/memoir/sessions",
        json={"title": "童年"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["session"]["title"] == "童年"
    assert data["current_question"] is not None


async def test_mobile_memoir_text_answer_flow(client):
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    assert session_response.status_code == 200
    question_id = session_response.json()["current_question"]["id"]

    answer_response = await client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question_id, "text": " 我记得外婆家的院子。 "},
    )
    assert answer_response.status_code == 201
    answer = answer_response.json()
    assert answer["status"] == "answered"
    assert answer["transcript_raw"] == "我记得外婆家的院子。"
    assert answer["transcript_edited"] == "我记得外婆家的院子。"

    updated_response = await client.patch(
        f"/api/mobile/memoir/answers/{answer['id']}",
        json={"text": "外婆家的院子里有一棵树。"},
    )
    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["transcript_raw"] == "我记得外婆家的院子。"
    assert updated["transcript_edited"] == "外婆家的院子里有一棵树。"

    next_response = await client.get("/api/mobile/memoir/questions/next")
    assert next_response.status_code == 200
    assert next_response.json()["id"] != question_id


async def test_mobile_memoir_draft_flow(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.memoir.drafting.get_ai_service",
        lambda ai_model=None: FakeAIClient(),
    )
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]
    answer_response = await client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question_id, "text": "我记得外婆家的院子。"},
    )
    answer_id = answer_response.json()["id"]

    draft_response = await client.post(
        f"/api/mobile/memoir/answers/{answer_id}/draft",
        json={"ai_model": "deepseek"},
    )
    assert draft_response.status_code == 200
    draft = draft_response.json()
    assert draft["status"] == "draft"
    assert draft["content_raw"] == "外婆家的院子，是我童年里最清晰的地方。"
    assert draft["content_edited"] == draft["content_raw"]

    edit_response = await client.patch(
        f"/api/mobile/memoir/drafts/{draft['id']}",
        json={"content_edited": "编辑后的回忆录正文"},
    )
    assert edit_response.status_code == 200
    assert edit_response.json()["content_edited"] == "编辑后的回忆录正文"

    accept_response = await client.post(f"/api/mobile/memoir/drafts/{draft['id']}/accept")
    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "accepted"

    regenerate_response = await client.post(
        f"/api/mobile/memoir/answers/{answer_id}/draft",
        json={},
    )
    assert regenerate_response.status_code == 409


async def test_mobile_memoir_draft_failure_returns_public_message(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.memoir.drafting.get_ai_service",
        lambda ai_model=None: FailingAIClient(),
    )
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]
    answer_response = await client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question_id, "text": "我记得外婆家的院子。"},
    )
    answer_id = answer_response.json()["id"]

    draft_response = await client.post(
        f"/api/mobile/memoir/answers/{answer_id}/draft",
        json={},
    )

    assert draft_response.status_code == 503
    assert draft_response.json()["detail"] == "AI 服务暂时不可用，请稍后重试。"
    assert "cursor.scihub" not in draft_response.text


async def test_mobile_memoir_rejects_blank_answer(client):
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]

    response = await client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question_id, "text": "  "},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "回答不能为空"


async def test_mobile_memoir_text_answer_links_uploaded_audio_file(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    upload_response = await client.post(
        "/api/mobile/memoir/audio-files",
        data={"duration_seconds": "7"},
        files={"file": ("answer.mp3", b"audio bytes", "audio/mpeg")},
    )
    audio_id = upload_response.json()["id"]
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]

    response = await client.post(
        "/api/mobile/memoir/answers",
        json={
            "question_id": question_id,
            "text": "我小时候经常去外婆家。",
            "audio_file_id": audio_id,
        },
    )

    assert response.status_code == 201
    answer = response.json()
    assert answer["audio_file_id"] == audio_id
    assert answer["duration_seconds"] == 7
    stored = await test_db.scalar(select(MemoirAnswer).where(MemoirAnswer.id == answer["id"]))
    assert stored.audio_file_id == audio_id
    assert stored.duration_seconds == 7


async def test_mobile_memoir_skip_question(client):
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]

    response = await client.post(f"/api/mobile/memoir/questions/{question_id}/skip")

    assert response.status_code == 200
    data = response.json()
    assert data["questions"][0]["status"] == "skipped"
    assert data["completed_question_count"] == 1
    assert data["pending_question_count"] == 4


async def test_mobile_memoir_uploads_audio_file(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")

    response = await client.post(
        "/api/mobile/memoir/audio-files",
        data={"duration_seconds": "7"},
        files={"file": ("answer.mp3", b"audio bytes", "audio/mpeg")},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["mime_type"] == "audio/mpeg"
    assert data["size_bytes"] == len(b"audio bytes")
    assert data["duration_seconds"] == 7
    assert "storage_path" not in data
    assert "user_id" not in data

    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == data["id"]))
    assert audio_file is not None
    assert audio_file.storage_path.endswith(f"{audio_file.id}.mp3")
    assert (tmp_path / "audio" / audio_file.user_id / f"{audio_file.id}.mp3").read_bytes() == b"audio bytes"


async def test_mobile_memoir_transcribes_audio_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    captured = {}

    class FakeASRService:
        async def transcribe(self, audio_bytes, audio_format):
            captured["audio_bytes"] = audio_bytes
            captured["audio_format"] = audio_format
            return ASRTranscriptionResult(
                text=" 外婆家的院子。 ",
                deltas=("外婆", "家的院子。"),
                usage={"total_tokens": 6},
            )

    monkeypatch.setattr(
        "app.services.memoir.transcription.StepFunASRService",
        lambda: FakeASRService(),
    )

    upload_response = await client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.mp3", b"audio bytes", "audio/mpeg")},
    )
    audio_id = upload_response.json()["id"]

    response = await client.post(f"/api/mobile/memoir/audio-files/{audio_id}/transcribe")

    assert response.status_code == 200
    assert response.json() == {
        "audio_file_id": audio_id,
        "text": "外婆家的院子。",
        "usage": {"total_tokens": 6},
    }
    assert captured["audio_bytes"] == b"audio bytes"
    assert captured["audio_format"].type == "mp3"


async def test_mobile_memoir_transcribe_rejects_pcm_until_metadata_is_stored(
    client,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    upload_response = await client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.pcm", b"audio bytes", "audio/pcm")},
    )
    audio_id = upload_response.json()["id"]

    response = await client.post(f"/api/mobile/memoir/audio-files/{audio_id}/transcribe")

    assert response.status_code == 400
    assert response.json()["detail"] == "PCM 音频暂不支持转写"


async def test_mobile_memoir_transcribe_returns_404_when_file_is_missing(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    upload_response = await client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.mp3", b"audio bytes", "audio/mpeg")},
    )
    audio_id = upload_response.json()["id"]
    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == audio_id))
    audio_file_path(audio_file).unlink()

    response = await client.post(f"/api/mobile/memoir/audio-files/{audio_id}/transcribe")

    assert response.status_code == 404
    assert response.json()["detail"] == "音频文件不存在"


async def test_mobile_memoir_transcribe_hides_upstream_errors(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")

    class FailingASRService:
        async def transcribe(self, audio_bytes, audio_format):
            raise StepFunASRError("upstream api token rejected")

    monkeypatch.setattr(
        "app.services.memoir.transcription.StepFunASRService",
        lambda: FailingASRService(),
    )
    upload_response = await client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.mp3", b"audio bytes", "audio/mpeg")},
    )
    audio_id = upload_response.json()["id"]

    response = await client.post(f"/api/mobile/memoir/audio-files/{audio_id}/transcribe")

    assert response.status_code == 503
    assert response.json()["detail"] == "语音转写服务暂时不可用，请稍后重试。"
    assert "upstream" not in response.text
    assert "token" not in response.text


async def test_mobile_memoir_upload_accepts_generic_mime_with_supported_extension(
    client,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")

    response = await client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.mp3", b"audio bytes", "application/octet-stream")},
    )

    assert response.status_code == 201
    assert response.json()["mime_type"] == "audio/mpeg"


async def test_mobile_memoir_rejects_unsupported_audio_format(client):
    response = await client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.aac", b"audio bytes", "audio/aac")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "仅支持 mp3、wav、ogg、pcm 音频文件"


async def test_mobile_memoir_rejects_negative_audio_duration(client):
    response = await client.post(
        "/api/mobile/memoir/audio-files",
        data={"duration_seconds": "-1"},
        files={"file": ("answer.mp3", b"audio bytes", "audio/mpeg")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "音频时长不能为负数"


async def test_mobile_memoir_rejects_oversized_audio_file(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr("app.services.memoir.audio_files.MAX_AUDIO_BYTES", 3)

    response = await client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.mp3", b"audio", "audio/mpeg")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "音频文件不能超过 20MB"
    assert await test_db.scalar(select(AudioFile)) is None
    assert not any(path.is_file() for path in (tmp_path / "audio").rglob("*"))


async def test_mobile_memoir_hides_other_users_questions(anonymous_client):
    first_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "memoir-first@example.com",
            "password": "password123",
            "display_name": "第一位作者",
        },
    )
    assert first_register.status_code == 201
    session_response = await anonymous_client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]

    await anonymous_client.post("/api/auth/logout")

    second_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "memoir-second@example.com",
            "password": "password123",
            "display_name": "第二位作者",
        },
    )
    assert second_register.status_code == 201

    response = await anonymous_client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question_id, "text": "越权回答"},
    )

    assert response.status_code == 404


async def test_mobile_memoir_hides_other_users_audio_files(anonymous_client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    first_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "audio-first@example.com",
            "password": "password123",
            "display_name": "第一位作者",
        },
    )
    assert first_register.status_code == 201
    upload_response = await anonymous_client.post(
        "/api/mobile/memoir/audio-files",
        files={"file": ("answer.mp3", b"audio bytes", "audio/mpeg")},
    )
    audio_id = upload_response.json()["id"]

    await anonymous_client.post("/api/auth/logout")

    second_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "audio-second@example.com",
            "password": "password123",
            "display_name": "第二位作者",
        },
    )
    assert second_register.status_code == 201

    response = await anonymous_client.post(f"/api/mobile/memoir/audio-files/{audio_id}/transcribe")

    assert response.status_code == 404
