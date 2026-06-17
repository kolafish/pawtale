from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.services.memoir.voice_tts import TTSResult


class FakeBookTTS:
    provider_name = "fake-tts"
    model_name = "fake-tts-model"

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, *, voice=None):
        self.calls.append({"text": text, "voice": voice})
        return TTSResult(
            audio=f"audio:{len(self.calls)}".encode(),
            mime_type="audio/mpeg",
            extension=".mp3",
            provider=self.provider_name,
            model=self.model_name,
        )


async def _active_project_id(client):
    response = await client.get("/api/mobile/memoir/projects")
    assert response.status_code == 200
    return response.json()["active_project_id"]


async def _create_confirmed_book_plan(test_db, *, project_id: str, confirmed_chapters: bool = True):
    plan = MemoirBookPlan(
        project_id=project_id,
        title="外婆回忆录章节规划",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapters = []
    for index, (title, content) in enumerate(
        [
            ("外婆家的童年", "我小时候常在外婆家的院子里晒太阳。"),
            ("第一次离家", "后来我第一次离家去县城读书，心里很紧张。"),
        ]
    ):
        chapter = MemoirBookPlanChapter(
            plan_id=plan.id,
            project_id=project_id,
            title=title,
            summary="",
            sort_order=index,
            source_refs_json=[],
        )
        test_db.add(chapter)
        await test_db.flush()
        draft = MemoirBookChapterDraft(
            project_id=project_id,
            chapter_id=chapter.id,
            status=(
                MemoirBookChapterDraftStatus.CONFIRMED.value
                if confirmed_chapters or index == 0
                else MemoirBookChapterDraftStatus.DRAFT.value
            ),
        )
        test_db.add(draft)
        await test_db.flush()
        test_db.add(
            MemoirBookChapterDraftVersion(
                draft_id=draft.id,
                project_id=project_id,
                chapter_id=chapter.id,
                version=1,
                content=content,
                created_by=MemoirCreatedBy.USER.value,
                prompt_snapshot="",
            )
        )
        chapters.append(chapter)
    await test_db.commit()
    return plan, chapters


async def test_mobile_memoir_book_output_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-output/generate",
        json={},
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_output_requires_all_chapters_confirmed(client, test_db):
    project_id = await _active_project_id(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id, confirmed_chapters=False)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/generate",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "请先确认所有章节：第一次离家"


async def test_mobile_memoir_book_output_generate_combines_confirmed_chapters(client, test_db):
    project_id = await _active_project_id(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/generate",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "外婆回忆录"
    assert data["latest_version"]["version"] == 1
    content = data["latest_version"]["content"]
    assert "目录\n1. 外婆家的童年\n2. 第一次离家" in content
    assert "第1章 外婆家的童年" in content
    assert "我小时候常在外婆家的院子里晒太阳。" in content
    assert "第2章 第一次离家" in content
    assert "后来我第一次离家去县城读书，心里很紧张。" in content
    snapshot = data["latest_version"]["chapter_snapshot_json"]
    assert [item["chapter_id"] for item in snapshot] == [chapters[0].id, chapters[1].id]

    stored_output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    stored_versions = (
        await test_db.execute(
            select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.output_id == stored_output.id)
        )
    ).scalars().all()
    assert stored_output.plan_id == plan.id
    assert len(stored_versions) == 1


async def test_mobile_memoir_book_output_generate_returns_existing_without_force(client, test_db):
    project_id = await _active_project_id(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    first = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    second = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["latest_version"]["version"] == 1


async def test_mobile_memoir_book_chapter_tts_generates_and_reuses_audio(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    project_id = await _active_project_id(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    fake_tts = FakeBookTTS()
    monkeypatch.setattr("app.services.memoir.book_audio.get_tts_client", lambda: fake_tts)

    first = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[0].id}/draft/tts",
        json={"voice": "voice-a"},
    )
    second = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[0].id}/draft/tts",
        json={"voice": "voice-a"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["audio_file_ids"] == second.json()["audio_file_ids"]
    assert first.json()["chunk_count"] == 1
    assert len(fake_tts.calls) == 1
    assert fake_tts.calls[0]["text"] == "我小时候常在外婆家的院子里晒太阳。"
    playback = await client.get(f"/api/mobile/memoir/audio-files/{first.json()['audio_file_ids'][0]}/playback")
    assert playback.status_code == 200


async def test_mobile_memoir_book_output_tts_generates_full_book_audio(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    project_id = await _active_project_id(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    fake_tts = FakeBookTTS()
    monkeypatch.setattr("app.services.memoir.book_audio.get_tts_client", lambda: fake_tts)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/tts",
        json={"voice": "voice-book"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["audio_file_ids"]
    assert data["provider"] == "fake-tts"
    assert len(fake_tts.calls) == data["chunk_count"]
    assert "外婆回忆录" in fake_tts.calls[0]["text"]
    assert fake_tts.calls[0]["voice"] == "voice-book"
