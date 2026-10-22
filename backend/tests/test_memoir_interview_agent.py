import asyncio
from contextlib import aclosing
from datetime import datetime

import pytest

from app.domain.memoir.types import MemoirConversationAction
from app.services.ai_streaming import AITextStreamChunk
from app.services.memoir.interview_agent import (
    InterviewConfig, InterviewInput, InterviewRun, prepare_interview_turn, stream_interview_turn,
)
from app.services.memoir.voice_followup_ai import InterviewDialogue, InterviewMessage


class StreamingClient:
    provider_name = "test"
    model_name = "test-interviewer"

    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def stream_llm(self, *args, **kwargs):
        try:
            for text in self.chunks:
                yield AITextStreamChunk(text_delta=text)
            yield AITextStreamChunk(usage={"input_tokens": 12, "output_tokens": 8}, finish_reason="stop")
        finally:
            self.closed = True


def snapshot(goal="open_narration"):
    return InterviewInput(dialogue=InterviewDialogue(goal, (
        InterviewMessage("user", "我先想想，后来还发生了一件事。", 0, "m1", datetime(2026, 1, 1)),
    )))


@pytest.mark.parametrize("goal,action", [("open_narration", "listen"), ("book_completion", "pause")])
async def test_offline_turn_uses_live_stream_protocol_and_completion_boundary(goal, action):
    prepared = prepare_interview_turn(snapshot(goal), InterviewConfig())
    client = StreamingClient(["[li", "sten]\n小", "余：", "您慢慢想。"])
    run = InterviewRun()
    chunks = [chunk async for chunk in stream_interview_turn(
        prepared, ai_client=client, run=run, cancel_event=asyncio.Event(),
    )]
    turn = run.completed_turn(prepared.input)
    assert "".join(chunk.text_delta for chunk in chunks) == turn.speech_text == "您慢慢想。"
    assert turn.conversation_action.value == action
    assert run.usage == {"input_tokens": 12, "output_tokens": 8}
    assert client.closed


async def test_interview_stream_can_be_consumed_before_provider_finishes_and_closed_early():
    prepared = prepare_interview_turn(snapshot(), InterviewConfig())
    client = StreamingClient(["[listen]\n我在听。", "您接着说。"])
    run = InterviewRun()
    async with aclosing(stream_interview_turn(
        prepared, ai_client=client, run=run, cancel_event=asyncio.Event(),
    )) as stream:
        assert (await anext(stream)).text_delta == "我在听。"
        assert not client.closed
    assert client.closed
    assert run.text == "我在听。"


async def test_cancelled_turn_does_not_start_provider():
    client = StreamingClient(["[listen]\n继续。"])
    cancel = asyncio.Event()
    cancel.set()
    run = InterviewRun()
    chunks = [chunk async for chunk in stream_interview_turn(
        prepare_interview_turn(snapshot(), InterviewConfig()), ai_client=client, run=run, cancel_event=cancel,
    )]
    assert chunks == []
    assert not client.closed


@pytest.mark.parametrize("chunks", [["[lis"], ["[listen]\n"]])
async def test_incomplete_or_empty_turn_cannot_be_reported_as_completed(chunks):
    run = InterviewRun()
    prepared = prepare_interview_turn(snapshot(), InterviewConfig())
    with pytest.raises(ValueError):
        async for _ in stream_interview_turn(
            prepared, ai_client=StreamingClient(chunks), run=run, cancel_event=asyncio.Event(),
        ):
            pass
        run.completed_turn(prepared.input)


async def test_missing_control_prefix_keeps_existing_safe_pause_behavior():
    run = InterviewRun()
    prepared = prepare_interview_turn(snapshot(), InterviewConfig())
    async for _ in stream_interview_turn(
        prepared, ai_client=StreamingClient(["我在听。"]), run=run, cancel_event=asyncio.Event(),
    ):
        pass
    assert run.completed_turn(prepared.input).conversation_action == MemoirConversationAction.PAUSE


def test_prompt_variants_keep_product_context_and_change_only_open_narration_rules():
    prepared_v3 = prepare_interview_turn(snapshot(), InterviewConfig(prompt_variant="v3_improved"))
    prepared_standalone = prepare_interview_turn(snapshot(), InterviewConfig(prompt_variant="standalone"))
    prepared_plus = prepare_interview_turn(snapshot(), InterviewConfig(prompt_variant="v3_plus"))

    assert "自称“小余”" in prepared_v3.system_prompt
    assert "本轮落点" in prepared_v3.prompt
    assert "你在对话中自称“小余”" in prepared_standalone.system_prompt
    assert "称呼讲述者使用“您”" in prepared_standalone.system_prompt
    assert "值得记录" in prepared_standalone.prompt
    assert "你是“小余”" in prepared_plus.system_prompt
    assert "故事价值" in prepared_plus.prompt
    assert "访谈上下文" in prepared_standalone.prompt
    assert "访谈上下文" in prepared_plus.prompt


def test_prompt_variants_do_not_replace_book_completion_contract():
    baseline = prepare_interview_turn(snapshot("book_completion"), InterviewConfig(prompt_variant="v3_improved"))
    standalone = prepare_interview_turn(snapshot("book_completion"), InterviewConfig(prompt_variant="standalone"))
    plus = prepare_interview_turn(snapshot("book_completion"), InterviewConfig(prompt_variant="v3_plus"))

    assert standalone.system_prompt == baseline.system_prompt
    assert plus.system_prompt == baseline.system_prompt
    assert "当前是成书补访" in standalone.prompt
    assert "当前是成书补访" in plus.prompt
