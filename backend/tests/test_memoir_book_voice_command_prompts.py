from types import SimpleNamespace

from app.schemas.memoir_book import (
    MemoirBookVoiceCommandBookContext,
    MemoirBookVoiceCommandSourceCoverageContext,
    MemoirBookVoiceCommandUncoveredSourceContext,
)
from app.services.memoir.book_voice_command_context import (
    chapter_source_count,
    draft_char_count,
    latest_draft_content,
)
from app.services.memoir.book_voice_command_prompts import build_book_voice_command_user_prompt


def _chapter(
    *,
    chapter_id: str,
    title: str,
    sort_order: int,
    content: str = "",
    source_refs: list[dict] | None = None,
):
    versions = [SimpleNamespace(content=content)] if content else []
    return SimpleNamespace(
        id=chapter_id,
        title=title,
        sort_order=sort_order,
        source_refs_json=source_refs or [],
        draft=SimpleNamespace(versions=versions) if versions else None,
    )


def test_book_voice_prompt_includes_chapter_status_sources_and_conversation():
    chapter = _chapter(
        chapter_id="chapter-1",
        title="外婆家的童年",
        sort_order=0,
        content="外婆家的院子里有一棵枣树。",
    )
    plan = SimpleNamespace(status="confirmed", chapters=[chapter])
    book_context = MemoirBookVoiceCommandBookContext(
        source_coverage=MemoirBookVoiceCommandSourceCoverageContext(
            total_source_count=3,
            covered_source_count=1,
            uncovered_source_count=2,
        ),
        uncovered_sources=[
            MemoirBookVoiceCommandUncoveredSourceContext(
                source_index=7,
                utterance_id="utterance-7",
                text="这条讲述提到了外婆家院子和摘菜。",
            )
        ],
    )
    conversation_context = [
        SimpleNamespace(role="user", text="还有多少记录没收入章节", summary=None),
        SimpleNamespace(role="assistant", text="还有 2 条未收入章节。", summary="回答素材覆盖"),
    ]

    prompt = build_book_voice_command_user_prompt(
        text="把刚才这条放到第一章",
        plan=plan,
        current_chapter_id="chapter-1",
        conversation_context=conversation_context,
        book_context=book_context,
        source_count_by_chapter={"chapter-1": 2},
    )

    assert "1. 外婆家的童年｜素材 2 条｜正文 13 字｜摘录：外婆家的院子里有一棵枣树。" in prompt
    assert "当前目录 1 章，已有正文 1 章，规划状态 confirmed。" in prompt
    assert "讲述记录共 3 条，已收入章节 1 条，未收入章节 2 条。" in prompt
    assert "待收录第 1 条｜语音记录 7｜摘录：这条讲述提到了外婆家院子和摘菜。" in prompt
    assert "第 1 章，外婆家的童年，正文 13 字" in prompt
    assert "AI：还有 2 条未收入章节。（回答素材覆盖）" in prompt
    assert "用户原句：\n把刚才这条放到第一章" in prompt


def test_book_voice_prompt_helpers_use_source_count_override_and_latest_draft():
    chapter = _chapter(
        chapter_id="chapter-1",
        title="外婆家的童年",
        sort_order=0,
        content=" 第一版\n第二版 ",
        source_refs=[{"utterance_id": "u1"}, {"utterance_id": "u2"}],
    )

    assert chapter_source_count(chapter) == 2
    assert chapter_source_count(chapter, {"chapter-1": 5}) == 5
    assert latest_draft_content(chapter) == "第一版\n第二版"
    assert draft_char_count(chapter) == 6
