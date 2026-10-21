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


def test_experimental_prompt_is_opt_in_and_preserves_completion_contract():
    baseline = prepare_interview_turn(snapshot(), InterviewConfig())
    candidate = prepare_interview_turn(snapshot(), InterviewConfig(prompt_variant="grounded_v1"))
    assert baseline.prompt_version != candidate.prompt_version
    assert baseline.prompt != candidate.prompt
    completion = snapshot("book_completion")
    original = prepare_interview_turn(completion, InterviewConfig())
    candidate_completion = prepare_interview_turn(completion, InterviewConfig(prompt_variant="grounded_v1"))
    assert original.prompt == candidate_completion.prompt
    assert original.prompt_version == candidate_completion.prompt_version
    with pytest.raises(ValueError, match="Unsupported interview prompt variant"):
        prepare_interview_turn(snapshot(), InterviewConfig(prompt_variant="typo"))
