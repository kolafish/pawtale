from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.memoir.book_manuscript_ai import generate_manuscript_editorial_copy, generate_title_suggestions
from app.services.memoir.book_manuscript_cleanup import sanitize_manuscript_payload
from app.services.memoir.book_audio import split_tts_text
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers, run_editing_job
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_tts_types import TTSResult
from tests.memoir_helpers import (
    add_test_chapter_source_links,
    create_test_book_plan_with_chapters,
    ensure_mobile_memoir_project,
)


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


class FakeEditorialAI:
    provider_name = "fake-ai"
    model_name = "fake-editorial-model"

    def __init__(self):
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, temperature=0.7, max_tokens=4000, timeout=60.0, return_raw=False):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        chapter_id = "chapter-1"
        marker = "chapter_id: "
        if marker in user_prompt:
            chapter_id = user_prompt.split(marker, 1)[1].splitlines()[0].strip()
        return f"""
        {{
          "book_title": "外婆慢慢说",
          "cover_subtitle": "关于童年与离家的记忆",
          "preface": "这些讲述从院子里的童年开始，也写到第一次离家时的紧张。整理成书，是为了让家里人以后还能翻到这些细节。",
          "afterword": "这一次先把已经讲出来的童年、离家和家里的细节留下来。以后家人如果想起新的年份、地点或照片，还可以继续补进书里，让这些话慢慢成为家里共同保存的记忆。",
          "chapter_intros": [
            {{
              "chapter_id": "{chapter_id}",
              "intro": "记忆先回到外婆家的院子，阳光和旧日子的声音都在那里。",
              "pull_quote": "我小时候常在外婆家的院子里晒太阳。",
              "pull_quote_source_ref_id": "u1"
            }}
          ],
          "back_cover_quote": "我小时候常在外婆家的院子里晒太阳。"
        }}
        """


class FakeTitleAI:
    provider_name = "fake-ai"
    model_name = "fake-title-model"

    def __init__(self):
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, temperature=0.7, max_tokens=4000, timeout=60.0, return_raw=False):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        return '{"suggestions": ["外婆慢慢说", "外婆的一生", "从前的日子", "外婆主题回忆录"]}'


class FakeTemplateCopyAI:
    provider_name = "fake-ai"
    model_name = "fake-template-copy-model"

    async def _call_llm(self, system_prompt, user_prompt, temperature=0.7, max_tokens=4000, timeout=60.0, return_raw=False):
        return """
        {
          "book_title": "外婆慢慢说",
          "cover_subtitle": "关于童年与离家的记忆",
          "preface": "这本书记录的是「爷爷主题回忆录」中的生活。",
          "afterword": "愿常翻看。",
          "chapter_intros": [
            {
              "chapter_id": "chapter-1",
              "intro": "这一章主要记录外婆家的童年相关的回忆。",
              "pull_quote": "这句不是原始口述里的金句。",
              "pull_quote_source_ref_id": "u1"
            }
          ],
          "back_cover_quote": "凡是过往皆是序章"
        }
        """


def test_book_tts_chunks_keep_offsets_across_blank_lines():
    chunks = split_tts_text("标题\n\n第一段正文。\n\n第二段正文。", max_chars=8)

    assert [chunk.text for chunk in chunks] == ["标题", "第一段正文。", "第二段正文。"]
    assert [chunk.text_start for chunk in chunks] == [0, 4, 12]


async def test_memoir_book_manuscript_editorial_copy_uses_llm_context():
    fake_ai = FakeEditorialAI()

    copy = await generate_manuscript_editorial_copy(
        title="外婆的回忆录",
        snapshot=[
            {
                "chapter_id": "chapter-1",
                "chapter_index": 1,
                "chapter_title": "外婆家的童年",
                "source_count": 2,
                "chapter_summary": "这一章围绕外婆家的院子和第一次离家的记忆展开。",
                "source_refs": [
                    {
                        "utterance_id": "u1",
                        "summary": "讲述小时候在院子里晒太阳，也记得外婆忙碌的身影。",
                        "quote": "我小时候常在外婆家的院子里晒太阳。",
                    }
                ],
                "content": "我小时候常在外婆家的院子里晒太阳。后来第一次离家去县城读书。",
            }
        ],
        ai_model="fake-model",
        ai_client=fake_ai,
    )

    assert copy is not None
    assert copy.book_title == "外婆慢慢说"
    assert copy.cover_subtitle == "关于童年与离家的记忆"
    assert copy.afterword.startswith("这一次先把已经讲出来的童年")
    assert copy.chapter_intros[0].chapter_id == "chapter-1"
    assert copy.chapter_intros[0].pull_quote == "我小时候常在外婆家的院子里晒太阳。"
    assert copy.chapter_intros[0].pull_quote_source_ref_id == "u1"
    assert copy.back_cover_quote == "我小时候常在外婆家的院子里晒太阳。"
    assert "chapter_id: chapter-1" in fake_ai.calls[0]["user_prompt"]
    assert "chapter_summary: 这一章围绕外婆家的院子和第一次离家的记忆展开。" in fake_ai.calls[0]["user_prompt"]
    assert "source_summaries: 讲述小时候在院子里晒太阳，也记得外婆忙碌的身影。" in fake_ai.calls[0]["user_prompt"]
    assert "representative_quotes: 我小时候常在外婆家的院子里晒太阳。" in fake_ai.calls[0]["user_prompt"]
    assert "source_ref_id: u1; quote: 我小时候常在外婆家的院子里晒太阳。" in fake_ai.calls[0]["user_prompt"]


async def test_memoir_book_title_suggestions_filter_internal_names():
    fake_ai = FakeTitleAI()
    suggestions = await generate_title_suggestions(
        title="外婆的回忆录",
        snapshot=[
            {
                "chapter_id": "chapter-1",
                "chapter_index": 1,
                "chapter_title": "外婆家的童年",
                "source_count": 1,
                "source_refs": [{"utterance_id": "u1", "quote": "我小时候常在外婆家的院子里晒太阳。"}],
                "content": "我小时候常在外婆家的院子里晒太阳。",
            }
        ],
        count=4,
        ai_model="fake-model",
        ai_client=fake_ai,
    )

    assert suggestions is not None
    assert suggestions.suggestions == ["外婆慢慢说", "外婆的一生", "从前的日子"]
    assert "一个人一生回忆" in fake_ai.calls[0]["user_prompt"]
    assert "不要只抓某个场景、物件、单章细节" in fake_ai.calls[0]["user_prompt"]


async def test_memoir_book_editorial_copy_filters_template_language():
    copy = await generate_manuscript_editorial_copy(
        title="外婆的回忆录",
        snapshot=[
            {
                "chapter_id": "chapter-1",
                "chapter_index": 1,
                "chapter_title": "外婆家的童年",
                "source_count": 1,
                "source_refs": [{"utterance_id": "u1", "quote": "我小时候常在外婆家的院子里晒太阳。"}],
                "content": "我小时候常在外婆家的院子里晒太阳。",
            }
        ],
        ai_model="fake-model",
        ai_client=FakeTemplateCopyAI(),
    )

    assert copy is not None
    assert copy.book_title == "外婆慢慢说"
    assert copy.cover_subtitle == "关于童年与离家的记忆"
    assert copy.preface == ""
    assert copy.afterword == ""
    assert copy.chapter_intros == []
    assert copy.back_cover_quote == ""


def test_memoir_book_manuscript_cleanup_removes_pdf_template_language():
    manuscript = {
        "profile": {
            "title": "我的小时候",
            "subtitle": "回望这一生",
            "narrator_name": "余晋",
            "prepared_by": "家人整理",
        },
        "front_matter": [
            {"kind": "cover", "title": "我的小时候", "content": "我的小时候\n回望这一生"},
            {"kind": "preface", "title": "写在前面", "content": "这本书记录的是「爷爷主题回忆录」中的生活。"},
        ],
        "chapters": [
            {
                "chapter_id": "chapter-1",
                "index": 1,
                "title": "我的小时候",
                "intro": "这一章主要记录小时候相关的回忆。",
                "pull_quotes": [{"text": "夏天的晚上，我就坐在院子里边看月亮。"}],
                "content": "夏天的晚上，我就坐在院子里边看月亮。那个院子里有一棵葡萄树。",
                "source_refs": [{"utterance_id": "u1", "quote": "夏天的晚上，我就坐在院子里边看月亮。"}],
            },
            {
                "chapter_id": "chapter-2",
                "index": 2,
                "title": "职场往事",
                "intro": "从口述内容看，这一章写工作。",
                "pull_quotes": [],
                "content": "工作占据了人生中很长的一段时间。",
                "source_refs": [{"utterance_id": "u2", "quote": "因为是家乡，所以一想起来成都，心里面就觉得很温暖、很踏实。"}],
            },
        ],
        "back_matter": {
            "afterword": {"kind": "afterword", "title": "后记", "content": "愿常翻看。"},
            "interview_records": {"kind": "interview_records", "title": "讲述资料", "content": "本版共整理 2 章正文，关联 2 条讲述来源。"},
            "version_note": {"kind": "version_note", "title": "版本说明", "content": "后台保留章节来源和口述素材"},
            "unconfirmed_facts": {"kind": "unconfirmed_facts", "title": "未确认信息", "content": ""},
            "source_appendix": {"kind": "source_appendix", "title": "章节来源", "content": "第1章：1 条讲述来源"},
        },
        "render_settings": {"back_cover_quote": "凡是过往皆是序章"},
    }

    cleaned = sanitize_manuscript_payload(title="我的小时候", manuscript=manuscript)

    assert cleaned["profile"]["title"] == "回望这一生"
    assert cleaned["profile"]["subtitle"] == "余晋口述回忆录"
    assert "爷爷主题回忆录" not in cleaned["front_matter"][1]["content"]
    assert "这一章主要记录" not in cleaned["chapters"][0]["intro"]
    assert "从口述内容看" not in cleaned["chapters"][1]["intro"]
    assert len(cleaned["back_matter"]["afterword"]["content"]) > 30
    assert cleaned["back_matter"]["interview_records"]["title"] == "口述片段"
    assert "讲述来源" not in cleaned["back_matter"]["interview_records"]["content"]
    assert cleaned["back_matter"]["version_note"]["content"] == "本书正文采用适合阅读的整理版表达。原始口述材料已保留，便于家人日后继续核对、补充和修订。"
    assert cleaned["back_matter"]["source_appendix"]["title"] == "口述片段"
    assert cleaned["render_settings"]["back_cover_quote"] == "因为是家乡，所以一想起来成都，心里面就觉得很温暖、很踏实。"


async def _create_confirmed_book_plan(test_db, *, project_id: str, confirmed_chapters: bool = True):
    draft_statuses = None
    if not confirmed_chapters:
        draft_statuses = [
            MemoirBookChapterDraftStatus.CONFIRMED,
            MemoirBookChapterDraftStatus.DRAFT,
        ]
    return await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        draft_contents=[
            "我小时候常在外婆家的院子里晒太阳。",
            "后来我第一次离家去县城读书，心里很紧张。",
        ],
        draft_statuses=draft_statuses,
    )


async def _add_output_source_link(
    test_db,
    *,
    project_id: str,
    plan_id: str,
    chapter_id: str,
    utterance_id: str,
    quote: str,
    text: str | None = None,
) -> None:
    source_text = text or quote
    session = MemoirVoiceSession(project_id=project_id)
    test_db.add(session)
    await test_db.flush()
    message = MemoirChatMessage(
        project_id=project_id,
        voice_session_id=session.id,
        role=MemoirChatMessageRole.USER.value,
        content_text=source_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
    )
    test_db.add(message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        id=utterance_id,
        voice_session_id=session.id,
        chat_message_id=message.id,
        transcript_raw=source_text,
        transcript_edited=source_text,
        start_ms=0,
        end_ms=8000,
    )
    test_db.add(utterance)
    await test_db.flush()
    await add_test_chapter_source_links(
        test_db,
        project_id=project_id,
        plan_id=plan_id,
        chapter_id=chapter_id,
        source_refs=[
            {
                "voice_session_id": session.id,
                "message_id": message.id,
                "utterance_id": utterance.id,
                "start_ms": 0,
                "end_ms": 8000,
                "quote": quote,
            }
        ],
    )


async def test_mobile_memoir_book_output_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-output/generate",
        json={},
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_output_requires_all_chapters_confirmed(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id, confirmed_chapters=False)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/generate",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "请先确认所有章节：第一次离家"


async def test_mobile_memoir_book_output_generate_combines_confirmed_chapters(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _add_output_source_link(
        test_db,
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapters[0].id,
        utterance_id="utterance-1",
        quote="我小时候常在外婆家的院子里晒太阳。",
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/generate",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "外婆的回忆录"
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
    assert structured["title"] == "外婆的回忆录"
    assert structured["chapter_count"] == 2
    assert [item["title"] for item in structured["table_of_contents"]] == ["外婆家的童年", "第一次离家"]
    assert structured["chapters"][0]["chapter_id"] == chapters[0].id
    assert structured["chapters"][0]["index"] == 1
    assert structured["chapters"][0]["content"] == "我小时候常在外婆家的院子里晒太阳。"
    manuscript = data["latest_version"]["manuscript"]
    assert manuscript["schema_version"] == 1
    assert manuscript["profile"]["template"] == "classic_family"
    assert manuscript["profile"]["title"] == "外婆的回忆录"
    assert manuscript["profile"]["subtitle"] == "关于外婆家的童年、第一次离家的记忆"
    assert [section["kind"] for section in manuscript["front_matter"]] == [
        "cover",
        "title_page",
        "generation_note",
        "preface",
    ]
    assert manuscript["front_matter"][2]["title"] == "整理说明"
    assert manuscript["front_matter"][3]["title"] == "写在前面"
    assert [item["title"] for item in manuscript["table_of_contents"]] == ["外婆家的童年", "第一次离家"]
    assert manuscript["chapters"][0]["chapter_id"] == chapters[0].id
    assert manuscript["chapters"][0]["intro"].startswith("记忆先回到外婆家的童年")
    assert "这一章主要记录" not in manuscript["chapters"][0]["intro"]
    assert "从口述内容看" not in manuscript["chapters"][0]["intro"]
    assert manuscript["chapters"][0]["pull_quotes"][0]["text"] == "我小时候常在外婆家的院子里晒太阳。"
    assert manuscript["chapters"][0]["source_refs"][0]["utterance_id"] == "utterance-1"
    assert manuscript["back_matter"]["interview_records"]["title"] == "口述片段"
    assert manuscript["back_matter"]["version_note"]["title"] == "版本与修订"
    assert manuscript["back_matter"]["source_appendix"]["title"] == "口述片段"
    assert "第1章「外婆家的童年」：1 条口述片段" in manuscript["back_matter"]["source_appendix"]["content"]
    assert manuscript["render_settings"]["back_cover_quote"] == "我小时候常在外婆家的院子里晒太阳。"
    assert manuscript["render_settings"]["page_size"] == "a5"

    stored_output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    stored_versions = (
        await test_db.execute(
            select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.output_id == stored_output.id)
        )
    ).scalars().all()
    assert stored_output.plan_id == plan.id
    assert len(stored_versions) == 1
    assert stored_versions[0].manuscript_json["profile"]["title"] == "外婆的回忆录"


async def test_mobile_memoir_book_output_generation_job_runs_in_background(client, test_db):
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/generate/jobs",
        json={"title": "外婆的回忆录", "force_regenerate": True},
    )

    assert response.status_code == 200
    event_data = response.json()
    assert event_data["event_type"] == MemoirAutomationEventType.BOOK_OUTPUT_GENERATED.value
    assert event_data["status"] == "pending"
    assert event_data["summary_text"] == "全书已提交后台生成。"
    job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_BOOK_OUTPUT.value,
        )
    )
    assert job is not None
    assert job.status == MemoirEditingJobStatus.PENDING.value
    assert job.payload_json["request"]["title"] == "外婆的回忆录"
    assert job.payload_json["automation_event_id"] == event_data["id"]

    assert await run_editing_job(test_db, job.id) is True

    refreshed_job = await test_db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.id == job.id))
    assert refreshed_job.status == MemoirEditingJobStatus.SUCCEEDED.value
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    assert output is not None
    version = await test_db.scalar(
        select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.output_id == output.id)
    )
    assert version is not None
    assert "第1章 外婆家的童年" in version.content
    event = await test_db.get(MemoirAutomationEvent, event_data["id"])
    assert event.status == "completed"
    assert event.summary_text == "全书已生成，可以预览或导出。"
    assert event.metadata_json["result_version_id"] == version.id


async def test_mobile_memoir_book_output_title_suggestions_use_fallback_without_ai(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/title-suggestions",
        json={"count": 6},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["suggestions"]) >= 3
    assert "外婆的回忆录" in data["suggestions"]
    assert "外婆口述回忆录" in data["suggestions"]
    assert "外婆口述的回忆录" not in data["suggestions"]
    assert all("外婆家的童年" not in title for title in data["suggestions"])
    assert all("主题" not in title for title in data["suggestions"])


async def test_mobile_memoir_book_output_update_title_and_cover_style(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    generated = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert generated.status_code == 200

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={
            "title": "外婆慢慢说",
            "subtitle": "一家人记得的旧日子",
            "narrator_name": "外婆",
            "prepared_by": "小林整理",
            "preface": "这本书从家里的讲述开始。",
            "afterword": "以后还可以继续补充。",
            "back_cover_quote": "我小时候常在院子里晒太阳。",
            "cover_style": "moonlit_courtyard",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "外婆慢慢说"
    latest = data["latest_version"]
    assert latest["content"].startswith("外婆慢慢说\n\n目录")
    assert latest["manuscript"]["profile"]["title"] == "外婆慢慢说"
    assert latest["manuscript"]["profile"]["subtitle"] == "一家人记得的旧日子"
    assert latest["manuscript"]["profile"]["narrator_name"] == "外婆"
    assert latest["manuscript"]["profile"]["prepared_by"] == "小林整理"
    assert latest["manuscript"]["render_settings"]["cover_style"] == "moonlit_courtyard"
    assert latest["manuscript"]["render_settings"]["back_cover_quote"] == "我小时候常在院子里晒太阳。"
    assert latest["manuscript"]["front_matter"][0]["title"] == "外婆慢慢说"
    assert latest["manuscript"]["front_matter"][1]["content"].startswith(
        "一家人记得的旧日子\n\n口述：外婆\n\n整理：小林整理\n\n"
    )
    assert "仅供家人保存与阅读" not in latest["manuscript"]["front_matter"][1]["content"]
    assert latest["manuscript"]["front_matter"][3]["content"] == "这本书从家里的讲述开始。"
    assert latest["manuscript"]["back_matter"]["afterword"]["content"] == "以后还可以继续补充。"


async def test_mobile_memoir_book_output_regenerates_editorial_copy_targets(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    chapters[0].metadata_json = {"chapter_summary": "这一章写外婆家的院子和童年生活。"}
    await _add_output_source_link(
        test_db,
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapters[0].id,
        utterance_id="u1",
        quote="我小时候常在外婆家的院子里晒太阳。",
        text="讲述小时候在院子里晒太阳，也记得外婆忙碌的身影。",
    )
    await test_db.commit()
    generated = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert generated.status_code == 200
    fake_ai = FakeEditorialAI()
    monkeypatch.setattr("app.services.memoir.book_manuscript_ai._text_ai_configured", lambda ai_model: True)
    monkeypatch.setattr("app.services.memoir.book_manuscript_ai.get_ai_service", lambda ai_model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/editorial-copy/regenerate",
        json={
            "ai_model": "fake-model",
            "targets": ["afterword", "chapter_intros", "pull_quotes", "back_cover_quote"],
            "chapter_ids": [chapters[0].id],
            "feedback": "更像家人整理后的书稿",
        },
    )

    assert response.status_code == 200
    manuscript = response.json()["latest_version"]["manuscript"]
    assert manuscript["back_matter"]["afterword"]["content"].startswith("这一次先把已经讲出来的童年")
    assert manuscript["render_settings"]["back_cover_quote"] == "我小时候常在外婆家的院子里晒太阳。"
    first_chapter = manuscript["chapters"][0]
    assert first_chapter["intro"] == "记忆先回到外婆家的院子，阳光和旧日子的声音都在那里。"
    assert first_chapter["pull_quotes"] == [
        {
            "text": "我小时候常在外婆家的院子里晒太阳。",
            "source_ref_ids": ["u1"],
            "source": "original_quote",
        }
    ]
    assert "本次生成目标：afterword、back_cover_quote、chapter_intros、pull_quotes" in fake_ai.calls[0]["user_prompt"]
    assert "用户本次反馈：更像家人整理后的书稿" in fake_ai.calls[0]["user_prompt"]
    assert "chapter_summary: 这一章写外婆家的院子和童年生活。" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_book_output_editorial_copy_generation_job_runs_in_background(
    client,
    test_db,
    monkeypatch,
):
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    chapters[0].metadata_json = {"chapter_summary": "这一章写外婆家的院子和童年生活。"}
    await _add_output_source_link(
        test_db,
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapters[0].id,
        utterance_id="u1",
        quote="我小时候常在外婆家的院子里晒太阳。",
        text="讲述小时候在院子里晒太阳。",
    )
    await test_db.commit()
    generated = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert generated.status_code == 200
    fake_ai = FakeEditorialAI()
    monkeypatch.setattr("app.services.memoir.book_manuscript_ai._text_ai_configured", lambda ai_model: True)
    monkeypatch.setattr("app.services.memoir.book_manuscript_ai.get_ai_service", lambda ai_model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/editorial-copy/regenerate/jobs",
        json={
            "ai_model": "fake-model",
            "targets": ["pull_quotes"],
            "chapter_ids": [chapters[0].id],
            "feedback": "只换章首引语",
        },
    )

    assert response.status_code == 200
    event_data = response.json()
    assert event_data["event_type"] == MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED.value
    assert event_data["status"] == "pending"
    assert event_data["summary_text"] == "章首引语已提交后台重生成。"
    job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY.value,
        )
    )
    assert job is not None
    assert job.status == MemoirEditingJobStatus.PENDING.value
    assert job.payload_json["request"]["targets"] == ["pull_quotes"]
    assert job.payload_json["request"]["chapter_ids"] == [chapters[0].id]
    assert job.payload_json["automation_event_id"] == event_data["id"]

    assert await run_editing_job(test_db, job.id) is True

    event = await test_db.get(MemoirAutomationEvent, event_data["id"])
    assert event.status == "completed"
    assert event.summary_text == "章首引语已重生成，可以预览或导出。"
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    version = await test_db.scalar(
        select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.output_id == output.id)
    )
    assert event.metadata_json["result_version_id"] == version.id
    assert version.manuscript_json["chapters"][0]["pull_quotes"] == [
        {
            "text": "我小时候常在外婆家的院子里晒太阳。",
            "source_ref_ids": ["u1"],
            "source": "original_quote",
        }
    ]
    assert "本次生成目标：pull_quotes" in fake_ai.calls[0]["user_prompt"]
    assert "用户本次反馈：只换章首引语" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_book_output_updates_chapter_editorial_copy(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _add_output_source_link(
        test_db,
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapters[0].id,
        utterance_id="u1",
        quote="那棵葡萄树我到现在都记得。",
    )
    await test_db.commit()
    generated = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert generated.status_code == 200

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output/chapters/{chapters[0].id}/editorial-copy",
        json={
            "intro": "记忆从院子里那棵葡萄树开始，慢慢带出一家人在夏天里的日子。",
            "pull_quote": "那棵葡萄树我到现在都记得。",
            "pull_quote_source_ref_id": "u1",
        },
    )

    assert response.status_code == 200
    chapter = response.json()["latest_version"]["manuscript"]["chapters"][0]
    assert chapter["intro"] == "记忆从院子里那棵葡萄树开始，慢慢带出一家人在夏天里的日子。"
    assert chapter["pull_quotes"] == [
        {
            "text": "那棵葡萄树我到现在都记得。",
            "source_ref_ids": ["u1"],
            "source": "manual",
        }
    ]

    invalid = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output/chapters/{chapters[0].id}/editorial-copy",
        json={"pull_quote": "不是本章来源。", "pull_quote_source_ref_id": "missing-source"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "章首引语来源不属于当前章节"


async def test_mobile_memoir_book_output_generate_returns_existing_without_force(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    first = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    second = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["latest_version"]["version"] == 1
    assert second.json()["is_stale"] is False


async def test_mobile_memoir_book_output_exports_txt_and_pdf(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
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
    assert "外婆的回忆录" in txt.text
    assert "第1章 外婆家的童年" in txt.text
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    assert b"/MediaBox [ 0 0 420 595 ]" in pdf.content
    assert b"/FontFile2" in pdf.content
    assert b"MemoirNotoSerifSC" in pdf.content
    assert b"NotoSansSC" in pdf.content
    assert b"/Helvetica" not in pdf.content
    assert b"STSong-Light" not in pdf.content
    assert b"Adobe-GB1" not in pdf.content
    assert pdf.content.count(b"/Type /Page") >= 8
    assert pdf_without_sources.status_code == 200
    assert pdf_without_sources.content != pdf.content
    assert print_pdf.status_code == 200
    assert b"/FontFile2" in print_pdf.content
    assert b"/Helvetica" not in print_pdf.content
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
    assert b"MemoirNotoSerifSC" in fallback_pdf.content
    assert b"/Helvetica" not in fallback_pdf.content
    assert b"STSong-Light" not in fallback_pdf.content
    assert b"Adobe-GB1" not in fallback_pdf.content


async def test_mobile_memoir_book_output_exports_selected_version(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
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
    project_id = await ensure_mobile_memoir_project(client)
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
    project_id = await ensure_mobile_memoir_project(client)
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
    project_id = await ensure_mobile_memoir_project(client)
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
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _add_output_source_link(
        test_db,
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapters[0].id,
        utterance_id="utterance-1",
        quote="第一章素材",
    )
    await _add_output_source_link(
        test_db,
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapters[1].id,
        utterance_id="utterance-2",
        quote="第二章素材一",
    )
    await _add_output_source_link(
        test_db,
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapters[1].id,
        utterance_id="utterance-3",
        quote="第二章素材二",
    )
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
    project_id = await ensure_mobile_memoir_project(client)
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
    assert "外婆的回忆录" in fake_tts.calls[0]["text"]
    assert fake_tts.calls[0]["voice"] == "voice-book"
    assert fake_tts.calls[0]["speed_ratio"] == 1.5
    assert len(data["audio_file_durations_seconds"]) == data["chunk_count"]
    assert data["alignment_provider"] == "fake-tts"
    assert data["alignment"][0]["text_start"] == 0
