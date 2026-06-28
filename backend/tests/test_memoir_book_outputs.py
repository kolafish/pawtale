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
from app.services.memoir.book_audio import split_tts_text
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_tts import TTSResult


class FakeBookTTS:
    provider_name = "fake-tts"
    model_name = "fake-tts-model"
    default_voice = ""

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, *, voice=None, speed_ratio=None):
        self.calls.append({"text": text, "voice": voice, "speed_ratio": speed_ratio})
        alignment_end = min(6, len(text))
        duration_seconds = max(0.2, alignment_end * 0.08)
        return TTSResult(
            audio=f"audio:{len(self.calls)}".encode(),
            mime_type="audio/mpeg",
            extension=".mp3",
            provider=self.provider_name,
            model=self.model_name,
            voice=voice or self.default_voice,
            speed_ratio=speed_ratio or 1.0,
            alignment=[
                TTSAlignmentSegment(
                    text=text[:alignment_end],
                    start_ms=0,
                    end_ms=int(duration_seconds * 1000),
                    text_start=0,
                    text_end=alignment_end,
                )
            ],
            duration_seconds=duration_seconds,
        )


async def _active_project_id(client):
    response = await client.get("/api/mobile/memoir/projects")
    assert response.status_code == 200
    active_project_id = response.json()["active_project_id"]
    if active_project_id:
        return active_project_id
    created = await client.post("/api/mobile/memoir/projects", json={"title": "测试回忆录"})
    assert created.status_code == 201
    return created.json()["id"]


def test_book_tts_chunks_keep_offsets_across_blank_lines():
    chunks = split_tts_text("标题\n\n第一段正文。\n\n第二段正文。", max_chars=8)

    assert [chunk.text for chunk in chunks] == ["标题", "第一段正文。", "第二段正文。"]
    assert [chunk.text_start for chunk in chunks] == [0, 4, 12]


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
    chapters[0].source_refs_json = [
        {
            "utterance_id": "utterance-1",
            "transcript_version_id": "transcript-version-1",
            "voice_session_id": "voice-session-1",
            "quote": "我小时候常在外婆家的院子里晒太阳。",
        }
    ]
    await test_db.commit()

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
    assert snapshot[0]["chapter_index"] == 1
    assert snapshot[0]["content"] == "我小时候常在外婆家的院子里晒太阳。"
    structured = data["latest_version"]["structured_content"]
    assert structured["title"] == "外婆回忆录"
    assert structured["chapter_count"] == 2
    assert [item["title"] for item in structured["table_of_contents"]] == ["外婆家的童年", "第一次离家"]
    assert structured["chapters"][0]["chapter_id"] == chapters[0].id
    assert structured["chapters"][0]["index"] == 1
    assert structured["chapters"][0]["content"] == "我小时候常在外婆家的院子里晒太阳。"
    manuscript = data["latest_version"]["manuscript"]
    assert manuscript["schema_version"] == 1
    assert manuscript["profile"]["template"] == "classic_family"
    assert manuscript["profile"]["title"] == "外婆回忆录"
    assert manuscript["profile"]["subtitle"] == "根据口述访谈整理"
    assert [section["kind"] for section in manuscript["front_matter"]] == [
        "cover",
        "title_page",
        "generation_note",
        "preface",
    ]
    assert [item["title"] for item in manuscript["table_of_contents"]] == ["外婆家的童年", "第一次离家"]
    assert manuscript["chapters"][0]["chapter_id"] == chapters[0].id
    assert manuscript["chapters"][0]["intro"].startswith("这一章主要记录「外婆家的童年」")
    assert manuscript["chapters"][0]["pull_quotes"][0]["text"] == "我小时候常在外婆家的院子里晒太阳。"
    assert manuscript["chapters"][0]["source_refs"][0]["utterance_id"] == "utterance-1"
    assert manuscript["back_matter"]["source_appendix"]["title"] == "章节来源"
    assert "第1章「外婆家的童年」：1 条讲述来源" in manuscript["back_matter"]["source_appendix"]["content"]
    assert manuscript["render_settings"]["page_size"] == "a5"

    stored_output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    stored_versions = (
        await test_db.execute(
            select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.output_id == stored_output.id)
        )
    ).scalars().all()
    assert stored_output.plan_id == plan.id
    assert len(stored_versions) == 1
    assert stored_versions[0].manuscript_json["profile"]["title"] == "外婆回忆录"


async def test_mobile_memoir_book_output_generate_returns_existing_without_force(client, test_db):
    project_id = await _active_project_id(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    first = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    second = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["latest_version"]["version"] == 1
    assert second.json()["is_stale"] is False


async def test_mobile_memoir_book_output_exports_txt_and_pdf(client, test_db):
    project_id = await _active_project_id(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    generated = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert generated.status_code == 200

    txt = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=txt")
    pdf = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=pdf")
    pdf_without_sources = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=pdf&show_source_appendix=false"
    )
    print_pdf = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=pdf&layout=large_print"
    )

    assert txt.status_code == 200
    assert txt.headers["content-type"].startswith("text/plain")
    assert "filename*=" in txt.headers["content-disposition"]
    assert "外婆回忆录" in txt.text
    assert "第1章 外婆家的童年" in txt.text
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    assert b"/MediaBox [ 0 0 420 595 ]" in pdf.content
    assert b"/FontFile2" in pdf.content
    assert b"NotoSansSC" in pdf.content
    assert pdf.content.count(b"/Type /Page") >= 8
    assert pdf_without_sources.status_code == 200
    assert pdf_without_sources.content.count(b"/Type /Page") < pdf.content.count(b"/Type /Page")
    assert print_pdf.status_code == 200
    assert b"/FontFile2" in print_pdf.content
    assert print_pdf.content != pdf.content
    assert "filename*=" in print_pdf.headers["content-disposition"]

    stored_version = await test_db.scalar(
        select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.project_id == project_id)
    )
    stored_version.manuscript_json = {}
    await test_db.commit()
    fallback_pdf = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=pdf")

    assert fallback_pdf.status_code == 200
    assert fallback_pdf.content.startswith(b"%PDF-")
    assert b"/MediaBox [ 0 0 420 595 ]" in fallback_pdf.content
    assert b"/FontFile2" in fallback_pdf.content


async def test_mobile_memoir_book_output_exports_selected_version(client, test_db):
    project_id = await _active_project_id(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    first = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert first.status_code == 200
    first_version_id = first.json()["latest_version"]["id"]

    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == chapters[0].id)
    )
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapters[0].id,
            version=2,
            content="新版第一章正文。",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()
    second = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/generate",
        json={"force_regenerate": True},
    )
    assert second.status_code == 200

    exported = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=txt&version_id={first_version_id}"
    )

    assert exported.status_code == 200
    assert "我小时候常在外婆家的院子里晒太阳。" in exported.text
    assert "新版第一章正文" not in exported.text


async def test_mobile_memoir_book_output_detects_and_regenerates_stale_chapter_versions(client, test_db):
    project_id = await _active_project_id(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    first = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert first.status_code == 200

    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == chapters[0].id)
    )
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapters[0].id,
            version=2,
            content="我小时候常在外婆家的院子里晒太阳，也常听外婆讲老屋的事。",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()

    stale = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")

    assert stale.status_code == 200
    stale_data = stale.json()
    assert stale_data["latest_version"]["version"] == 1
    assert stale_data["is_stale"] is True
    assert stale_data["stale_chapter_titles"] == ["外婆家的童年"]

    regenerated = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})

    assert regenerated.status_code == 200
    regenerated_data = regenerated.json()
    assert regenerated_data["latest_version"]["version"] == 2
    assert regenerated_data["is_stale"] is False
    assert "老屋的事" in regenerated_data["latest_version"]["content"]


async def test_mobile_memoir_book_output_marks_unconfirmed_plan_as_stale(client, test_db):
    project_id = await _active_project_id(client)
    plan, _ = await _create_confirmed_book_plan(test_db, project_id=project_id)
    generated = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert generated.status_code == 200
    plan.status = MemoirBookPlanStatus.DRAFT.value
    await test_db.commit()

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")

    assert response.status_code == 200
    data = response.json()
    assert data["is_stale"] is True
    assert data["stale_chapter_titles"] == ["章节规划"]


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
        json={"voice": "voice-a", "speed_ratio": 1.5},
    )
    second = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[0].id}/draft/tts",
        json={"voice": "voice-a", "speed_ratio": 1.5},
    )
    third = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[0].id}/draft/tts",
        json={"voice": "voice-a", "speed_ratio": 0.7},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert first.json()["audio_file_ids"] == second.json()["audio_file_ids"]
    assert first.json()["audio_file_ids"] != third.json()["audio_file_ids"]
    assert first.json()["chunk_count"] == 1
    assert first.json()["audio_file_durations_seconds"] == [0.48]
    assert first.json()["duration_seconds"] == 0.48
    assert first.json()["alignment_provider"] == "fake-tts"
    assert first.json()["alignment"][0]["text_start"] == 0
    assert len(fake_tts.calls) == 2
    assert fake_tts.calls[0]["text"] == "我小时候常在外婆家的院子里晒太阳。"
    assert fake_tts.calls[0]["speed_ratio"] == 1.5
    assert fake_tts.calls[1]["speed_ratio"] == 0.7
    playback = await client.get(f"/api/mobile/memoir/audio-files/{first.json()['audio_file_ids'][0]}/playback")
    assert playback.status_code == 200


async def test_mobile_memoir_book_outline_tts_generates_and_reuses_audio(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    project_id = await _active_project_id(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    chapters[0].source_refs_json = [{"utterance_id": "utterance-1"}]
    chapters[1].source_refs_json = [{"utterance_id": "utterance-2"}, {"utterance_id": "utterance-3"}]
    await test_db.commit()
    fake_tts = FakeBookTTS()
    monkeypatch.setattr("app.services.memoir.book_audio.get_tts_client", lambda: fake_tts)

    first = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/outline/tts",
        json={"voice": "voice-outline", "speed_ratio": 1.25},
    )
    second = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/outline/tts",
        json={"voice": "voice-outline", "speed_ratio": 1.25},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["audio_file_ids"] == second.json()["audio_file_ids"]
    assert len(fake_tts.calls) == 1
    outline_text = fake_tts.calls[0]["text"]
    assert "《外婆回忆录章节规划》现在有 2 章。" in outline_text
    assert "第 1 章，外婆家的童年。本章收录 1 条语音素材。" in outline_text
    assert "第 2 章，第一次离家。本章收录 2 条语音素材。" in outline_text
    assert "你可以继续调整章节顺序、合并章节，或者说出想修改的地方。" in outline_text
    assert fake_tts.calls[0]["voice"] == "voice-outline"
    assert fake_tts.calls[0]["speed_ratio"] == 1.25


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
        json={"voice": "voice-book", "speed_ratio": 1.5},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["audio_file_ids"]
    assert data["provider"] == "fake-tts"
    assert len(fake_tts.calls) == data["chunk_count"]
    assert "外婆回忆录" in fake_tts.calls[0]["text"]
    assert fake_tts.calls[0]["voice"] == "voice-book"
    assert fake_tts.calls[0]["speed_ratio"] == 1.5
    assert len(data["audio_file_durations_seconds"]) == data["chunk_count"]
    assert data["alignment_provider"] == "fake-tts"
    assert data["alignment"][0]["text_start"] == 0
