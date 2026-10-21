"""The interviewer's text boundary, shared by live voice turns and offline runs.

Policy context loading and topic validation stay with the database adapter. Policy
selection itself remains the shared, database-free select_policy_action function.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field, replace

from app.domain.memoir.types import MemoirConversationAction, MemoirSessionGoal
from app.services.ai_calls import stream_text_with_client
from app.services.ai_streaming import AITextStreamChunk
from app.services.ai_timeouts import AITextTimeoutProfile, ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.memoir.completion_interviews import CompletionIssuePromptContext
from app.services.memoir.interview_book_context import InterviewBookContext
from app.services.memoir.voice_followup_ai import (
    InterviewDialogue,
    InterviewerPersona,
    InterviewerTurn,
    ProgressPromptContext,
    _has_user_message,
    _llm_max_tokens,
    split_interviewer_control_prefix,
    strip_streaming_speaker_label_prefix,
    system_prompt_for_persona,
)
from app.services.memoir.voice_interviewer_prompt import build_stream_turn_prompt


STREAM_PROMPT_VERSION = "memoir_voice_interviewer_stream_turn_v10"
STREAM_TEXT_TEMPERATURE = 0.72


@dataclass(frozen=True)
class InterviewInput:
    dialogue: InterviewDialogue
    has_previous_project_narration: bool = False
    activation_context: str = ""
    progress_context: ProgressPromptContext = field(default_factory=lambda: ProgressPromptContext(prompt_text=""))
    persona: InterviewerPersona = field(default_factory=lambda: InterviewerPersona("小余", "unknown"))
    completion_context: CompletionIssuePromptContext | None = None
    book_context: InterviewBookContext | None = None
    policy_instruction: str = ""


@dataclass(frozen=True)
class InterviewConfig:
    requested_model: str | None = None
    temperature: float = STREAM_TEXT_TEMPERATURE
    regenerate: bool = False


@dataclass(frozen=True)
class PreparedInterviewTurn:
    input: InterviewInput
    config: InterviewConfig
    system_prompt: str
    prompt: str
    max_tokens: int


def prepare_interview_turn(snapshot: InterviewInput, config: InterviewConfig) -> PreparedInterviewTurn:
    return PreparedInterviewTurn(
        input=snapshot,
        config=config,
        system_prompt=system_prompt_for_persona(snapshot.persona),
        prompt=build_stream_turn_prompt(
            snapshot.dialogue,
            has_previous_project_narration=snapshot.has_previous_project_narration,
            activation_context=snapshot.activation_context,
            regenerate=config.regenerate,
            progress_context=snapshot.progress_context,
            persona=snapshot.persona,
            completion_prompt_context=snapshot.completion_context,
            book_context=snapshot.book_context,
            policy_instruction=snapshot.policy_instruction,
        ),
        max_tokens=_llm_max_tokens(
            snapshot.dialogue,
            has_user_message=_has_user_message(snapshot.dialogue.messages),
            regenerate=config.regenerate,
        ),
    )


@dataclass
class InterviewRun:
    text: str = ""
    conversation_action: MemoirConversationAction | None = None
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    ai_provider: str | None = None
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    response_ai_model: str | None = None

    def completed_turn(self, snapshot: InterviewInput) -> InterviewerTurn:
        action = self.conversation_action
        if action is None:
            raise ValueError("AI 未返回有效的会话动作，请重试")
        has_user_message = _has_user_message(snapshot.dialogue.messages)
        if (
            snapshot.dialogue.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value
            and has_user_message and action == MemoirConversationAction.LISTEN
        ):
            action = MemoirConversationAction.PAUSE
        fallback_intent = "follow_up" if has_user_message else "opening"
        intent = fallback_intent if action == MemoirConversationAction.LISTEN else "acknowledge"
        if not self.text.strip():
            raise ValueError("AI 未返回可用访谈回应")
        return InterviewerTurn(intent, [intent], self.text.strip(), action)


async def stream_interview_turn(
    prepared: PreparedInterviewTurn,
    *,
    ai_client: AITextClient,
    run: InterviewRun,
    cancel_event: asyncio.Event,
) -> AsyncIterator[AITextStreamChunk]:
    """Emit sanitized speech immediately; retain provider metadata even without speech.

    The caller owns the iterator and must close it on early exit. Task cancellation
    releases the upstream provider while the voice adapter owns audio cancellation.
    """
    if cancel_event.is_set():
        return
    control_buffer = ""
    initial_text_buffer = ""
    initial_text_resolved = False
    async with aclosing(stream_text_with_client(
        ai_client,
        prepared.system_prompt,
        prepared.prompt,
        requested_model=prepared.config.requested_model,
        temperature=prepared.config.temperature,
        max_tokens=prepared.max_tokens,
        timeout=ai_text_timeout_seconds(AITextTimeoutProfile.INTERACTIVE),
        text_timeout=ai_text_timeout_seconds(AITextTimeoutProfile.REALTIME),
    )) as stream:
        async for chunk in stream:
            if cancel_event.is_set():
                return
            run.finish_reason = chunk.finish_reason or run.finish_reason
            if chunk.usage:
                run.usage.update(chunk.usage)
            run.ai_provider = chunk.provider or run.ai_provider
            run.requested_ai_model = chunk.requested_model or run.requested_ai_model
            run.resolved_ai_model = chunk.resolved_model or run.resolved_ai_model
            run.response_ai_model = chunk.response_model or run.response_ai_model
            text_delta = chunk.text_delta
            if text_delta and run.conversation_action is None:
                control_buffer += text_delta
                text_delta, run.conversation_action = split_interviewer_control_prefix(control_buffer)
            if text_delta and run.conversation_action is not None and not initial_text_resolved:
                initial_text_buffer += text_delta
                text_delta, initial_text_resolved = strip_streaming_speaker_label_prefix(initial_text_buffer)
            run.text += text_delta
            yield replace(chunk, text_delta=text_delta)
    if run.conversation_action is None:
        raise ValueError("AI 访谈回复的会话动作不完整，请重试")
