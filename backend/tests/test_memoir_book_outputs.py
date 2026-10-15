import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.services.ai_base import AITextServiceBase
from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirOutputStatus,
    MemoirTranscriptPolishLevel,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirImageAsset,
    MemoirBookImagePlacement,
    MemoirSection,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.models.users import User
from app.services.memoir import book_outputs, book_output_export_cache
from app.services import content_storage
from app.services.memoir.book_manuscript_ai import generate_manuscript_editorial_copy, generate_title_suggestions
from app.services.memoir.book_manuscript_cleanup import sanitize_manuscript_payload
from app.services.memoir.book_audio import split_tts_text
from app.services.memoir.book_output_manuscript import (
    apply_editorial_copy,
    formal_book_title,
    manuscript_from_snapshot,
    updated_manuscript_payload,
)
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_tts_types import TTSResult
from tests.memoir_helpers import (
    add_test_section_materials,
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    run_test_editing_job,
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


class FakeEditorialAI(AITextServiceBase):
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
        source_ref_id = "M1"
        source_marker = "source_ref_id: "
        if source_marker in user_prompt:
            source_ref_id = (
                user_prompt.split(source_marker, 1)[1]
                .split(";", 1)[0]
                .strip()
            )
        response = f"""
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
              "pull_quote_source_ref_id": "{source_ref_id}"
            }}
          ],
          "back_cover_quote": "我小时候常在外婆家的院子里晒太阳。"
        }}
        """


        payload = json.loads(response)
        if '"pull_quote_source_ref_id"' not in user_prompt:
            payload['chapter_intros'][0].pop('pull_quote_source_ref_id')
        return json.dumps(payload, ensure_ascii=False)


class FakeTitleAI(AITextServiceBase):
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


class FakeTemplateCopyAI(AITextServiceBase):
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
              "pull_quote": "这句不是原始口述里的金句。"
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
        polish_level=MemoirTranscriptPolishLevel.LOW,
    )

    assert copy is not None
    assert copy.book_title == "外婆慢慢说"
    assert copy.cover_subtitle == "关于童年与离家的记忆"
    assert copy.afterword.startswith("这一次先把已经讲出来的童年")
    assert copy.chapter_intros[0].chapter_id == "chapter-1"
    assert copy.chapter_intros[0].pull_quote == "我小时候常在外婆家的院子里晒太阳。"
    assert copy.chapter_intros[0].pull_quote_source_ref_id == "u1"
    assert copy.back_cover_quote == "我小时候常在外婆家的院子里晒太阳。"
    assert "chapter_id: C1" in fake_ai.calls[0]["user_prompt"]
    assert "source_ref_id: M1; quote: 我小时候常在外婆家的院子里晒太阳。" in fake_ai.calls[0]["user_prompt"]
    assert "chapter-1" not in fake_ai.calls[0]["user_prompt"]
    assert "u1" not in fake_ai.calls[0]["user_prompt"]


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

    assert cleaned["profile"]["title"] == "我的小时候"
    assert cleaned["profile"]["subtitle"] == "回望这一生"
    assert "爷爷主题回忆录" not in cleaned["front_matter"][1]["content"]
    assert "这一章主要记录" not in cleaned["chapters"][0]["intro"]
    assert "从口述内容看" not in cleaned["chapters"][1]["intro"]
    assert len(cleaned["back_matter"]["afterword"]["content"]) > 30
    assert cleaned["back_matter"]["interview_records"]["title"] == "口述片段"
    assert "讲述来源" not in cleaned["back_matter"]["interview_records"]["content"]
    assert cleaned["back_matter"]["version_note"]["content"] == "本书正文采用适合阅读的整理版表达。原始口述材料已保留，便于家人日后继续核对、补充和修订。"
    assert cleaned["back_matter"]["source_appendix"]["title"] == "口述片段"
    assert cleaned["render_settings"]["back_cover_quote"] == "那个院子里有一棵葡萄树。"


@pytest.fixture
def release_quote_snapshot():
    # Fictional narration whose exported 81/46-character quotes ended mid-sentence.
    narration = (
        "这是发布测试用的虚构故事。我小时候住在四川成都，7岁那年，奶奶每天早晨带我去河边散步。"
        "有一天突然下大雨，奶奶把她的蓝色雨衣披在我身上，自己淋湿了。"
        "回到家以后，她给我煮了一碗热汤面。从那以后，我一直记得她照顾我的样子。"
    )
    return [{
        "chapter_id": "release-chapter",
        "chapter_title": "奶奶的蓝色雨衣",
        "content": narration,
        "source_refs": [{"utterance_id": "release-utterance", "transcript_version_id": "release-transcript", "quote": narration}],
    }]


def test_release_quotes_keep_complete_source_sentences(release_quote_snapshot):
    narration = release_quote_snapshot[0]["content"]
    manuscript = manuscript_from_snapshot(title="发布手测", content=narration, snapshot=release_quote_snapshot, polish_level=MemoirTranscriptPolishLevel.LOW)

    assert manuscript is not None
    quote = manuscript.chapters[0].pull_quotes[0]
    assert quote.text == narration[:73]
    assert quote.source == "original_quote"
    assert quote.source_ref_ids == ["release-utterance"]
    assert manuscript.chapters[0].source_refs[0].transcript_version_id == "release-transcript"
    assert manuscript.render_settings.back_cover_quote == "我小时候住在四川成都，7岁那年，奶奶每天早晨带我去河边散步。"

    payload = manuscript.model_dump(mode="json")
    payload["render_settings"]["back_cover_quote"] = ""
    regenerated = sanitize_manuscript_payload(title="发布手测", manuscript=payload)
    assert regenerated["render_settings"]["back_cover_quote"] == "我小时候住在四川成都，7岁那年，奶奶每天早晨带我去河边散步。"


def test_release_quote_skips_source_without_a_complete_sentence_in_budget(release_quote_snapshot):
    chapter = release_quote_snapshot[0]
    chapter["source_refs"][0]["quote"] = chapter["content"].replace("。", "，")
    chapter["source_refs"].append({"utterance_id": "short-source", "quote": "回到家以后，她给我煮了一碗热汤面。"})
    manuscript = manuscript_from_snapshot(title="发布手测", content=chapter["content"], snapshot=release_quote_snapshot, polish_level=MemoirTranscriptPolishLevel.LOW)

    assert manuscript is not None
    quote = manuscript.chapters[0].pull_quotes[0]
    assert quote.text == "回到家以后，她给我煮了一碗热汤面。"
    assert quote.source_ref_ids == ["short-source"]


def test_release_quote_metadata_update_preserves_manual_text(release_quote_snapshot):
    narration = release_quote_snapshot[0]["content"]
    old_chapter_quote = narration[:80].rstrip("，。；、,. ") + "。"
    old_back_cover_quote = narration[:45].rstrip("，。；、,. ") + "。"
    assert len(old_chapter_quote) == 81
    assert len(old_back_cover_quote) == 46
    manuscript = manuscript_from_snapshot(
        title="发布手测", content=narration, snapshot=release_quote_snapshot,
        metadata={"back_cover_quote": old_back_cover_quote},
    )
    assert manuscript is not None
    manuscript.chapters[0].pull_quotes[0].text = old_chapter_quote
    manuscript.chapters[0].pull_quotes[0].source = "manual"
    updated = updated_manuscript_payload(
        title="发布手测", stored=manuscript.model_dump(mode="json"), content=narration,
        snapshot=release_quote_snapshot, cover_style_value=None, metadata_patch={"subtitle": "自定副标题"},
    )

    assert updated["chapters"][0]["pull_quotes"][0]["text"] == old_chapter_quote
    assert updated["chapters"][0]["pull_quotes"][0]["source"] == "manual"
    assert updated["render_settings"]["back_cover_quote"] == old_back_cover_quote


async def test_release_quote_regeneration_keeps_complete_sentences_and_selected_targets(release_quote_snapshot, monkeypatch):
    narration = release_quote_snapshot[0]["content"]
    fake_ai = FakeEditorialAI()
    excerpt = "回到家以后，她给我煮了一碗热汤面。"

    async def provider_response(*args, **kwargs):
        return json.dumps({
            "chapter_intros": [{"chapter_id": "C1", "pull_quote": excerpt}],
            "back_cover_quote": excerpt,
        }, ensure_ascii=False)

    monkeypatch.setattr(fake_ai, "_call_llm", provider_response)
    copy = await generate_manuscript_editorial_copy(
        title="发布手测", snapshot=release_quote_snapshot, ai_model="fake-model", ai_client=fake_ai,
        targets=["pull_quotes", "back_cover_quote"],
    )
    assert copy is not None
    assert copy.chapter_intros[0].pull_quote == excerpt
    assert copy.chapter_intros[0].pull_quote_source_ref_id == ""
    assert copy.back_cover_quote == excerpt

    manuscript = manuscript_from_snapshot(title="发布手测", content=narration, snapshot=release_quote_snapshot)
    assert manuscript is not None
    manuscript.chapters[0].pull_quotes[0].text = "手工保留的摘录"
    manuscript.chapters[0].pull_quotes[0].source = "manual"
    apply_editorial_copy(manuscript, copy, targets={"back_cover_quote"}, chapter_ids=set())
    assert manuscript.render_settings.back_cover_quote == excerpt
    assert manuscript.chapters[0].pull_quotes[0].text == "手工保留的摘录"
    apply_editorial_copy(manuscript, copy, targets={"pull_quotes"}, chapter_ids={"release-chapter"})
    assert manuscript.chapters[0].pull_quotes[0].text == excerpt
    assert manuscript.chapters[0].pull_quotes[0].source_ref_ids == []




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
    await add_test_section_materials(
        test_db,
        project_id=project_id,
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


async def _seed_confirmed_book_output(test_db, *, project_id: str) -> MemoirBookOutputVersion:
    plan = await test_db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
        .execution_options(populate_existing=True)
    )
    assert plan is not None
    chapter_versions = [
        (chapter, chapter.draft.versions[-1])
        for chapter in sorted(plan.chapters, key=lambda item: item.sort_order)
        if chapter.draft is not None and chapter.draft.versions
    ]
    assert len(chapter_versions) == len(plan.chapters)
    source_refs = await plan_source_ref_dicts_by_chapter(test_db, plan)
    title = formal_book_title(plan.title or "我的回忆录")
    content, snapshot = book_outputs._compose_book_content(
        title=title,
        chapters=chapter_versions,
        source_refs_by_chapter=source_refs,
    )
    output = await test_db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    if output is None:
        output = MemoirBookOutput(
            project_id=project_id,
            plan_id=plan.id,
            title=title,
            status=MemoirOutputStatus.DRAFT.value,
            metadata_json={"source": "confirmed_draft_projection"},
        )
        test_db.add(output)
        await test_db.flush()
    version_number = int(
        await test_db.scalar(
            select(func.max(MemoirBookOutputVersion.version)).where(
                MemoirBookOutputVersion.output_id == output.id
            )
        )
        or 0
    ) + 1
    manuscript = manuscript_from_snapshot(
        title=title,
        content=content,
        snapshot=snapshot,
        metadata={"prepared_by": "家人整理"},
    )
    assert manuscript is not None
    version = MemoirBookOutputVersion(
        output_id=output.id,
        project_id=project_id,
        plan_id=plan.id,
        version=version_number,
        content=content,
        chapter_snapshot_json=snapshot,
        manuscript_json=manuscript.model_dump(mode="json"),
        created_by=MemoirCreatedBy.SYSTEM.value,
    )
    test_db.add(version)
    await test_db.commit()
    return version


async def test_release_quote_normal_assembly_repairs_reused_original_only(client, test_db, release_quote_snapshot):
    narration = release_quote_snapshot[0]["content"]
    old_quote = narration[:80].rstrip("，。；、,. ") + "。"
    old_back_cover = narration[:45].rstrip("，。；、,. ") + "。"
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _add_output_source_link(
        test_db, project_id=project_id, plan_id=plan.id, chapter_id=chapters[0].id,
        utterance_id="release-utterance", quote=narration,
    )
    await test_db.commit()
    previous = await _seed_confirmed_book_output(test_db, project_id=project_id)
    payload = json.loads(json.dumps(previous.manuscript_json))
    payload["chapters"][0]["pull_quotes"] = [
        {"text": old_quote, "source": source, "source_ref_ids": ["release-utterance"]}
        for source in ("original_quote", "manual", "editorial_generated")
    ]
    payload["render_settings"]["back_cover_quote"] = old_back_cover
    previous.manuscript_json = payload
    draft_version = await test_db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.chapter_id == chapters[0].id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
    )
    assert draft_version is not None
    draft_version.content += "正常整理发布测试说明。"
    await test_db.commit()

    assembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert assembled.status_code == 200
    current = await test_db.get(MemoirBookOutputVersion, assembled.json()["output_version_id"])
    assert current is not None and current.id != previous.id
    quotes = current.manuscript_json["chapters"][0]["pull_quotes"]
    assert [quote["text"] for quote in quotes] == [old_quote]
    assert [quote["source"] for quote in quotes] == ["manual"]
    assert current.manuscript_json["render_settings"]["back_cover_quote"] == "正常整理发布测试说明。"
    assert previous.manuscript_json["chapters"][0]["pull_quotes"][0]["text"] == old_quote


async def test_mobile_memoir_book_output_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/memoir/projects/project-1/book-output/assemble",
        json={},
    )

    assert response.status_code == 401


@pytest.mark.ci_smoke
async def test_admin_reads_other_users_memoir_book_without_write_access(client, test_db):
    narration = "我小时候常在外婆家的院子里晒太阳。"
    material = await create_test_voice_transcript(client, test_db, narration)
    project_id = material["session"]["project_id"]
    plan, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        title="外婆的回忆录",
        chapter_titles=["外婆家的童年"],
        source_refs_by_chapter=[[memoir_source_ref(material)]],
        draft_contents=[narration],
    )
    version = await _seed_confirmed_book_output(test_db, project_id=project_id)
    base = f"/api/mobile/memoir/projects/{project_id}"
    paths = {
        "plan": f"{base}/book-plan",
        "draft": f"{base}/book-plan/chapters/{chapters[0].id}/draft",
        "output": f"{base}/book-output",
        "version": f"{base}/book-output/versions/{version.id}",
        "export": f"{base}/book-output/export?format=txt&version_id={version.id}",
        "export_info": f"{base}/book-output/export/info?format=txt&version_id={version.id}",
    }
    owner_responses = {}
    for key, path in paths.items():
        response = await client.get(path)
        assert response.status_code == 200, response.text
        owner_responses[key] = response

    assert owner_responses["plan"].json()["id"] == plan.id
    section = owner_responses["plan"].json()["chapters"][0]["sections"][0]
    assert section["materials"][0]["utterance_id"] == material["utterance_id"]
    draft_section = owner_responses["draft"].json()["latest_version"]["sections"][0]
    assert draft_section["section_id"] == section["id"]
    assert draft_section["content"] == narration
    assert owner_responses["output"].json()["latest_version"]["id"] == version.id
    assert owner_responses["version"].json()["id"] == version.id
    assert narration in owner_responses["export"].text
    assert "attachment" in owner_responses["export"].headers["content-disposition"]

    registration = await client.post(
        "/api/auth/register",
        json={"email": "other-reader@example.com", "password": "password123"},
    )
    assert registration.status_code == 201
    reader = await test_db.get(User, registration.json()["user"]["id"])
    assert reader is not None and reader.role == "user"
    for path in paths.values():
        response = await client.get(path)
        assert response.status_code == 404, response.text

    reader.role = "admin"
    await test_db.commit()
    for key, path in paths.items():
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert response.content == owner_responses[key].content

    for path, payload in (
        (
            f"{base}/book-plan",
            {
                "title": "管理员尝试修改",
                "chapters": [{"id": chapters[0].id, "title": chapters[0].title}],
            },
        ),
        (f"{base}/book-output", {"title": "管理员尝试修改"}),
    ):
        response = await client.patch(path, json=payload)
        assert response.status_code == 404, response.text


async def test_book_output_requires_structured_small_sections(test_db, client):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(
        test_db,
        project_id=project_id,
    )
    plan_id = plan.id
    first_chapter_title = chapters[0].title
    version = await test_db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.chapter_id == chapters[0].id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
    )
    assert version is not None
    version.section_snapshot_json = []
    await test_db.commit()
    plan = await test_db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.id == plan_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
        .execution_options(populate_existing=True)
    )
    assert plan is not None

    with pytest.raises(HTTPException) as error:
        book_outputs.readable_chapter_versions(plan)

    assert error.value.status_code == 409
    assert "还没有可读正文" in error.value.detail
    assert first_chapter_title in error.value.detail


async def test_mobile_memoir_book_output_serializes_confirmed_chapter_projection(client, test_db):
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

    version = await _seed_confirmed_book_output(test_db, project_id=project_id)
    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")

    assert response.status_code == 200
    data = response.json()
    assert "metadata_json" not in data
    assert data["title"] == "外婆的回忆录"
    assert data["latest_version"]["version"] == 1
    assert data["versions"] == [
        {
            "id": version.id,
            "version": 1,
            "created_by": "system",
            "created_at": version.created_at.isoformat(),
        }
    ]
    assert "content" not in data["versions"][0]
    assert "chapter_snapshot_json" not in data["latest_version"]
    assert "structured_content" not in data["latest_version"]
    content = data["latest_version"]["content"]
    assert "目录\n1. 外婆家的童年\n  1.1 外婆家的童年\n2. 第一次离家" in content
    assert "第1章 外婆家的童年" in content
    assert "\n\n外婆家的童年\n\n我小时候常在外婆家的院子里晒太阳。" in content
    assert "我小时候常在外婆家的院子里晒太阳。" in content
    assert "第2章 第一次离家" in content
    assert "后来我第一次离家去县城读书，心里很紧张。" in content
    manuscript = data["latest_version"]["manuscript"]
    assert manuscript["schema_version"] == 5
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
    assert manuscript["chapters"][0]["sections"][0]["title"] == "外婆家的童年"
    assert manuscript["chapters"][0]["intro"].startswith("记忆先回到外婆家的童年")
    assert "这一章主要记录" not in manuscript["chapters"][0]["intro"]
    assert "从口述内容看" not in manuscript["chapters"][0]["intro"]
    assert manuscript["chapters"][0]["pull_quotes"] == []
    assert manuscript["chapters"][0]["source_refs"][0]["utterance_id"] == "utterance-1"
    assert manuscript["back_matter"]["interview_records"]["title"] == "口述片段"
    assert manuscript["back_matter"]["version_note"]["title"] == "版本与修订"
    assert manuscript["back_matter"]["source_appendix"]["title"] == "口述片段"
    assert "第1章「外婆家的童年」：1 条口述片段" in manuscript["back_matter"]["source_appendix"]["content"]
    assert manuscript["render_settings"]["back_cover_quote"] != "我小时候常在外婆家的院子里晒太阳。"
    assert manuscript["render_settings"]["page_size"] == "a5"

    version_response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/versions/{version.id}"
    )
    assert version_response.status_code == 200
    version_data = version_response.json()
    assert version_data["id"] == version.id
    assert version_data["content"] == content
    assert version_data["manuscript"]["chapters"][0]["chapter_id"] == chapters[0].id
    assert "chapter_snapshot_json" not in version_data
    assert "structured_content" not in version_data

    missing_version_response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/versions/missing"
    )
    assert missing_version_response.status_code == 404

    stored_output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    stored_versions = (
        await test_db.execute(
            select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.output_id == stored_output.id)
        )
    ).scalars().all()
    assert stored_output.plan_id == plan.id
    assert len(stored_versions) == 1
    assert stored_versions[0].manuscript_json["profile"]["title"] == "外婆的回忆录"


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
    assert "测试的回忆录" in data["suggestions"]
    assert "测试口述回忆录" in data["suggestions"]
    assert "测试口述的回忆录" not in data["suggestions"]
    assert all("外婆家的童年" not in title for title in data["suggestions"])
    assert all("主题" not in title for title in data["suggestions"])


async def test_mobile_memoir_book_output_update_title_and_cover_style(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)

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
    project = await test_db.get(Project, project_id)
    assert project is not None
    assert project.title == "外婆慢慢说"


async def test_book_manuscript_design_saves_and_preview_does_not_change_selection(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)

    selected = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={
            "manuscript_template": "warm_journal",
            "cover_style": "navy_gilt",
            "custom_layout": {
                "font_family": "sans",
                "font_scale": "large",
                "line_spacing": "relaxed",
                "page_margin": "wide",
            },
        },
    )
    assert selected.status_code == 200
    version = selected.json()["latest_version"]
    assert version["manuscript"]["schema_version"] == 5
    assert version["manuscript"]["profile"]["template"] == "warm_journal"
    assert version["manuscript"]["render_settings"]["custom_layout"] == {
        "font_family": "sans",
        "font_scale": "large",
        "line_spacing": "relaxed",
        "page_margin": "wide",
    }

    rendered_designs = []
    render_export = book_outputs.render_book_output_export

    def capture_rendered_design(**kwargs):
        rendered_designs.append(kwargs["manuscript"])
        return render_export(**kwargs)

    monkeypatch.setattr(book_outputs, "render_book_output_export", capture_rendered_design)
    preview = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export"
        "?format=pdf&preview_template=photo_memory"
    )
    custom_preview = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export"
        "?format=pdf&preview_font_family=serif&preview_font_scale=standard"
        "&preview_line_spacing=compact&preview_page_margin=narrow"
    )
    current = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")

    assert preview.status_code == 200
    assert custom_preview.status_code == 200
    assert preview.content.startswith(b"%PDF-")
    assert custom_preview.content.startswith(b"%PDF-")
    adjusted = rendered_designs[-1]
    assert adjusted["profile"]["template"] == "warm_journal"
    assert adjusted["render_settings"]["cover_style"] == "navy_gilt"
    assert adjusted["render_settings"]["custom_layout"]["font_family"] == "serif"
    assert adjusted["chapters"] == version["manuscript"]["chapters"]
    assert current.status_code == 200
    assert current.json()["latest_version"]["id"] == version["id"]
    assert current.json()["latest_version"]["manuscript"]["profile"]["template"] == "warm_journal"
    assert current.json()["latest_version"]["manuscript"]["render_settings"]["custom_layout"] == (
        version["manuscript"]["render_settings"]["custom_layout"]
    )

    saved = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"custom_layout": {"font_family": "serif", "font_scale": "large"}},
    )
    assert saved.status_code == 200
    saved_manuscript = saved.json()["latest_version"]["manuscript"]
    assert saved_manuscript["profile"]["template"] == "warm_journal"
    assert saved_manuscript["render_settings"]["cover_style"] == "navy_gilt"
    assert saved_manuscript["render_settings"]["custom_layout"]["font_family"] == "serif"
    assert saved_manuscript["chapters"] == version["manuscript"]["chapters"]


async def test_new_manuscript_templates_pair_covers_without_saving_preview(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    cover = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"cover_style": "navy_gilt"},
    )
    assert cover.status_code == 200
    current_version = cover.json()["latest_version"]
    render_export = book_outputs.render_book_output_export
    rendered_designs = []

    def capture_rendered_design(**kwargs):
        manuscript = kwargs["manuscript"]
        rendered_designs.append(
            (manuscript["profile"]["template"], manuscript["render_settings"]["cover_style"])
        )
        return render_export(**kwargs)

    monkeypatch.setattr(book_outputs, "render_book_output_export", capture_rendered_design)

    for template in ("heritage_classic", "warm_journal", "modern_minimal"):
        preview = await client.get(
            f"/api/mobile/memoir/projects/{project_id}/book-output/export"
            f"?format=pdf&preview_template={template}"
        )
        after_preview = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
        assert preview.status_code == 200
        assert preview.content.startswith(b"%PDF-")
        assert rendered_designs[-1] == (template, template)
        assert after_preview.status_code == 200
        assert after_preview.json()["latest_version"]["id"] == current_version["id"]
        assert after_preview.json()["latest_version"]["manuscript"] == current_version["manuscript"]

        selected = await client.patch(
            f"/api/mobile/memoir/projects/{project_id}/book-output",
            json={"manuscript_template": template},
        )
        assert selected.status_code == 200
        current_version = selected.json()["latest_version"]
        assert current_version["manuscript"]["profile"]["template"] == template
        assert current_version["manuscript"]["render_settings"]["cover_style"] == template

    manual_cover = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"cover_style": "navy_gilt"},
    )
    assert manual_cover.status_code == 200
    assert manual_cover.json()["latest_version"]["manuscript"]["profile"]["template"] == "modern_minimal"
    assert manual_cover.json()["latest_version"]["manuscript"]["render_settings"]["cover_style"] == "navy_gilt"

    reselected = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"manuscript_template": "modern_minimal"},
    )
    assert reselected.status_code == 200
    assert reselected.json()["latest_version"]["manuscript"]["render_settings"]["cover_style"] == "modern_minimal"

    explicit_cover = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"manuscript_template": "heritage_classic", "cover_style": "navy_gilt"},
    )
    assert explicit_cover.status_code == 200
    assert explicit_cover.json()["latest_version"]["manuscript"]["profile"]["template"] == "heritage_classic"
    assert explicit_cover.json()["latest_version"]["manuscript"]["render_settings"]["cover_style"] == "navy_gilt"

    old_template = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"manuscript_template": "photo_memory"},
    )
    assert old_template.status_code == 200
    assert old_template.json()["latest_version"]["manuscript"]["render_settings"]["cover_style"] == "navy_gilt"


async def test_book_manuscript_design_rejects_unsupported_values(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)

    update = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"manuscript_template": "unknown"},
    )
    preview = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export"
        "?preview_template=custom&preview_page_margin=extreme"
    )

    assert update.status_code == 422
    assert preview.status_code == 422

    invalid_font = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"custom_layout": {"font_family": "kai"}},
    )
    invalid_preview_font = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?preview_font_family=kai"
    )
    assert invalid_font.status_code == 422
    assert invalid_preview_font.status_code == 422


async def test_book_manuscript_design_requires_structured_manuscript(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    version = await _seed_confirmed_book_output(test_db, project_id=project_id)
    version.content = "已有的纯文字正文"
    version.chapter_snapshot_json = []
    version.manuscript_json = {}
    await test_db.commit()

    update = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"manuscript_template": "photo_memory"},
    )
    preview = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export"
        "?format=pdf&preview_template=photo_memory"
    )

    assert update.status_code == 400
    assert preview.status_code == 400
    assert update.json()["detail"] == "请先更新书稿，再选择版式"
    assert preview.json()["detail"] == "请先更新书稿，再预览版式"


async def test_book_manuscript_design_survives_new_book_assembly(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    project = await test_db.get(Project, project_id)
    image = MemoirImageAsset(project_id=project_id, user_id=project.user_id,
        status="ready", source_type="manual_upload", source_signature="uploaded-cover",
        storage_path="projects/cover.jpg", width=600, height=800)
    test_db.add(image)
    await test_db.flush()
    test_db.add(MemoirBookImagePlacement(project_id=project_id, image_asset_id=image.id, placement_type="cover", enabled=True))
    await test_db.commit()
    selected = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={
            "manuscript_template": "warm_journal",
            "custom_layout": {
                "font_family": "sans",
                "font_scale": "large",
                "line_spacing": "compact",
                "page_margin": "narrow",
            },
        },
    )
    assert selected.status_code == 200

    draft = await test_db.scalar(
        select(MemoirBookChapterDraft)
        .where(MemoirBookChapterDraft.chapter_id == chapters[0].id)
        .options(selectinload(MemoirBookChapterDraft.versions))
    )
    assert draft is not None
    previous_draft_version = draft.versions[-1]
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapters[0].id,
            version=previous_draft_version.version + 1,
            content=previous_draft_version.content,
            section_snapshot_json=previous_draft_version.section_snapshot_json,
            material_snapshot_json=previous_draft_version.material_snapshot_json,
            used_material_ids_json=previous_draft_version.used_material_ids_json,
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()

    assembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert assembled.status_code == 200
    current = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert current.status_code == 200
    assert current.json()["latest_version"]["id"] == assembled.json()["output_version_id"]
    manuscript = current.json()["latest_version"]["manuscript"]
    assert manuscript["profile"]["template"] == "warm_journal"
    assert manuscript["render_settings"]["cover_image"]["image_asset_id"] == image.id
    assert manuscript["render_settings"]["custom_layout"] == {
        "font_family": "sans",
        "font_scale": "large",
        "line_spacing": "compact",
        "page_margin": "narrow",
    }


async def test_first_book_assembly_starts_with_warm_journal(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)

    assembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert assembled.status_code == 200
    manuscript = (await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")).json()["latest_version"]["manuscript"]
    assert (manuscript["profile"]["template"], manuscript["render_settings"]["cover_style"]) == (
        "warm_journal",
        "warm_journal",
    )


async def test_project_title_update_replaces_existing_book_output_title(client, test_db):
    project_id = await ensure_mobile_memoir_project(client, title="旧项目名称")
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}",
        json={"title": "我的故事"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "我的故事"
    output_response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
    )
    assert output_response.status_code == 200
    output = output_response.json()
    assert output["title"] == "我的故事"
    assert output["latest_version"]["content"].startswith("我的故事\n\n目录")
    assert output["latest_version"]["manuscript"]["profile"]["title"] == "我的故事"


async def test_unchanged_titles_keep_book_version(client, test_db):
    title = formal_book_title("外婆回忆录章节规划")
    project_id = await ensure_mobile_memoir_project(client, title=title)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    primed = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output", json={"title": title}
    )
    assert primed.status_code == 200, primed.text
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    assert output is not None
    latest_id = await test_db.scalar(
        select(MemoirBookOutputVersion.id)
        .where(MemoirBookOutputVersion.output_id == output.id)
        .order_by(MemoirBookOutputVersion.version.desc())
        .limit(1)
    )

    project_update = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}", json={"title": title}
    )
    assert project_update.status_code == 200, project_update.text
    book_update = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output", json={"title": title}
    )
    assert book_update.status_code == 200, book_update.text
    current_id = await test_db.scalar(
        select(MemoirBookOutputVersion.id)
        .where(MemoirBookOutputVersion.output_id == output.id)
        .order_by(MemoirBookOutputVersion.version.desc())
        .limit(1)
    )
    assert current_id == latest_id


async def test_project_title_updates_book_output_before_first_version(client, test_db):
    project_id = await ensure_mobile_memoir_project(client, title="旧名称")
    plan, _ = await _create_confirmed_book_plan(test_db, project_id=project_id)
    output = MemoirBookOutput(
        project_id=project_id,
        plan_id=plan.id,
        title="旧名称",
        status=MemoirOutputStatus.DRAFT.value,
    )
    test_db.add(output)
    await test_db.commit()

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}", json={"title": "新的名称"}
    )
    assert response.status_code == 200, response.text
    await test_db.refresh(output)
    assert output.title == "新的名称"


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
        text="我小时候常在外婆家的院子里晒太阳。",
    )
    await test_db.commit()
    await _seed_confirmed_book_output(test_db, project_id=project_id)
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
    assert manuscript["render_settings"]["back_cover_quote"] != "我小时候常在外婆家的院子里晒太阳。"
    first_chapter = manuscript["chapters"][0]
    assert first_chapter["intro"] == "记忆先回到外婆家的院子，阳光和旧日子的声音都在那里。"
    assert first_chapter["pull_quotes"] == []
    assert "本次生成目标：afterword、back_cover_quote、chapter_intros、pull_quotes" in fake_ai.calls[0]["user_prompt"]
    assert "用户本次反馈：更像家人整理后的书稿" in fake_ai.calls[0]["user_prompt"]
    assert "这一章写外婆家的院子和童年生活。" not in fake_ai.calls[0]["user_prompt"]


async def test_editorial_copy_explains_empty_book_after_chapters_are_withheld(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    plan, _ = await _create_confirmed_book_plan(test_db, project_id=project_id)
    previous = await _seed_confirmed_book_output(test_db, project_id=project_id)
    test_db.add(MemoirBookOutputVersion(
        output_id=previous.output_id,
        project_id=project_id,
        plan_id=plan.id,
        version=previous.version + 1,
        content="我的回忆录",
        chapter_snapshot_json=[],
        manuscript_json={},
        created_by=MemoirCreatedBy.SYSTEM.value,
    ))
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/editorial-copy/regenerate",
        json={"targets": ["preface"]},
    )
    assert response.status_code == 409
    assert "待确认事项" in response.json()["detail"]
    assert "后台更新书稿后再重写" in response.json()["detail"]


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
        text="我小时候常在外婆家的院子里晒太阳。",
    )
    await test_db.commit()
    await _seed_confirmed_book_output(test_db, project_id=project_id)
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

    assert await run_test_editing_job(test_db, job.id) is True

    event = await test_db.get(MemoirAutomationEvent, event_data["id"])
    assert event.status == "completed"
    assert event.summary_text == "章首引语已重生成，可以预览或导出。"
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    version = await test_db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.output_id == output.id)
        .order_by(MemoirBookOutputVersion.version.desc())
    )
    assert event.metadata_json["result_version_id"] == version.id
    assert version.manuscript_json["chapters"][0]["pull_quotes"] == []
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
    await _seed_confirmed_book_output(test_db, project_id=project_id)

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


@pytest.fixture(autouse=True)
def isolate_export_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(content_storage, "PROJECTS_DIR", tmp_path / "projects")


async def test_mobile_memoir_book_output_exports_txt_and_pdf(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)

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


async def test_book_output_export_deadline_returns_retryable_timeout(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)

    def slow_export(**_kwargs):
        import time

        time.sleep(0.05)
        return book_outputs.ExportFile("book.pdf", "application/pdf", b"%PDF-test\n%%EOF\n")

    monkeypatch.setattr(book_outputs, "render_book_output_export", slow_export)

    prepared = await book_outputs.prepare_book_output_export(
        test_db, user_id=(await test_db.get(Project, project_id)).user_id,
        project_id=project_id, export_format=book_outputs.MemoirBookOutputExportFormat.PDF,
    )
    with pytest.raises(HTTPException) as exc_info:
        await book_outputs.export_book_output_file(prepared, timeout_seconds=0.01)

    assert exc_info.value.status_code == 504
    assert "请稍后重试" in str(exc_info.value.detail)
    await asyncio.sleep(0.06)


async def test_mobile_memoir_book_output_exports_selected_version(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    first_version = await _seed_confirmed_book_output(test_db, project_id=project_id)
    second = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-output",
        json={"title": "外婆慢慢说"},
    )
    assert second.status_code == 200
    assert second.json()["latest_version"]["version"] == first_version.version + 1

    exported = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=txt&version_id={first_version.id}"
    )
    unchanged = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/versions/{first_version.id}"
    )

    assert exported.status_code == 200
    assert "我小时候常在外婆家的院子里晒太阳。" in exported.text
    assert exported.text.startswith("外婆的回忆录")
    assert unchanged.status_code == 200
    assert unchanged.json()["content"] == first_version.content
    assert unchanged.json()["manuscript"] == first_version.manuscript_json


async def test_partial_book_export_omits_empty_chapters_until_material_arrives(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["有讲述的章节", "尚未讲述的章节一", "尚未讲述的章节二"],
        draft_contents=["这是用于发布回归的虚构正文。", None, None],
    )
    await _add_output_source_link(
        test_db, project_id=project_id, plan_id=plan.id,
        chapter_id=chapters[0].id,
        utterance_id="aa562d9f-393b-4272-b7f8-8f329bedc5a2",
        quote="这是用于发布回归的虚构讲述。",
    )
    await test_db.commit()
    loaded = await book_outputs.load_book_plan_drafts(test_db, project_id=project_id)
    chapter = next(c for c in loaded.chapters if c.id == chapters[0].id)
    refs = await plan_source_ref_dicts_by_chapter(test_db, loaded)
    content, snapshot = book_outputs._compose_book_content(
        title=plan.title, chapters=[(chapter, chapter.draft.versions[-1])],
        source_refs_by_chapter=refs,
    )
    output = MemoirBookOutput(project_id=project_id, plan_id=plan.id, title=plan.title)
    test_db.add(output)
    await test_db.flush()
    output_version = MemoirBookOutputVersion(
        output_id=output.id, project_id=project_id, plan_id=plan.id,
        version=1, content=content, chapter_snapshot_json=snapshot,
    )
    test_db.add(output_version)
    chapters[1].title = "改名后的空章"
    await test_db.commit()

    current = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert current.status_code == 200
    assert current.json()["is_stale"] is False
    assert current.json()["stale_chapter_titles"] == []
    assert current.json()["latest_version"]["content"] == content
    exported = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=txt"
    )
    assert exported.status_code == 200

    await _add_output_source_link(
        test_db, project_id=project_id, plan_id=plan.id,
        chapter_id=chapters[1].id,
        utterance_id="0e6a58f5-9c27-4db5-a87c-28c401a72f89",
        quote="这是刚补进另一章的虚构讲述。",
    )
    await test_db.commit()
    changed = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert changed.status_code == 200
    assert changed.json()["is_stale"] is True
    assert changed.json()["stale_chapter_titles"] == [chapters[1].title]
    assert changed.json()["latest_version"]["id"] == output_version.id


async def test_mobile_memoir_book_output_detects_stale_chapter_versions(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)

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

async def test_mobile_memoir_book_output_detects_reordered_chapters(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)

    reordered = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "move_chapter", "chapter_id": chapters[1].id, "direction": "up"},
    )
    assert reordered.status_code == 200

    stale = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert stale.status_code == 200
    stale_data = stale.json()
    assert stale_data["is_stale"] is True
    assert stale_data["stale_chapter_titles"] == ["第一次离家", "外婆家的童年"]


async def test_mobile_memoir_book_output_excludes_hidden_chapter_and_invalidates_formal_output(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(
        test_db,
        project_id=project_id,
        confirmed_chapters=False,
    )
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    output = await test_db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    assert output is not None
    output.status = MemoirOutputStatus.ACCEPTED.value
    await test_db.commit()

    hidden = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "set_chapter_book_inclusion",
            "chapter_id": chapters[1].id,
            "include_in_book": False,
        },
    )
    assert hidden.status_code == 200, hidden.text
    await test_db.refresh(output)
    assert output.status == MemoirOutputStatus.DRAFT.value

    plan = await test_db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
        .execution_options(populate_existing=True)
    )
    assert plan is not None
    included = book_outputs.readable_chapter_versions(plan)
    assert [chapter.id for chapter, _ in included] == [chapters[0].id]

    stale = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert stale.status_code == 200
    assert stale.json()["is_stale"] is True
    assert stale.json()["stale_chapter_titles"] == ["章节结构"]

async def test_mobile_memoir_book_output_marks_unconfirmed_plan_as_stale(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    plan, _ = await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)
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
    register_memoir_editing_job_handlers()
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
    assert fake_tts.calls[0]["text"] == (
        "外婆家的童年\n\n我小时候常在外婆家的院子里晒太阳。"
    )
    assert fake_tts.calls[0]["speed_ratio"] == 1.5
    assert fake_tts.calls[1]["speed_ratio"] == 0.7
    playback = await client.get(f"/api/mobile/memoir/audio-files/{first.json()['audio_file_ids'][0]}/playback")
    assert playback.status_code == 200


async def test_mobile_memoir_book_chapter_section_tts_reads_only_selected_section(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    register_memoir_editing_job_handlers()
    monkeypatch.setattr(
        "app.services.memoir.audio_files.MEMOIR_AUDIO_DIR",
        tmp_path / "audio",
    )
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(
        test_db,
        project_id=project_id,
    )
    section = await test_db.scalar(
        select(MemoirSection).where(
            MemoirSection.project_id == project_id,
            MemoirSection.chapter_id == chapters[0].id,
        )
    )
    assert section is not None
    fake_tts = FakeBookTTS()
    monkeypatch.setattr(
        "app.services.memoir.book_audio.get_tts_client",
        lambda: fake_tts,
    )
    endpoint = (
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/"
        f"{chapters[0].id}/draft/sections/{section.id}/tts"
    )

    first = await client.post(
        endpoint,
        json={"voice": "voice-a", "speed_ratio": 0.7},
    )
    second = await client.post(
        endpoint,
        json={"voice": "voice-a", "speed_ratio": 0.7},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["audio_file_ids"] == second.json()["audio_file_ids"]
    assert len(fake_tts.calls) == 1
    assert fake_tts.calls[0]["text"] == (
        "外婆家的童年\n\n我小时候常在外婆家的院子里晒太阳。"
    )
    assert fake_tts.calls[0]["speed_ratio"] == 0.7


async def test_mobile_memoir_book_outline_tts_generates_and_reuses_audio(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    register_memoir_editing_job_handlers()
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
    chapters[1].include_in_book = False
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
    assert "本章目前保留整理，但不收入最终成书。" in outline_text
    assert "你可以继续调整章节顺序、合并章节，或者说出想修改的地方。" in outline_text
    assert fake_tts.calls[0]["voice"] == "voice-outline"
    assert fake_tts.calls[0]["speed_ratio"] == 1.25


async def test_mobile_memoir_book_output_tts_generates_full_book_audio(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    register_memoir_editing_job_handlers()
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)
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


async def test_export_info_reuses_pdf_and_changes_with_effective_settings(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    version = await _seed_confirmed_book_output(test_db, project_id=project_id)
    base = f"/api/mobile/memoir/projects/{project_id}/book-output/export"
    calls = []
    original = book_outputs.render_book_output_export

    def render(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(book_outputs, "render_book_output_export", render)
    info = (await client.get(base + "/info?format=pdf")).json()
    assert info["version_id"] == version.id
    assert info["ready"] is False
    assert "外婆的回忆录" in info["filename"]
    assert info["filename"].endswith("_阅读版.pdf")
    first = await client.get(base + f"?format=pdf&cache_key={info['cache_key']}")
    again = await client.get(base + "?format=pdf")
    assert first.status_code == again.status_code == 200
    assert first.content == again.content
    assert len(calls) == 1
    assert (await client.get(base + "/info?format=pdf")).json()["ready"] is True
    same = (await client.get(base + "/info?format=pdf&show_source_appendix=true")).json()
    assert same["cache_key"] == info["cache_key"]
    for query in ("layout=large_print", "show_source_appendix=false", "preview_template=modern_minimal"):
        changed = (await client.get(base + "/info?format=pdf&" + query)).json()
        assert changed["cache_key"] != info["cache_key"]
        assert changed["ready"] is False
    changed = await client.patch(f"/api/mobile/memoir/projects/{project_id}/book-output", json={"title": "新的书名"})
    assert changed.status_code == 200
    latest = (await client.get(base + "/info?format=pdf")).json()
    assert latest["version_id"] != version.id
    assert latest["cache_key"] != info["cache_key"]
    historical = (await client.get(base + f"/info?format=pdf&version_id={version.id}")).json()
    assert historical == {**info, "ready": True}
    conflict = await client.get(base + f"?format=pdf&cache_key={info['cache_key']}")
    assert conflict.status_code == 409


async def test_concurrent_export_and_timeout_retry_share_one_render(client, test_db, monkeypatch):
    import time
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    owner = (await test_db.get(Project, project_id)).user_id
    prepared = await book_outputs.prepare_book_output_export(
        test_db, user_id=owner, project_id=project_id,
        export_format=book_outputs.MemoirBookOutputExportFormat.PDF,
    )
    calls = []

    def slow_render(**kwargs):
        calls.append(kwargs)
        time.sleep(0.1)
        return book_outputs.ExportFile("book.pdf", "application/pdf", b"%PDF-test\n%%EOF\n")

    monkeypatch.setattr(book_outputs, "render_book_output_export", slow_render)
    with pytest.raises(HTTPException) as error:
        await book_outputs.export_book_output_file(prepared, timeout_seconds=0.01)
    assert error.value.status_code == 504
    results = await asyncio.gather(*(book_outputs.export_book_output_file(prepared) for _ in range(4)))
    assert len(calls) == 1
    assert all(result.content == results[0].content for result in results)


async def test_failed_or_missing_pdf_can_be_regenerated(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    owner = (await test_db.get(Project, project_id)).user_id
    prepared = await book_outputs.prepare_book_output_export(
        test_db, user_id=owner, project_id=project_id,
        export_format=book_outputs.MemoirBookOutputExportFormat.PDF,
    )
    calls = []

    def render(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("render failed")
        return book_outputs.ExportFile("book.pdf", "application/pdf", b"%PDF-test\n%%EOF\n")

    monkeypatch.setattr(book_outputs, "render_book_output_export", render)
    with pytest.raises(RuntimeError, match="render failed"):
        await book_outputs.export_book_output_file(prepared)
    await book_outputs.export_book_output_file(prepared)
    path = book_output_export_cache.export_cache_path(prepared, prepared.cache_key)
    path.write_bytes(b"%PDF-truncated")
    await book_outputs.export_book_output_file(prepared)
    assert len(calls) == 3


@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
async def test_project_deletion_waits_for_export_and_removes_the_pdf_cache(client, test_db, monkeypatch, interruption):
    from threading import Event
    from app.services.projects import core as project_core
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=project_id)
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    project = await test_db.get(Project, project_id)
    user = await test_db.get(User, project.user_id)
    prepared = await book_outputs.prepare_book_output_export(
        test_db, user_id=user.id, project_id=project_id,
        export_format=book_outputs.MemoirBookOutputExportFormat.PDF,
    )
    started = Event()
    release = Event()

    def render(**kwargs):
        started.set()
        assert release.wait(5)
        return book_outputs.ExportFile("book.pdf", "application/pdf", b"%PDF-test\n%%EOF\n")

    monkeypatch.setattr(book_outputs, "render_book_output_export", render)
    export = asyncio.create_task(book_outputs.export_book_output_file(
        prepared, timeout_seconds=0.02 if interruption == "timeout" else 5,
    ))
    assert await asyncio.to_thread(started.wait, 5)
    if interruption == "cancel":
        export.cancel()
        with pytest.raises(asyncio.CancelledError):
            await export
    else:
        with pytest.raises(HTTPException) as error:
            await export
        assert error.value.status_code == 504
    deletion = asyncio.create_task(project_core.delete_project(test_db, project_id, user))
    await asyncio.sleep(0.03)
    assert not deletion.done()
    release.set()
    await deletion
    assert not (content_storage.PROJECTS_DIR / project_id).exists()
    with pytest.raises(HTTPException) as error:
        await book_outputs.export_book_output_file(prepared)
    assert error.value.status_code == 404
    assert not (content_storage.PROJECTS_DIR / project_id).exists()


def test_retains_shorter_valid_original_quote_after_another_sentence_changes():
    from app.services.memoir.book_output_versions import retained_chapter_quotes
    ref = {"utterance_id": "u", "quote": "我很高兴。那年我十五岁。我一直记得师傅教我修车的日子。"}
    stored = {"source_refs": [ref], "pull_quotes": [
        {"text": "我很高兴。", "source": "original_quote", "source_ref_ids": ["u"]}]}
    current = {"source_refs": [ref], "content": "我很高兴。那年我十六岁。我一直记得师傅教我修车的日子。"}
    assert [item.text for item in retained_chapter_quotes(stored, current, polish_level=MemoirTranscriptPolishLevel.LOW)] == ["我很高兴。"]


def test_polished_prose_falls_back_to_a_distinct_body_excerpt():
    snapshot = [{"chapter_id": "c", "chapter_title": "往事", "chapter_index": 1,
        "content": "那一天，我非常高兴。我始终记得师傅教我修车的那些日子。",
        "source_refs": [{"utterance_id": "u", "quote": "那天我很高兴。我一直记得师傅教我修车的日子。"}]}]
    manuscript = manuscript_from_snapshot(title="往事", content=snapshot[0]["content"], snapshot=snapshot)
    quote = manuscript.chapters[0].pull_quotes[0]
    assert quote.text == "我始终记得师傅教我修车的那些日子。"
    assert quote.source == "editorial_generated"
    assert quote.source_ref_ids == []
    snapshot[0]["content"] = "那一天，我非常高兴。"
    manuscript = manuscript_from_snapshot(title="往事", content=snapshot[0]["content"], snapshot=snapshot)
    assert manuscript.chapters[0].pull_quotes == []


def test_generated_body_excerpt_never_claims_to_be_original_speech():
    from app.services.memoir.book_manuscript_ai import _copy_from_payload
    snapshot = [{"chapter_id": "c", "content": "那一天，我非常高兴。我始终记得师傅教我修车的那些日子。",
        "source_refs": [{"utterance_id": "u", "quote": "那天我很高兴。我一直记得师傅教我修车的日子。"}]}]
    excerpt = "我始终记得师傅教我修车的那些日子。"
    payload = {"chapter_intros": [{"chapter_id": "C1", "pull_quote": excerpt,
        "pull_quote_source_ref_id": ""}], "back_cover_quote": excerpt}
    result = _copy_from_payload(payload, snapshot=snapshot, prompt="", model="offline")
    assert result.chapter_intros[0].pull_quote == excerpt
    assert result.chapter_intros[0].pull_quote_source_ref_id == ""
    assert result.back_cover_quote == excerpt
    payload["chapter_intros"][0]["pull_quote_source_ref_id"] = "M1"
    result = _copy_from_payload(payload, snapshot=snapshot, prompt="", model="offline")
    assert result.chapter_intros == []


def test_no_quote_candidate_is_a_valid_generation_result():
    from app.services.memoir.book_manuscript_ai import _copy_from_payload
    result = _copy_from_payload({"chapter_intros": [{"chapter_id": "C1", "pull_quote": ""}]},
        snapshot=[{"chapter_id": "c", "content": "我五岁那年离开家。", "source_refs": []}],
        prompt="", model="offline")
    assert result is not None
    assert result.chapter_intros[0].pull_quote == ""


@pytest.mark.parametrize("body,expected", [
    ('他说：“好。”', ()),
    ('开头。嗯。好。', ()),
    ('开头。老师说：“慢慢走。不用着急。”', ()),
    ('开头。后来我们一起走过那条很长的小路。', ('后来我们一起走过那条很长的小路。',)),
    ('开头。没有闭合的“引号。', ()),
])
def test_body_quotes_have_meaningful_length_and_balanced_punctuation(body, expected):
    from app.services.memoir.book_manuscript_cleanup import body_excerpt_candidates
    assert body_excerpt_candidates(body, max_chars=90) == expected


def test_quote_display_policy_preserves_existing_evidence_signature():
    from app.services.memoir.book_output_versions import chapter_editorial_copy_signature
    body = '他说：“好。”\n后来我们一起去了学校，我一直记得那天。'
    snapshot = {'chapter_id': 'c', 'chapter_title': '上学', 'content': body,
                'source_refs': [{'utterance_id': 'u', 'quote': body}]}
    # Captured from v2 before the display-quality fix, including old quote splitting.
    assert chapter_editorial_copy_signature(snapshot) == 'c946ade1be87df1e3777c9bc3de07cd92af99ef4d58e2371a856e68f2b8671f2'


@pytest.mark.parametrize("wrapper", ["none", "quotes"])
def test_generated_excerpt_returns_canonical_evidence_without_guessing(wrapper):
    from app.services.memoir.book_manuscript_ai import _copy_from_payload
    sentence = '后来我们一起走过那条很长的小路。'
    value = sentence if wrapper == 'none' else f'“{sentence}”' if wrapper == 'quotes' else sentence[:-1]
    copy = _copy_from_payload({'chapter_intros': [{'chapter_id': 'C1', 'pull_quote': value}], 'back_cover_quote': value},
        snapshot=[{'chapter_id': 'c', 'chapter_title': '回家', 'content': '那是一个普通的日子。' + sentence}], prompt='', model='offline')
    assert copy.chapter_intros[0].pull_quote == sentence
    assert copy.chapter_intros[0].pull_quote_source_ref_id == ''
    assert copy.back_cover_quote == sentence


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("speech", [
    '母亲说：“孩子，路上小心。{sep}“到了就写信回来。”',
    '母亲说：“孩子，路上小心。',
    '母亲说：「路上要小心。',
])
def test_quote_evidence_survives_an_unclosed_previous_paragraph(separator, speech):
    from app.services.memoir.book_manuscript_cleanup import body_excerpt_candidates, original_quote_candidates
    from app.services.memoir.book_output_versions import retained_chapter_quotes
    ending = '后来我们走了很远的路才到县城。'
    body = separator.join(['我记得那天很冷。', speech.format(sep=separator), ending])
    snapshot = {'chapter_id': 'c', 'chapter_title': '离家', 'content': body,
        'source_refs': [{'utterance_id': 'u', 'quote': body}],
        'sections': [{'content': body, 'used_material_ids': ['u']}]}
    assert ending in body_excerpt_candidates(body, max_chars=90)
    assert any(ending in candidate for candidate in original_quote_candidates(snapshot, max_chars=90)['u'])
    for source in ('original_quote', 'editorial_generated'):
        stored = {**snapshot, 'pull_quotes': [{'text': ending, 'source': source,
            'source_ref_ids': ['u'] if source == 'original_quote' else []}]}
        assert [quote.text for quote in retained_chapter_quotes(stored, snapshot, polish_level=MemoirTranscriptPolishLevel.LOW)] == [ending]






@pytest.mark.parametrize("value,expected", [
    ("后来我们一起走过那条很长的小路。", True),
    ("“后来我们一起走过那条很长的小路。”", True),
    ("后来我们一起走过那条很长的小路", False),
    ("我们一起走过那条很长的小路。", False),
])
def test_quote_selection_requires_exact_candidate(value, expected):
    from app.services.memoir.book_manuscript_cleanup import matched_quote
    candidate = "后来我们一起走过那条很长的小路。"
    assert matched_quote(value, [candidate], max_chars=90) == (candidate if expected else "")


@pytest.mark.parametrize("level", list(MemoirTranscriptPolishLevel))
async def test_quote_policy_is_shared_by_prompt_schema_validation_and_fallback(level):
    from app.services.memoir.book_manuscript_ai import _ManuscriptCopyOutput, _OriginalManuscriptCopyOutput, _copy_from_payload, build_manuscript_copy_prompt
    excerpt = '后来我们一起走过那条很长的小路。'
    snapshot = [{'chapter_id': 'c', 'chapter_title': '往事', 'content': '那年我回了家。' + excerpt,
        'source_refs': [{'utterance_id': 'u', 'quote': excerpt}]}]
    prompt = build_manuscript_copy_prompt(title='往事', snapshot=snapshot, polish_level=level)
    original = level == MemoirTranscriptPolishLevel.LOW
    assert ('original_quote_candidates' in prompt) is original
    assert ('pull_quote_source_ref_id' in prompt) is original
    payload = {'chapter_intros': [{'chapter_id': 'C1', 'pull_quote': excerpt,
        'pull_quote_source_ref_id': 'M1'}]}
    schema = _OriginalManuscriptCopyOutput if original else _ManuscriptCopyOutput
    result = _copy_from_payload(schema.model_validate(payload).model_dump(), snapshot=snapshot,
        prompt=prompt, model='offline', polish_level=level)
    assert result.chapter_intros[0].pull_quote == excerpt
    assert result.chapter_intros[0].pull_quote_source_ref_id == ('u' if original else '')
    manuscript = manuscript_from_snapshot(title='往事', content=snapshot[0]['content'], snapshot=snapshot, polish_level=level)
    quote = manuscript.chapters[0].pull_quotes[0]
    assert quote.source == ('original_quote' if original else 'editorial_generated')
    assert quote.source_ref_ids == (['u'] if original else [])


def test_low_original_quote_cannot_be_retained_from_a_different_section():
    from app.services.memoir.book_output_versions import retained_chapter_quotes
    text = '我十五岁那年发过洪水。'
    snapshot = {'content': '我五岁那年发过洪水。' + text,
        'source_refs': [{'utterance_id': 'u1', 'quote': text}],
        'sections': [
            {'content': '我五岁那年发过洪水。', 'used_material_ids': ['u1']},
            {'content': text, 'used_material_ids': ['u2']},
        ]}
    stored = {**snapshot, 'pull_quotes': [{'text': text, 'source': 'original_quote', 'source_ref_ids': ['u1']}]}
    assert retained_chapter_quotes(stored, snapshot, polish_level=MemoirTranscriptPolishLevel.LOW) == []


async def test_polished_copy_ignores_only_obsolete_source_field_without_provider_retry():
    from pydantic import ValidationError
    from app.services.memoir.book_manuscript_ai import _ManuscriptCopyOutput
    excerpt = '后来我们一起走过那条很长的小路。'
    class OldShapeAI(FakeEditorialAI):
        async def _call_llm(self, *args, **kwargs):
            self.calls.append(True)
            return json.dumps({'chapter_intros': [{'chapter_id': 'C1', 'intro': '一起走过的小路',
                'pull_quote': excerpt, 'pull_quote_source_ref_id': 'M1'}]})
    ai = OldShapeAI()
    result = await generate_manuscript_editorial_copy(title='往事', ai_model=None, ai_client=ai, snapshot=[{
        'chapter_id': 'c', 'chapter_title': '往事', 'content': '我记得那一天。' + excerpt,
        'source_refs': [{'utterance_id': 'u', 'quote': excerpt}],
    }])
    assert result is not None and len(ai.calls) == 1
    assert result.chapter_intros[0].pull_quote == excerpt
    assert result.chapter_intros[0].pull_quote_source_ref_id == ''
    with pytest.raises(ValidationError):
        _ManuscriptCopyOutput.model_validate({'chapter_intros': [{'pull_quote': excerpt, 'unknown_field': 'value'}]})
