from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from dataclasses import dataclass, field, replace
from typing import Protocol
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirSessionGoal,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.services.ai_calls import stream_text_with_client
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
from app.services.memoir.voice_followup_ai import (
    GUIDED_TOPIC_MENU,
    PROGRESS_PROMPT_VERSION,
    InterviewerPersona,
    InterviewerTurn,
    ProgressPromptContext,
    build_progress_prompt_context,
    _has_user_message,
    _llm_max_tokens,
    _module_label,
    _parse_interviewer_turn,
    _question_mark_count,
    _recent_context,
    recent_context_messages,
    _requested_topic_hint,
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
from app.services.memoir.voice_tts import get_tts_client
from app.services.memoir.voice_tts_types import (
    DEFAULT_TTS_SPEED_RATIO,
    TTSClient,
    StreamingTTSChunk,
)


STREAM_PROMPT_VERSION = "memoir_voice_interviewer_stream_turn_v3"
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
        if voice_session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value:
            project_progress_context = ProgressPromptContext(
                prompt_text=(
                    "当前是单项成书补访。不要播报全书进度、其他章节状态、未归章数量或后台任务；只围绕当前补全问题回应。"
                ),
                progress_update=None,
                automation_event_ids=[],
                speech_prefix="",
                voice_announcement_event_ids=[],
            )
        else:
            project_progress_context = build_progress_prompt_context(
                await progress_snapshot.get_project_progress_snapshot(
                    self.db,
                    user_id=self.user_id,
                    project_id=voice_session.project_id,
                    voice_unannounced_events_only=True,
                )
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
        if current_user_message is not None:
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
        anchor_instruction = ""
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
        if policy_turn is None:
            try:
                anchor_instruction = await interview_policy_runtime.anchored_opening_reply_instruction(
                    self.db,
                    session=voice_session,
                )
            except Exception:
                logger.exception(
                    "Topic anchor opening preparation failed; continuing with "
                    "the existing reply flow voice_session_id=%s turn_id=%s",
                    voice_session.id,
                    turn_id,
                )

        prompt = _build_stream_turn_prompt(
            voice_session,
            has_previous_project_narration=has_previous_project_narration,
            activation_context=activation_context,
            regenerate=req.regenerate,
            progress_context=project_progress_context,
            persona=persona,
            completion_prompt_context=completion_prompt_context,
            policy_instruction=interview_policy_runtime.active_reply_instruction(policy_turn) or anchor_instruction,
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
            progress_context=project_progress_context,
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
                "progress_spoken_text": project_progress_context.speech_prefix,
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
        progress_context: ProgressPromptContext,
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
        try:
            await self._stream_progress_prefix(
                text_queue=text_queue,
                turn_id=turn_id,
                artifacts=artifacts,
                progress_context=progress_context,
                cancel_event=cancel_event,
            )
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
                with contextlib.suppress(asyncio.CancelledError):
                    await audio_task
                return
            await audio_task
        except Exception:
            audio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await audio_task
            raise

    async def _stream_progress_prefix(
        self,
        *,
        text_queue: asyncio.Queue[str | None],
        turn_id: str,
        artifacts: AssistantTurnArtifacts,
        progress_context: ProgressPromptContext,
        cancel_event: asyncio.Event,
    ) -> None:
        text = progress_context.speech_prefix.strip()
        if not text or cancel_event.is_set():
            return
        artifacts.text += text
        await text_queue.put(text)
        await self.client.send_json(
            {
                "type": "text.delta",
                "turn_id": turn_id,
                "voice_session_id": self.voice_session_id,
                "text_delta": text,
                "text": artifacts.text,
                "source": "progress_announcement",
            }
        )

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
        await stream_tts_events(
            client=self.client,
            tts_client=tts_client,
            text_chunks=chunk_text_for_tts(
                queued_text_chunks(text_queue, cancel_event),
                cancel_event,
            ),
            event_context={"turn_id": turn_id, "voice_session_id": self.voice_session_id},
            voice=req.voice,
            speed_ratio=req.speed_ratio,
            cancel_event=cancel_event,
            audio_protocol=req.audio_protocol,
            on_chunk=artifacts.append_tts_chunk,
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
                    "progress_spoken_text": progress_context.speech_prefix,
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


def _build_stream_turn_prompt(
    voice_session: MemoirVoiceSession,
    *,
    has_previous_project_narration: bool,
    activation_context: str,
    regenerate: bool,
    progress_context: ProgressPromptContext,
    persona: InterviewerPersona,
    completion_prompt_context: (completion_interviews.CompletionIssuePromptContext | None),
    policy_instruction: str = "",
) -> str:
    messages = list(voice_session.messages)
    has_user_message = _has_user_message(messages)
    context = _recent_context(messages)
    module_label = _module_label(voice_session.current_guidance_module)
    topic_hint = _requested_topic_hint(messages)
    host_name = persona.host_name
    regenerate_instruction = "这次是在替换上一句回应，请换一个角度，不要重复上一句。" if regenerate else ""
    policy_section = f"\n当前受控访谈策略：\n{policy_instruction}\n" if policy_instruction else ""
    if voice_session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value:
        if has_user_message:
            mode_instruction = """当前是成书补访，只围绕“当前补全问题”推进。
- 先承接叙述者刚才真正回答到的内容，用一句话感谢并说明这一段已经保存
- 不再继续追问细节，也不要自动切换到下一题；后台会核对内容，用户随后回到全书概览自行选择
- 如果回答跑题，先自然接住，再温和带回当前唯一目标
- 不要重复询问已经列为“不要重复追问”的事实"""
        elif completion_prompt_context is not None:
            mode_instruction = f"""当前是成书补访首次开场。
- 最多两句，第一句简短说明今天只补一件事
- 第二句自然地提出这个具体问题：{completion_prompt_context.question}
- 不要展示任务 ID，不要念系统说明，不要扩展成多个问题"""
        else:
            mode_instruction = """当前成书补访已经没有待回答的问题。
- 简短说明本轮已经结束，请用户返回全书概览
- 不要继续提问，也不要引导用户再次录音"""
    elif voice_session.guidance_mode == "guided_interview":
        if has_user_message:
            mode_instruction = f"""当前是主题讲述，当前主题线索是“{module_label}”；如果最近对话中叙述者已经选择了其他主题，以最近对话为准。
- 如果叙述者明确说想换主题，下一句必须围绕新主题展开，不要继续旧话题
- 优先顺着当前故事的势能继续听或只追一个自然方向，不要为了“完整”机械补齐时间、地点、人物、起因和结果
- 只有确实影响理解时，才询问背景、人物关系、原因、结果、转场或感受
- 不要连续追同一个细节超过两轮；当前受控访谈策略存在时，以它为最高优先级"""
        elif has_previous_project_narration:
            mode_instruction = f"""当前是主题讲述继续开场，叙述者之前已有同模式讲述记录。
- 最多两句：第一句简短欢迎回来；第二句只问“今天想先讲哪一段？”
- 当前没有检索具体历史内容，不要猜测或概括上次聊过什么
- 可以自然给出几个轻选项，但不要念成长列表，选项来自：{GUIDED_TOPIC_MENU}
- 必须说明想不起来也没关系，可以随便聊"""
        else:
            mode_instruction = f"""当前是主题讲述首次开场。
- 最多两句：第一句用“{host_name}”做很短的自我介绍并说明可以慢慢聊；第二句问“今天想先讲哪一段？”
- 可以自然带出 3 到 5 个轻选项，但不要念成长列表，选项来自：{GUIDED_TOPIC_MENU}
- 明确说想不起来也没关系，可以随便聊
- 不要立刻进入历史细节"""
    else:
        if has_user_message:
            mode_instruction = """当前是自由讲述，AI 只做弱引导。
- 顺着叙述者自己的时间线走，尽量不要突然换主题
- 如果这件事重要、有情绪或有转折，鼓励他多讲一点
- 如果这件事已经讲得差不多，就自然引导到前后时间、相关人物，或下一段回忆"""
        elif has_previous_project_narration:
            mode_instruction = """当前是自由讲述开场，AI 只做弱引导，叙述者之前已有讲述记录。
- 最多两句：第一句简短欢迎回来；第二句给一个很小的开口
- 当前没有检索具体历史内容，不要猜测或概括上次聊过什么
- 结尾可以问“今天想接着讲哪一段？”或“先说一件想起来的小事也可以”"""
        else:
            mode_instruction = f"""当前是自由讲述开场，AI 只做弱引导。
- 最多两句：第一句用“{host_name}”做很短的自我介绍并说明可以慢慢聊；第二句给一个很小的开口
- 可以提示“先说一件想起来的小事”或“从一个人、一件事、一个地方开始都可以”
- 不要连续提问，只给一个开放入口"""
    return f"""请根据下面的访谈上下文，直接生成下一句“{host_name}”要说的话。

输出要求：
- 只输出要播放的中文口语正文，不要 JSON，不要 Markdown，不要解释
- 不要在正文前加“{host_name}：”“老余：”“小余：”“AI：”等说话人标签；自称只能出现在自然句子里，标签不属于可播放内容
- 你可以自主组合动作：承接认可、鼓励继续讲、小结、自然换方向、继续追问，或没听清/太短时温和接住
- 普通回应 1 到 2 句，建议 35 到 75 个汉字
- 开场最多 2 句，建议 45 到 70 个汉字；先轻提示，再给一个明确问题，不要说成长开场白
- 不要上来就追问历史细节，不要给叙述者压力
- 不要机械重复叙述者刚说过的话；认可必须落在具体内容上，但不要把用户原话换个说法再说一遍
- 如果要问问题，一次只问一个自然问题
- 不要编造上下文没有的信息；只有“历史讲述候选”中出现的内容才能作为跨会话事实使用
- 如果用户最新一句明确要求换方向，必须尊重最新一句
{regenerate_instruction}
{topic_hint}

项目进度上下文：
{progress_context.prompt_text}

{mode_instruction}
{policy_section}

当前补全问题：
{completion_prompt_context.prompt_text if completion_prompt_context else "本轮没有当前补全问题，按当前引导模式继续。"}

访谈上下文：
{context}

历史讲述候选：
{activation_context or "本轮没有检索到历史讲述候选。"}
"""


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
