from __future__ import annotations

from types import SimpleNamespace

from app.services.memoir import book_voice_command_rules as rules


def test_rule_helpers_normalize_chapter_and_source_references():
    assert rules._compact(" 第 一 章 ") == "第一章"
    assert rules._clean_chapter_ref("请把第一章的顺序") == "第一章"
    assert rules._clean_source_ref("把刚才这条") == "刚才这条"
    assert rules._chapter_number("第十章") == 10
    assert rules._source_ref_number("待收录第二条") == 2


def test_rule_helpers_classify_source_and_consultation_requests():
    source_read = rules._compact("下一条未收入记录给我念一下")
    coverage_question = rules._compact("还有多少记录没收入章节")
    title_question = rules._compact("第一章标题是不是需要换个名字")
    length_question = rules._compact("第一章是不是太短")

    assert rules._is_source_read_request(source_read) is True
    assert rules._source_read_ref(source_read) == "下一条"
    assert rules._is_source_coverage_question(coverage_question) is True
    assert rules._fallback_consultation_intent(title_question) == "review_chapter_title"
    assert rules._fallback_consultation_intent(length_question) == "review_chapter_length"


def test_rule_helpers_normalize_payload_values_and_response_kind():
    move_payload = SimpleNamespace(
        direction=None,
        target_position=None,
        target_index=3,
        relative_chapter_ref=None,
        relative_position=None,
    )

    assert rules._normalized_direction("往前") == "up"
    assert rules._normalized_target_position("最后一章") == "last"
    assert rules._normalized_relative_position("前面") == "before"
    assert rules._optional_int("三") == 3
    assert rules._payload_has_move_target(move_payload) is True
    assert rules._response_kind_for_intent("review_chapter_title") == "consultation"
    assert rules._response_kind_for_intent("capability_help") == "answer"
    assert rules._response_kind_for_intent("unknown") == "clarification"


def test_rule_helpers_clean_user_facing_text():
    assert rules._optional_text("  第一章 ") == "第一章"
    assert rules._clean_title(" “童年往事”。 ") == "童年往事"
    assert rules._clean_reply_text("第一句\n第二句") == "第一句 第二句"
    assert rules._clean_reply_text("x" * 300) == f"{'x' * 277}..."
    assert rules._is_ambiguous_follow_up("刚才那个") is True
