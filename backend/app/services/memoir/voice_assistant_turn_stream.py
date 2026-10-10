from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Protocol
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirGuidanceMode,
    MemoirSessionGoal,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.services.ai_calls import stream_text_with_client
from app.services.ai_costs import estimate_text_cost
from app.services.ai_usage_records import record_ai_usage, record_ai_usage_in_background
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import AITextTimeoutProfile, ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.memoir import automation_events
from app.services.memoir import completion_interviews
from app.services.memoir import memory_activation
from app.services.memoir import progress_snapshot
from app.services.memoir.narrative import interview_policy_runtime
from app.services.memoir.audio_files import StagedAudioFile, discard_staged_audio, stage_audio_bytes
from app.services.memoir.project_ai_preferences import load_project_ai_model
from app.services.memoir.tts_alignment import alignment_segments_to_json
from app.services.memoir.interview_book_context import load_interview_book_context
from app.services.memoir.voice_followup_ai import (
    PROGRESS_PROMPT_VERSION,
    InterviewerTurn,
    ProgressPromptContext,
    build_progress_prompt_context,
    _has_user_message,
    _llm_max_tokens,
    _parse_interviewer_turn,
    _question_mark_count,
    recent_context_messages,
    interviewer_persona_for_voice,
    strip_streaming_speaker_label_prefix,
    system_prompt_for_persona,
)
from app.services.memoir.streaming_tts_events import (
    STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME,
    chunk_text_for_tts,
    normalize_audio_protocol,
    queued_text_chunks,
    stream_tts_events,
)
from app.services.memoir.voice_chat_context import (
    load_voice_session,
    next_turn_index,
    project_has_previous_narration,
)
from app.services.memoir.voice_interviewer_prompt import build_stream_turn_prompt
from app.services.memoir.voice_tts import get_tts_client
from app.services.memoir.voice_tts_types import (
    DEFAULT_TTS_SPEED_RATIO,
    TTSClient,
    StreamingTTSChunk,
)


STREAM_PROMPT_VERSION = "memoir_voice_interviewer_stream_turn_v5"
STREAM_TEXT_TEMPERATURE = 0.72
_PCM_MIME_TYPES = {"audio/l16", "audio/pcm", "audio/x-pcm"}
logger = logging.getLogger(__name__)


class AssistantTurnStreamClient(Protocol):
    async def receive_json(self) -> dict: ...

    async def send_json(self, payload: dict) -> None: ...


@dataclass(frozen=True)
class AssistantTurnStartRequest:
    ai_model: str | None = None
    voice: str | None = None
    speed_ratio: float | None = None
    regenerate: bool = False
    temperature: float = STREAM_TEXT_TEMPERATURE
    audio_protocol: str = STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME

    @classmethod
    def from_payload(cls, payload: dict) -> "AssistantTurnStartRequest":
        temperature = payload.get("temperature", STREAM_TEXT_TEMPERATURE)
        try:
            normalized_temperature = float(temperature)
        except (TypeError, ValueError):
            normalized_temperature = STREAM_TEXT_TEMPERATURE
        normalized_temperature = max(0.0, min(1.5, normalized_temperature))

        speed_ratio = payload.get("speed_ratio")
        try:
            normalized_speed_ratio = float(speed_ratio) if speed_ratio is not None else None
        except (TypeError, ValueError):
            normalized_speed_ratio = None
        if normalized_speed_ratio is not None:
            normalized_speed_ratio = max(0.5, min(2.0, normalized_speed_ratio))

        return cls(
            ai_model=_optional_text(payload.get("ai_model")),
            voice=_optional_text(payload.get("voice")),
            speed_ratio=normalized_speed_ratio,
            regenerate=bool(payload.get("regenerate", False)),
            temperature=normalized_temperature,
            audio_protocol=normalize_audio_protocol(payload.get("audio_protocol")),
        )


@dataclass
class AssistantTurnArtifacts:
    text: str = ""
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    ai_provider: str | None = None
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    response_ai_model: str | None = None
    audio_chunks: list[bytes] = field(default_factory=list)
    audio_mime_type: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    tts_speed_ratio: float = DEFAULT_TTS_SPEED_RATIO
    tts_duration_seconds: float | None = None
    tts_alignment: list = field(default_factory=list)
    memory_activation_trace_id: str | None = None
    memory_activation_status: str | None = None
    memory_activation_injected_count: int = 0

    def append_tts_chunk(self, chunk: StreamingTTSChunk) -> None:
        if chunk.audio:
            self.audio_chunks.append(chunk.audio)
            self.audio_mime_type = self.audio_mime_type or chunk.mime_type
            self.tts_provider = self.tts_provider or chunk.provider
            self.tts_model = self.tts_model or chunk.model
            self.tts_voice = self.tts_voice or chunk.voice
            self.tts_speed_ratio = chunk.speed_ratio
        if chunk.is_final:
            self.audio_mime_type = self.audio_mime_type or chunk.mime_type
            self.tts_provider = self.tts_provider or chunk.provider
            self.tts_model = self.tts_model or chunk.model
            self.tts_voice = self.tts_voice or chunk.voice
            self.tts_speed_ratio = chunk.speed_ratio
            self.tts_duration_seconds = chunk.duration_seconds
            self.tts_alignment.extend(chunk.alignment)

    @property
    def ai_model_mismatch(self) -> bool:
        return bool(
            self.resolved_ai_model and self.response_ai_model and self.resolved_ai_model != self.response_ai_model
        )

    @property
    def audio(self) -> bytes:
        return b"".join(self.audio_chunks)


@dataclass(frozen=True)
class AssistantTurnCancelSignal:
    reason: str = "cancelled"
    client_disconnected: bool = False

    @property
    def state(self) -> str:
        return "interrupted" if self.reason == "interrupted_by_user" else "cancelled"


class VoiceAssistantTurnStreamSession:
    def __init__(
        self,
        *,
        db: AsyncSession,
        client: AssistantTurnStreamClient,
        voice_session_id: str,
        user_id: str,
        ai_client_factory=get_ai_service,
        policy_ai_client_factory=get_ai_service,
        tts_client_factory=get_tts_client,
    ) -> None:
        self.db = db
        self.client = client
        self.voice_session_id = voice_session_id
        self.user_id = user_id
        self.ai_client_factory = ai_client_factory
        self.policy_ai_client_factory = policy_ai_client_factory
        self.tts_client_factory = tts_client_factory

    async def run(self) -> None:
        await self.client.send_json(
            {
                "type": "turn.ready",
                "voice_session_id": self.voice_session_id,
                "state": "ready",
            }
        )
        message = await self.client.receive_json()
        message_type = message.get("type")
        if message_type in {"connection.close", "turn.cancel"}:
            return
        if message_type != "turn.start":
            await self.client.send_json(
                {
                    "type": "turn.failed",
                    "voice_session_id": self.voice_session_id,
                    "error": "turn_start_required",
                }
            )
            return
        await self._run_turn(AssistantTurnStartRequest.from_payload(message))

    async def _run_turn(self, req: AssistantTurnStartRequest) -> None:
        turn_id = str(uuid.uuid4())
        cancel_event = asyncio.Event()
        cancel_task = asyncio.create_task(self._wait_for_cancel(cancel_event))
        stream_task = asyncio.create_task(self._stream_turn(turn_id, req, cancel_event))
        try:
            done, _pending = await asyncio.wait(
                {cancel_task, stream_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_task in done:
                try:
                    cancel_signal = await cancel_task
                except Exception:
                    cancel_signal = AssistantTurnCancelSignal(reason="connection_close", client_disconnected=True)
                cancel_event.set()
                stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream_task
                await self.db.rollback()
                if cancel_signal.client_disconnected:
                    return
                await self.client.send_json(
                    {
                        "type": "turn.cancelled",
                        "turn_id": turn_id,
                        "voice_session_id": self.voice_session_id,
                        "state": cancel_signal.state,
                        "reason": cancel_signal.reason,
                    }
                )
                return

            cancel_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_task

            try:
                await stream_task
            except Exception as exc:
                await self.db.rollback()
                logger.exception(
                    "Streaming assistant turn failed voice_session_id=%s turn_id=%s",
                    self.voice_session_id,
                    turn_id,
                )
                await self.client.send_json(
                    {
                        "type": "turn.failed",
                        "turn_id": turn_id,
                        "voice_session_id": self.voice_session_id,
                        "state": "failed",
                        "error": _exception_message(exc),
                    }
                )
        finally:
            cancel_event.set()
            for task in (cancel_task, stream_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(cancel_task, stream_task, return_exceptions=True)

    async def _wait_for_cancel(self, cancel_event: asyncio.Event) -> AssistantTurnCancelSignal:
        while not cancel_event.is_set():
            message = await self.client.receive_json()
            message_type = message.get("type")
            if message_type == "connection.close":
                cancel_event.set()
                return AssistantTurnCancelSignal(reason="connection_close", client_disconnected=True)
            if message_type == "turn.cancel":
                cancel_event.set()
                return AssistantTurnCancelSignal(reason=_cancel_reason(message))
        return AssistantTurnCancelSignal()

    async def _stream_turn(
        self,
        turn_id: str,
        req: AssistantTurnStartRequest,
        cancel_event: asyncio.Event,
    ) -> None:
        voice_session = await load_voice_session(self.db, self.voice_session_id, self.user_id)
        req = replace(
            req,
            ai_model=await load_project_ai_model(
                self.db,
                voice_session.project_id,
                req.ai_model,
            ),
        )
        has_previous_project_narration = await project_has_previous_narration(
            self.db,
            voice_session,
        )
        messages = list(voice_session.messages)
        has_user_message = _has_user_message(messages)
        fallback_intent = "follow_up" if has_user_message else "opening"
        max_tokens = _llm_max_tokens(
            voice_session,
            has_user_message=has_user_message,
            regenerate=req.regenerate,
        )
        book_completion = voice_session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value
        guided = voice_session.guidance_mode == MemoirGuidanceMode.GUIDED_INTERVIEW.value
        book_context = None
        # Progress is only offered at an opening; mid-story turns leave events unannounced.
        project_progress_context = ProgressPromptContext(prompt_text="")
        if book_completion:
            project_progress_context = ProgressPromptContext(
                prompt_text=(
                    "当前是单项成书补访。不要播报全书进度、其他章节状态、未归章数量或后台任务；只围绕当前补全问题回应。"
                ),
                progress_update=None,
                automation_event_ids=[],
                voice_announcement_event_ids=[],
            )
        elif guided or not has_user_message:
            snapshot = await progress_snapshot.get_project_progress_snapshot(
                self.db,
                user_id=self.user_id,
                project_id=voice_session.project_id,
                voice_unannounced_events_only=True,
            )
            if not has_user_message:
                project_progress_context = build_progress_prompt_context(snapshot)
            book_context = await load_interview_book_context(
                self.db,
                voice_session=voice_session,
                snapshot=snapshot,
            )
        completion_prompt_context = await completion_interviews.prompt_context_for_session(
            self.db,
            voice_session=voice_session,
        )
        tts_client = self.tts_client_factory()
        persona = interviewer_persona_for_voice(req.voice or tts_client.default_voice)
        ai_client = self.ai_client_factory(req.ai_model)
        artifacts = AssistantTurnArtifacts(
            ai_provider=ai_client.provider_name,
            requested_ai_model=req.ai_model,
            resolved_ai_model=ai_client.model_name,
        )

        await self.client.send_json(
            {
                "type": "turn.started",
                "turn_id": turn_id,
                "voice_session_id": self.voice_session_id,
                "state": "streaming",
                "ai_provider": ai_client.provider_name,
                "ai_model": ai_client.model_name,
                "requested_ai_model": req.ai_model,
                "resolved_ai_model": ai_client.model_name,
                "tts_provider": tts_client.provider_name,
                "tts_model": tts_client.model_name,
                "host_name": persona.host_name,
                "audio_protocol": req.audio_protocol,
                "max_tokens": max_tokens,
                "current_completion_issue_id": (
                    completion_prompt_context.issue_id if completion_prompt_context else None
                ),
            }
        )

        current_user_message = next(
            (
                message
                for message in reversed(messages)
                if message.role == MemoirChatMessageRole.USER.value and message.content_text.strip()
            ),
            None,
        )
        activation_context = ""
        # Free narration keeps the AI a listener, so it gets no earlier material to ask about.
        if current_user_message is not None and (book_completion or guided):
            try:
                activation = await memory_activation.activate_memory_for_turn(
                    self.db,
                    project_id=voice_session.project_id,
                    voice_session_id=voice_session.id,
                    current_message=current_user_message,
                    context_message_ids=tuple(item.id for item in recent_context_messages(messages)),
                )
                activation_context = activation.context
                artifacts.memory_activation_trace_id = activation.trace_id
                artifacts.memory_activation_status = activation.status
                artifacts.memory_activation_injected_count = activation.injected_count
            except Exception:
                await self.db.rollback()
                artifacts.memory_activation_status = "failed"
                logger.exception(
                    "Memory activation orchestration failed; continuing without "
                    "historical context voice_session_id=%s turn_id=%s",
                    self.voice_session_id,
                    turn_id,
                )
            voice_session = await load_voice_session(
                self.db,
                self.voice_session_id,
                self.user_id,
            )

        policy_turn: interview_policy_runtime.ActivePolicyTurn | None = None
        completion_answered = bool(completion_prompt_context is not None and completion_prompt_context.answered)
        if not completion_answered:
            try:
                async with self.db.begin_nested():
                    policy_turn = await interview_policy_runtime.prepare_active_policy_turn(
                        self.db,
                        session=voice_session,
                        completion_issue_id=(completion_prompt_context.issue_id if completion_prompt_context else None),
                        requested_ai_model=req.ai_model,
                        ai_client_factory=self.policy_ai_client_factory,
                    )
            except Exception:
                logger.exception(
                    "Active interview policy selection failed; continuing with "
                    "the existing reply flow voice_session_id=%s turn_id=%s",
                    voice_session.id,
                    turn_id,
                )

        prompt = build_stream_turn_prompt(
            voice_session,
            has_previous_project_narration=has_previous_project_narration,
            activation_context=activation_context,
            regenerate=req.regenerate,
            progress_context=project_progress_context,
            persona=persona,
            completion_prompt_context=completion_prompt_context,
            book_context=book_context,
            policy_instruction=interview_policy_runtime.active_reply_instruction(policy_turn),
        )

        await self._stream_text_and_audio(
            ai_client=ai_client,
            tts_client=tts_client,
            system_prompt=system_prompt_for_persona(persona),
            prompt=prompt,
            req=req,
            max_tokens=max_tokens,
            turn_id=turn_id,
            artifacts=artifacts,
            cancel_event=cancel_event,
        )
        if cancel_event.is_set():
            return
        turn = _parse_interviewer_turn(artifacts.text, fallback_intent=fallback_intent)
        if (
            voice_session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value
            and has_user_message
            and turn.expects_answer
        ):
            turn = replace(turn, expects_answer=False)
        if not turn.speech_text:
            raise HTTPException(status_code=502, detail="AI 未返回可用访谈回应")
        message, audio_file_id = await self._persist_completed_turn(
            voice_session=voice_session,
            turn=turn,
            artifacts=artifacts,
            ai_client=ai_client,
            max_tokens=max_tokens,
            req=req,
            progress_context=project_progress_context,
            completion_prompt_context=completion_prompt_context,
        )
        await automation_events.mark_automation_events_voice_selected(
            self.db,
            user_id=self.user_id,
            project_id=voice_session.project_id,
            event_ids=project_progress_context.voice_announcement_event_ids or [],
            voice_session_id=voice_session.id,
            message_id=message.id,
        )
        persisted_message_id = message.id
        try:
            if not completion_answered:
                if policy_turn is not None:
                    await interview_policy_runtime.persist_policy_turn_decision(
                        self.db,
                        session=voice_session,
                        generated_message=message,
                        policy_turn=policy_turn,
                    )
                else:
                    await interview_policy_runtime.schedule_shadow_policy_evaluation(
                        self.db,
                        session=voice_session,
                        generated_message=message,
                        completion_issue_id=(completion_prompt_context.issue_id if completion_prompt_context else None),
                        requested_ai_model=req.ai_model,
                    )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            logger.exception(
                "Failed to persist interview policy result voice_session_id=%s message_id=%s",
                self.voice_session_id,
                persisted_message_id,
            )
            if policy_turn is not None:
                try:
                    fallback_session = await load_voice_session(
                        self.db,
                        self.voice_session_id,
                        self.user_id,
                    )
                    fallback_message = await self.db.get(
                        MemoirChatMessage,
                        persisted_message_id,
                    )
                    if fallback_message is None:
                        raise RuntimeError("Persisted assistant message unavailable for shadow fallback")
                    await interview_policy_runtime.schedule_shadow_policy_evaluation(
                        self.db,
                        session=fallback_session,
                        generated_message=fallback_message,
                        completion_issue_id=(completion_prompt_context.issue_id if completion_prompt_context else None),
                        requested_ai_model=req.ai_model,
                    )
                    await self.db.commit()
                except Exception:
                    await self.db.rollback()
                    logger.exception(
                        "Failed to schedule interview policy shadow fallback voice_session_id=%s message_id=%s",
                        self.voice_session_id,
                        persisted_message_id,
                    )
        await self.client.send_json(
            {
                "type": "turn.completed",
                "turn_id": turn_id,
                "voice_session_id": self.voice_session_id,
                "project_id": voice_session.project_id,
                "state": "completed",
                "message_id": message.id,
                "audio_file_id": audio_file_id,
                "text": turn.speech_text,
                "primary_intent": turn.primary_intent,
                "intent_tags": turn.intent_tags,
                "expects_answer": turn.expects_answer,
                "host_name": persona.host_name,
                "finish_reason": artifacts.finish_reason,
                "usage": artifacts.usage,
                "requested_ai_model": artifacts.requested_ai_model,
                "resolved_ai_model": artifacts.resolved_ai_model,
                "response_ai_model": artifacts.response_ai_model,
                "ai_model_mismatch": artifacts.ai_model_mismatch,
                "progress_update": project_progress_context.progress_update,
                "progress_update_used": project_progress_context.progress_update is not None,
                "progress_spoken_text": "",
                "progress_voice_selected_event_ids": project_progress_context.voice_announcement_event_ids or [],
                "current_completion_issue_id": (
                    completion_prompt_context.issue_id if completion_prompt_context else None
                ),
            }
        )

    async def _stream_text_and_audio(
        self,
        *,
        ai_client: AITextClient,
        tts_client: TTSClient,
        system_prompt: str,
        prompt: str,
        req: AssistantTurnStartRequest,
        max_tokens: int,
        turn_id: str,
        artifacts: AssistantTurnArtifacts,
        cancel_event: asyncio.Event,
    ) -> None:
        text_queue: asyncio.Queue[str | None] = asyncio.Queue()
        audio_task = asyncio.create_task(
            self._stream_audio_from_text_queue(
                tts_client=tts_client,
                text_queue=text_queue,
                req=req,
                turn_id=turn_id,
                artifacts=artifacts,
                cancel_event=cancel_event,
            )
        )

        async def produce_text() -> None:
            await self._stream_text(
                ai_client=ai_client,
                system_prompt=system_prompt,
                prompt=prompt,
                req=req,
                max_tokens=max_tokens,
                turn_id=turn_id,
                artifacts=artifacts,
                cancel_event=cancel_event,
                text_queue=text_queue,
            )
            await text_queue.put(None)
            if cancel_event.is_set():
                audio_task.cancel()

        text_task = asyncio.create_task(produce_text())
        tasks = (text_task, audio_task)
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _stream_text(
        self,
        *,
        ai_client: AITextClient,
        system_prompt: str,
        prompt: str,
        req: AssistantTurnStartRequest,
        max_tokens: int,
        turn_id: str,
        artifacts: AssistantTurnArtifacts,
        cancel_event: asyncio.Event,
        text_queue: asyncio.Queue[str | None],
    ) -> None:
        initial_text_buffer = ""
        initial_text_resolved = False
        async for chunk in stream_text_with_client(
            ai_client,
            system_prompt,
            prompt,
            requested_model=req.ai_model,
            temperature=req.temperature,
            max_tokens=max_tokens,
            timeout=ai_text_timeout_seconds(AITextTimeoutProfile.INTERACTIVE),
        ):
            if cancel_event.is_set():
                return
            artifacts.finish_reason = chunk.finish_reason or artifacts.finish_reason
            if chunk.usage:
                artifacts.usage.update(chunk.usage)
            artifacts.ai_provider = chunk.provider or artifacts.ai_provider
            artifacts.requested_ai_model = chunk.requested_model or artifacts.requested_ai_model
            artifacts.resolved_ai_model = chunk.resolved_model or artifacts.resolved_ai_model
            artifacts.response_ai_model = chunk.response_model or artifacts.response_ai_model
            if not chunk.text_delta:
                continue
            text_delta = chunk.text_delta
            if not initial_text_resolved:
                initial_text_buffer += text_delta
                text_delta, initial_text_resolved = strip_streaming_speaker_label_prefix(initial_text_buffer)
                if not initial_text_resolved:
                    continue
            if not text_delta:
                continue
            artifacts.text += text_delta
            await text_queue.put(text_delta)
            await self.client.send_json(
                {
                    "type": "text.delta",
                    "turn_id": turn_id,
                    "voice_session_id": self.voice_session_id,
                    "text_delta": text_delta,
                    "text": artifacts.text,
                }
            )

    async def _stream_audio_from_text_queue(
        self,
        *,
        tts_client: TTSClient,
        text_queue: asyncio.Queue[str | None],
        req: AssistantTurnStartRequest,
        turn_id: str,
        artifacts: AssistantTurnArtifacts,
        cancel_event: asyncio.Event,
    ) -> None:
        tts_character_count = 0

        async def tracked_text_chunks() -> AsyncIterator[str]:
            nonlocal tts_character_count
            async for text in chunk_text_for_tts(
                queued_text_chunks(text_queue, cancel_event),
                cancel_event,
            ):
                tts_character_count += len(text)
                yield text

        try:
            async with contextlib.aclosing(tracked_text_chunks()) as text_chunks:
                await stream_tts_events(
                    client=self.client,
                    tts_client=tts_client,
                    text_chunks=text_chunks,
                    event_context={"turn_id": turn_id, "voice_session_id": self.voice_session_id},
                    voice=req.voice,
                    speed_ratio=req.speed_ratio,
                    cancel_event=cancel_event,
                    audio_protocol=req.audio_protocol,
                    on_chunk=artifacts.append_tts_chunk,
                )
        except asyncio.CancelledError:
            # Interrupted turn: the text already sent was synthesized upstream.
            if tts_character_count > 0:
                record_ai_usage_in_background(
                    provider=tts_client.provider_name,
                    model=tts_client.model_name,
                    status="cancelled",
                    cost=estimate_text_cost(
                        provider=tts_client.provider_name,
                        model=tts_client.model_name,
                        character_count=tts_character_count,
                    ),
                    input_characters=tts_character_count,
                    billable=True,
                )
            raise
        except Exception as exc:
            if tts_character_count > 0:
                await record_ai_usage(
                    provider=tts_client.provider_name,
                    model=tts_client.model_name,
                    status="failed",
                    cost=estimate_text_cost(
                        provider=tts_client.provider_name,
                        model=tts_client.model_name,
                        character_count=tts_character_count,
                    ),
                    input_characters=tts_character_count,
                    error_type=type(exc).__name__,
                )
            raise
        if tts_character_count > 0:
            await record_ai_usage(
                provider=tts_client.provider_name,
                model=tts_client.model_name,
                status="succeeded",
                cost=estimate_text_cost(
                    provider=tts_client.provider_name,
                    model=tts_client.model_name,
                    character_count=tts_character_count,
                ),
                input_characters=tts_character_count,
            )

    async def _persist_completed_turn(
        self,
        *,
        voice_session: MemoirVoiceSession,
        turn: InterviewerTurn,
        artifacts: AssistantTurnArtifacts,
        ai_client: AITextClient,
        max_tokens: int,
        req: AssistantTurnStartRequest,
        progress_context: ProgressPromptContext,
        completion_prompt_context: (completion_interviews.CompletionIssuePromptContext | None),
    ) -> tuple[MemoirChatMessage, str | None]:
        if not turn.speech_text:
            raise HTTPException(status_code=502, detail="AI 未返回可用访谈回应")
        staged_audio: StagedAudioFile | None = None
        persona = interviewer_persona_for_voice(req.voice or artifacts.tts_voice)
        if artifacts.audio:
            mime_type, extension = _storage_audio_type(artifacts.audio_mime_type)
            staged_audio = await stage_audio_bytes(
                self.db,
                self.user_id,
                artifacts.audio,
                mime_type=mime_type,
                extension=extension,
                duration_seconds=(
                    math.ceil(artifacts.tts_duration_seconds) if artifacts.tts_duration_seconds is not None else None
                ),
            )
        try:
            message = MemoirChatMessage(
                project_id=voice_session.project_id,
                voice_session_id=voice_session.id,
                role=MemoirChatMessageRole.ASSISTANT.value,
                content_text=turn.speech_text,
                content_status=MemoirChatContentStatus.FINAL.value,
                audio_file_id=staged_audio.audio_file.id if staged_audio else None,
                guidance_intent=turn.primary_intent,
                turn_index=await next_turn_index(self.db, voice_session.id),
                metadata_json={
                    "source": "ai.streaming_assistant_turn",
                    "prompt_version": STREAM_PROMPT_VERSION,
                    "progress_prompt_version": PROGRESS_PROMPT_VERSION,
                    "progress_update": progress_context.progress_update,
                    "progress_update_used": progress_context.progress_update is not None,
                    "progress_automation_event_ids": progress_context.automation_event_ids or [],
                    "progress_voice_selected_event_ids": progress_context.voice_announcement_event_ids or [],
                    "progress_spoken_text": "",
                    "current_completion_issue_id": (
                        completion_prompt_context.issue_id if completion_prompt_context else None
                    ),
                    "memory_activation_trace_id": artifacts.memory_activation_trace_id,
                    "memory_activation_status": artifacts.memory_activation_status,
                    "memory_activation_injected_count": artifacts.memory_activation_injected_count,
                    "primary_intent": turn.primary_intent,
                    "intent_tags": turn.intent_tags,
                    "expects_answer": turn.expects_answer,
                    "parsed_json": turn.parsed_json,
                    "provider": artifacts.ai_provider or ai_client.provider_name,
                    "model": (artifacts.resolved_ai_model or ai_client.model_name),
                    "requested_model": artifacts.requested_ai_model,
                    "resolved_model": (artifacts.resolved_ai_model or ai_client.model_name),
                    "response_model": artifacts.response_ai_model,
                    "model_mismatch": artifacts.ai_model_mismatch,
                    "max_tokens": max_tokens,
                    "temperature": req.temperature,
                    "regenerate": req.regenerate,
                    "host_name": persona.host_name,
                    "host_voice_gender": persona.voice_gender,
                    "response_chars": len(turn.speech_text),
                    "question_mark_count": _question_mark_count(turn.speech_text),
                    "finish_reason": artifacts.finish_reason,
                    "usage": artifacts.usage,
                    "tts_provider": artifacts.tts_provider,
                    "tts_model": artifacts.tts_model,
                    "tts_voice": artifacts.tts_voice,
                    "tts_mime_type": artifacts.audio_mime_type,
                    "tts_speed_ratio": artifacts.tts_speed_ratio,
                    "tts_duration_seconds": artifacts.tts_duration_seconds,
                    "tts_alignment": alignment_segments_to_json(artifacts.tts_alignment),
                    "tts_timing_source": artifacts.tts_provider if artifacts.tts_alignment else None,
                    "turn_pipeline": "streaming_llm_tts",
                },
            )
            self.db.add(message)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            await discard_staged_audio(staged_audio)
            raise
        await self.db.refresh(message)
        return message, staged_audio.audio_file.id if staged_audio else None


def _storage_audio_type(mime_type: str | None) -> tuple[str, str]:
    normalized = (mime_type or "audio/pcm").split(";")[0].strip().lower()
    if normalized in _PCM_MIME_TYPES:
        return "audio/pcm", ".pcm"
    if normalized in {"audio/mpeg", "audio/mp3"}:
        return "audio/mpeg", ".mp3"
    if normalized in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return "audio/wav", ".wav"
    if normalized == "audio/ogg":
        return "audio/ogg", ".ogg"
    return "audio/pcm", ".pcm"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cancel_reason(message: dict) -> str:
    reason = _optional_text(message.get("reason"))
    if not reason:
        return "cancelled"
    return reason[:80]


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc) or exc.__class__.__name__
