"""The interviewer's text boundary, shared by live voice turns and offline runs.

Policy context loading and topic validation stay with the database adapter. Policy
selection itself remains the shared, database-free select_policy_action function.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from typing import Literal

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
SUPPORTED_PROMPT_VARIANTS = ("baseline", "grounded_v1", "grounded_v2", "grounded_v3", "grounded_v4")
_GROUNDED_INTERVIEW_INSTRUCTION = """本轮承接与提问的事实边界：
- 事实只来自讲述者明确说过的内容或给定资料。访谈员的问题、猜测和未获确认的总结不算讲述者的经历。
- 礼貌承接也要有依据；不要从年龄、职业或说话方式推断健康、性格、情绪和长期心理。可以承接已经表达的感受，不替他补感受。
- 问题的前提同样不能添写经历：尚未讲过的离开、转行、迁居或遭遇不能当成已发生。只沿已知内容邀请展开，允许对方否认或说不知道。
- “不知道、从未问过、没有证据”保留为未知，不改成忘记，不猜原因、他人动机或讲述者的心理。"""
_GROUNDED_V2_INSTRUCTION = """本轮先核对事实，再接话（只输出访谈员的话，不输出核对过程）：
- 事实仅来自讲述者明确说过的内容或给定资料。访谈员的猜测、问题和未被确认的总结不能算成讲述者的经历；也不能根据年龄、职业、说话方式补出健康、性格、情绪或“经历肯定很多”等评价。
- 对每个承接句和问题前提，分别核对谁、做了什么、对谁、何时何地以及结果。不同经历不合并，遇见某人不等于向他学习，做过一件事不等于取得某种结果。缺少依据就删掉该前提，不靠“是不是”给猜测披上问题形式。
- 用户最新的否定和更正优先；改正被否定的那一部分，保留其余已明确的信息。已经确认的更正后续继续沿用，不重问已说清的事实。
- 不知道、没问过、缺乏证据保持原样，不转换成忘记，不要求回忆从未知道的事，不猜别人动机或替讲述者补心理。
- 接话保持朴素自然，最多一个问题。用户还在展开就让他继续；需要邀请展开时，沿他明确提到的一件事问一个尚未说过的具体细节。若对方刚纠正你，先准确接住这次纠正，不强行换到另一段经历。"""
_GROUNDED_V3_INSTRUCTION = _GROUNDED_V2_INSTRUCTION + """
本轮落点：先在讲述者最新一两句话里选一个明确说到的事、原话或细节。需要追问时，就让他把这个细节展开，不跨到尚未提及的后续人生。
- 优先问当时具体怎样、某句原话指的是什么、已提到的一件事怎样发生；只有用户已明确开启后续阶段，才跟着往后问。
- 不用“后来您怎么离开/转向/走出/放下”等问法，除非用户已明确说发生了该变化；不要借“后来”自动补出新的经历或心理结局。
- 用户正在给出一个尚未展开的概念或场景，就接住这个入口，不替他说完；用户已经说明的感受、结果或原因，不再换成确认问题。
- 不需要为了凑字数先评价或复述。一个有依据、好回答的问题可以独立成句；语气自然，不播报规则。"""
_GROUNDED_V4_INSTRUCTION = """访谈原则：让讲述者把自己的事讲清楚，事实与节奏比热情的套话更重要。请遵循输入中的控制标记协议，只输出标记与自然口语，不输出思考过程。
回应前按以下顺序判断：
1. 用户此刻要结束、暂停、不聊某件事或纠正你，先尊重这一意思。更正只替换被否定的内容，保留其他已经明确的信息；后续不再索取已确认的答案。
2. 从用户最新讲述里选一个明确的事件、原话或细节作落点。话未说完或明确想自己讲，就简短接住让他继续；已经给出清楚入口，就邀请展开当前这件事，不自动跳到未提及的“后来”。
3. 要问就只问一件事，给一个容易回答的开放入口。不用二选一串出两种猜测，不把“怎么接触的”和“有什么影响”等两个问题塞在一句里。先检查答案是否已经说过；已说过就不要再确认。
4. 检查回应每个断言和问题前提：主体、动作、对象、地点、时间、结果、心理状态都需要讲述者或给定资料的明确依据。两件事分别出现，不代表它们之间存在因果或师生等关系；提到某人的机构不代表讲述者到过那里。不得添写停下脚步、后来重逢、离开转行、走出伤痛等未说过的动作或变化；疑问句也不能豁免这些前提。
5. 不知道、从未问过、没有证据保持为未知，不等于忘记。可以邀请讲自己的亲历，不要求猜测未知原因或他人动机。礼貌承接也不推断健康、性格、人生经历或情绪；朴素接话即可，不为凑字数复述、夸赞或补心理。
若一个问题必须先补出未说事实才能成立，就删掉这个前提，回到用户原话里的落点。"""
_PROMPT_INSTRUCTIONS = {
    "grounded_v1": _GROUNDED_INTERVIEW_INSTRUCTION,
    "grounded_v2": _GROUNDED_V2_INSTRUCTION,
    "grounded_v3": _GROUNDED_V3_INSTRUCTION,
    "grounded_v4": _GROUNDED_V4_INSTRUCTION,
}


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
    prompt_variant: Literal["baseline", "grounded_v1", "grounded_v2", "grounded_v3", "grounded_v4"] = "baseline"


@dataclass(frozen=True)
class PreparedInterviewTurn:
    input: InterviewInput
    config: InterviewConfig
    system_prompt: str
    prompt: str
    max_tokens: int
    prompt_version: str = STREAM_PROMPT_VERSION


def prepare_interview_turn(snapshot: InterviewInput, config: InterviewConfig) -> PreparedInterviewTurn:
    if config.prompt_variant not in SUPPORTED_PROMPT_VARIANTS:
        raise ValueError(f"Unsupported interview prompt variant: {config.prompt_variant}")
    prompt = build_stream_turn_prompt(
        snapshot.dialogue,
        has_previous_project_narration=snapshot.has_previous_project_narration,
        activation_context=snapshot.activation_context,
        regenerate=config.regenerate,
        progress_context=snapshot.progress_context,
        persona=snapshot.persona,
        completion_prompt_context=snapshot.completion_context,
        book_context=snapshot.book_context,
        policy_instruction=snapshot.policy_instruction,
    )
    version = STREAM_PROMPT_VERSION
    system_prompt = system_prompt_for_persona(snapshot.persona)
    if config.prompt_variant != "baseline" and snapshot.dialogue.session_goal == MemoirSessionGoal.OPEN_NARRATION.value:
        if config.prompt_variant == "grounded_v4":
            system_prompt += "\n\n" + _PROMPT_INSTRUCTIONS[config.prompt_variant]
        else:
            prompt += "\n\n" + _PROMPT_INSTRUCTIONS[config.prompt_variant]
        version += "/" + config.prompt_variant.replace("_", "-")
    return PreparedInterviewTurn(
        input=snapshot,
        config=config,
        system_prompt=system_prompt,
        prompt=prompt,
        prompt_version=version,
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
