from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.memoir_book import (
    MemoirBookVoiceCommandBookContext,
    MemoirBookVoiceCommandContextMessage,
    MemoirBookVoiceCommandExecuteRequest,
    MemoirBookVoiceCommandExecuteResponse,
    MemoirBookVoiceCommandParseRequest,
    MemoirBookVoiceCommandParseResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_source_links import source_count_by_chapter
from app.services.memoir.book_voice_command_executor import execute_confirmed_book_voice_command
from app.services.memoir.book_voice_command_parser import (
    CONSULTATION_INTENTS,
    EXECUTABLE_MUTATION_INTENTS,
    response_from_payload,
    parse_payload_with_ai,
)
from app.services.memoir.book_voice_commands import load_book_plan
from app.services.memoir.streaming_tts_events import (
    STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME,
    normalize_audio_protocol,
    single_text_chunk,
    stream_tts_events,
)
from app.services.memoir.voice_assistant_turn_stream import (
    AssistantTurnArtifacts,
    AssistantTurnCancelSignal,
    _cancel_reason,
    _exception_message,
)
from app.services.memoir.voice_tts import TTSClient, get_tts_client


logger = logging.getLogger(__name__)
BOOK_VOICE_ASSISTANT_TURN_PIPELINE = "book_voice_editing_turn_stream"
BOOK_VOICE_ASSISTANT_TRANSIENT_SESSION_PREFIX = "book-voice-command"


class BookVoiceAssistantTurnClient(Protocol):
    async def receive_json(self) -> dict:
        ...

    async def send_json(self, payload: dict) -> None:
        ...


@dataclass(frozen=True)
class BookVoiceAssistantTurnStartRequest:
    text: str
    ai_model: str | None = None
    voice: str | None = None
    speed_ratio: float | None = None
    audio_protocol: str = STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME
    current_chapter_id: str | None = None
    conversation_context: list[MemoirBookVoiceCommandContextMessage] = field(default_factory=list)
    book_context: MemoirBookVoiceCommandBookContext | None = None
    pending_command: MemoirBookVoiceCommandParseResponse | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> "BookVoiceAssistantTurnStartRequest":
        speed_ratio = payload.get("speed_ratio")
        try:
            normalized_speed_ratio = float(speed_ratio) if speed_ratio is not None else None
        except (TypeError, ValueError):
            normalized_speed_ratio = None
        if normalized_speed_ratio is not None:
            normalized_speed_ratio = max(0.5, min(2.0, normalized_speed_ratio))
        return cls(
            text=str(payload.get("text") or "").strip(),
            ai_model=_optional_text(payload.get("ai_model")),
            voice=_optional_text(payload.get("voice")),
            speed_ratio=normalized_speed_ratio,
            audio_protocol=normalize_audio_protocol(payload.get("audio_protocol")),
            current_chapter_id=_optional_text(payload.get("current_chapter_id")),
            conversation_context=_conversation_context_from_payload(payload.get("conversation_context")),
            book_context=_book_context_from_payload(payload.get("book_context")),
            pending_command=_pending_command_from_payload(payload.get("pending_command")),
        )


@dataclass(frozen=True)
class BookVoiceAssistantTurnResult:
    reply_text: str
    command_preview: MemoirBookVoiceCommandParseResponse
    execute_after_reply: bool = False


class BookVoiceAssistantTurnStreamSession:
    def __init__(
        self,
        *,
        db: AsyncSession,
        client: BookVoiceAssistantTurnClient,
        project_id: str,
        user_id: str,
        ai_client_factory=get_ai_service,
        tts_client_factory=get_tts_client,
    ) -> None:
        self.db = db
        self.client = client
        self.project_id = project_id
        self.user_id = user_id
        self.ai_client_factory = ai_client_factory
        self.tts_client_factory = tts_client_factory
        self.transient_voice_session_id = (
            f"{BOOK_VOICE_ASSISTANT_TRANSIENT_SESSION_PREFIX}-{project_id}"
        )

    async def run(self) -> None:
        await self.client.send_json(
            {
                "type": "turn.ready",
                "voice_session_id": self.transient_voice_session_id,
                "project_id": self.project_id,
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
                    "voice_session_id": self.transient_voice_session_id,
                    "project_id": self.project_id,
                    "state": "failed",
                    "error": "turn_start_required",
                }
            )
            return
        await self._run_turn(BookVoiceAssistantTurnStartRequest.from_payload(message))

    async def _run_turn(self, req: BookVoiceAssistantTurnStartRequest) -> None:
        turn_id = str(uuid.uuid4())
        cancel_event = asyncio.Event()
        cancel_task = asyncio.create_task(self._wait_for_cancel(cancel_event))
        stream_task = asyncio.create_task(self._stream_turn(turn_id, req, cancel_event))
        done, _pending = await asyncio.wait(
            {cancel_task, stream_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stream_task in done:
            try:
                completed = await stream_task
            except Exception as exc:
                cancel_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_task
                await self.db.rollback()
                logger.exception(
                    "Book voice assistant turn failed project_id=%s turn_id=%s",
                    self.project_id,
                    turn_id,
                )
                await self.client.send_json(
                    {
                        "type": "turn.failed",
                        "turn_id": turn_id,
                        "voice_session_id": self.transient_voice_session_id,
                        "project_id": self.project_id,
                        "state": "failed",
                        "error": _exception_message(exc),
                    }
                )
                return

            if completed:
                cancel_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_task
                return

            if cancel_task in done:
                try:
                    cancel_signal = await cancel_task
                except Exception:
                    cancel_signal = AssistantTurnCancelSignal(
                        reason="connection_close",
                        client_disconnected=True,
                    )
            else:
                cancel_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_task
                cancel_signal = AssistantTurnCancelSignal()
            await self.db.rollback()
            if cancel_signal.client_disconnected:
                return
            await self.client.send_json(
                {
                    "type": "turn.cancelled",
                    "turn_id": turn_id,
                    "voice_session_id": self.transient_voice_session_id,
                    "project_id": self.project_id,
                    "state": cancel_signal.state,
                    "reason": cancel_signal.reason,
                }
            )
            return

        if cancel_task in done:
            try:
                cancel_signal = await cancel_task
            except Exception:
                cancel_signal = AssistantTurnCancelSignal(
                    reason="connection_close",
                    client_disconnected=True,
                )
            cancel_event.set()
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
            if cancel_signal.client_disconnected:
                await self.db.rollback()
                return
            await self.client.send_json(
                {
                    "type": "turn.cancelled",
                    "turn_id": turn_id,
                    "voice_session_id": self.transient_voice_session_id,
                    "project_id": self.project_id,
                    "state": cancel_signal.state,
                    "reason": cancel_signal.reason,
                }
            )
            return

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
        req: BookVoiceAssistantTurnStartRequest,
        cancel_event: asyncio.Event,
    ) -> bool:
        if not req.text:
            raise HTTPException(status_code=400, detail="语音指令不能为空")
        await load_memoir_project(self.db, self.project_id, self.user_id)
        plan = await load_book_plan(self.db, self.project_id)
        if not plan:
            raise HTTPException(status_code=404, detail="章节规划不存在")
        tts_client = self.tts_client_factory()
        await self.client.send_json(
            {
                "type": "turn.started",
                "turn_id": turn_id,
                "voice_session_id": self.transient_voice_session_id,
                "project_id": self.project_id,
                "state": "streaming",
                "ai_model": req.ai_model,
                "tts_provider": tts_client.provider_name,
                "tts_model": tts_client.model_name,
                "audio_protocol": req.audio_protocol,
                "turn_pipeline": BOOK_VOICE_ASSISTANT_TURN_PIPELINE,
            }
        )

        result = await self._resolve_turn_result(req, plan)
        await self._send_command_preview(turn_id, result)
        artifacts = AssistantTurnArtifacts()
        await self._stream_reply_audio(
            turn_id=turn_id,
            req=req,
            tts_client=tts_client,
            reply_text=result.reply_text,
            artifacts=artifacts,
            cancel_event=cancel_event,
        )
        if cancel_event.is_set():
            return False

        execution_result = None
        if result.execute_after_reply:
            execution_result = await self._execute_preview(result.command_preview)

        completed = {
            "type": "turn.completed",
            "turn_id": turn_id,
            "voice_session_id": self.transient_voice_session_id,
            "project_id": self.project_id,
            "state": "completed",
            "message_id": turn_id,
            "audio_file_id": None,
            "text": result.reply_text,
            "primary_intent": result.command_preview.intent,
            "intent_tags": [result.command_preview.intent],
            "expects_answer": True,
            "finish_reason": "book_voice_command_resolved",
            "usage": {},
            "command_preview": _model_dump(result.command_preview),
            "execution_result": _model_dump(execution_result) if execution_result else None,
            "turn_pipeline": BOOK_VOICE_ASSISTANT_TURN_PIPELINE,
            "tts_provider": artifacts.tts_provider,
            "tts_model": artifacts.tts_model,
            "tts_voice": artifacts.tts_voice,
            "tts_speed_ratio": artifacts.tts_speed_ratio,
            "tts_duration_seconds": artifacts.tts_duration_seconds,
        }
        await self.client.send_json(completed)
        return True

    async def _resolve_turn_result(
        self,
        req: BookVoiceAssistantTurnStartRequest,
        plan,
    ) -> BookVoiceAssistantTurnResult:
        if (
            req.pending_command
            and req.pending_command.requires_confirmation
            and _is_confirmation_text(req.text)
            and _can_execute_preview(req.pending_command)
        ):
            return BookVoiceAssistantTurnResult(
                reply_text="好的，我按刚才理解的修改。",
                command_preview=req.pending_command,
                execute_after_reply=True,
            )

        source_counts = await source_count_by_chapter(self.db, plan)
        parse_req = MemoirBookVoiceCommandParseRequest(
            text=req.text,
            current_chapter_id=req.current_chapter_id,
            conversation_context=req.conversation_context,
            book_context=req.book_context,
            ai_model=req.ai_model,
        )
        ai_client = self.ai_client_factory(req.ai_model)
        payload = await parse_payload_with_ai(
            parse_req,
            plan,
            source_count_by_chapter=source_counts,
            ai_client=ai_client,
        )
        preview = response_from_payload(
            req.text,
            plan,
            req.current_chapter_id,
            payload,
            req.book_context,
            source_count_by_chapter=source_counts,
        )
        return BookVoiceAssistantTurnResult(
            reply_text=_reply_text_for_preview(preview),
            command_preview=preview,
        )

    async def _send_command_preview(
        self,
        turn_id: str,
        result: BookVoiceAssistantTurnResult,
    ) -> None:
        await self.client.send_json(
            {
                "type": "command.preview",
                "turn_id": turn_id,
                "voice_session_id": self.transient_voice_session_id,
                "project_id": self.project_id,
                "command_preview": _model_dump(result.command_preview),
                "execute_after_reply": result.execute_after_reply,
            }
        )

    async def _stream_reply_audio(
        self,
        *,
        turn_id: str,
        req: BookVoiceAssistantTurnStartRequest,
        tts_client: TTSClient,
        reply_text: str,
        artifacts: AssistantTurnArtifacts,
        cancel_event: asyncio.Event,
    ) -> None:
        if not reply_text:
            raise HTTPException(status_code=502, detail="AI 未返回可用语音回复")
        artifacts.text = reply_text
        await self.client.send_json(
            {
                "type": "text.delta",
                "turn_id": turn_id,
                "voice_session_id": self.transient_voice_session_id,
                "project_id": self.project_id,
                "text_delta": reply_text,
                "text": reply_text,
                "source": "book_voice_command",
            }
        )
        await stream_tts_events(
            client=self.client,
            tts_client=tts_client,
            text_chunks=single_text_chunk(reply_text),
            event_context={"turn_id": turn_id, "voice_session_id": self.transient_voice_session_id},
            voice=req.voice,
            speed_ratio=req.speed_ratio,
            cancel_event=cancel_event,
            audio_protocol=req.audio_protocol,
            on_chunk=artifacts.append_tts_chunk,
        )

    async def _execute_preview(
        self,
        preview: MemoirBookVoiceCommandParseResponse,
    ) -> MemoirBookVoiceCommandExecuteResponse:
        operation = preview.operations[0] if preview.operations else None
        if not operation:
            raise HTTPException(status_code=400, detail="缺少可执行语音修改")
        return await execute_confirmed_book_voice_command(
            self.db,
            user_id=self.user_id,
            project_id=self.project_id,
            req=MemoirBookVoiceCommandExecuteRequest(
                intent=preview.intent,
                operation=operation,
            ),
        )


def _reply_text_for_preview(preview: MemoirBookVoiceCommandParseResponse) -> str:
    reply_text = (preview.reply_text or "").strip()
    if reply_text:
        return reply_text
    if preview.intent == "read_outline":
        return "好，我现在读目录。读完后我会继续听你说。"
    if preview.intent == "read_chapter":
        return "好，我来读这一章。读完后我会继续听你说。"
    if preview.response_kind == "answer":
        return preview.summary or "我可以回答当前目录状态，也可以帮你改章名、调顺序或合并章节。"
    if preview.intent == "clarify_previous":
        return preview.summary or "我还没确定你指的是哪一处，请直接说要改哪一章。"
    if preview.intent == "unknown":
        return "我还没理解清楚。你可以问现在有几章、第一章是不是太短，或者直接说把第一章改名为什么。"
    if preview.intent in CONSULTATION_INTENTS:
        return f"我的建议是：{preview.summary}"
    if preview.requires_confirmation and _can_execute_preview(preview):
        return f"我理解了，{preview.summary} 如果没问题，说确认执行，我再改。"
    return f"我理解到的是：{preview.summary} 这个操作现在先展示预览，不会直接改动。"


def _can_execute_preview(preview: MemoirBookVoiceCommandParseResponse) -> bool:
    if not preview.operations or preview.intent == "unknown":
        return False
    operation = preview.operations[0]
    if preview.intent == "read_outline":
        return True
    if preview.intent == "read_chapter":
        return bool(operation.chapter_id)
    if preview.intent not in EXECUTABLE_MUTATION_INTENTS:
        return False
    if preview.intent == "rename_chapter":
        return bool(operation.chapter_id and operation.new_title)
    if preview.intent == "move_chapter":
        return bool(
            operation.chapter_id
            and (
                operation.direction in {"up", "down"}
                or operation.target_position in {"first", "last"}
                or operation.target_index is not None
                or bool(
                    operation.relative_chapter_id
                    and operation.relative_position in {"before", "after"}
                )
            )
        )
    if preview.intent == "merge_chapters":
        return bool(operation.chapter_id and operation.target_chapter_id)
    if preview.intent == "assign_source":
        return bool(operation.chapter_id and operation.utterance_id)
    return False


def _is_confirmation_text(text: str) -> bool:
    normalized = "".join(
        char
        for char in text.strip()
        if char not in set(" \t\r\n，。,.!！?？、；;：“”\"‘’'（）()")
    )
    return normalized in {
        "确认",
        "确认执行",
        "执行",
        "可以",
        "可以执行",
        "就这样",
        "就这样改",
        "好的",
        "好",
        "没问题",
        "按这个改",
    }


def _conversation_context_from_payload(value: Any) -> list[MemoirBookVoiceCommandContextMessage]:
    if not isinstance(value, list):
        return []
    context = []
    for item in value[:8]:
        try:
            context.append(MemoirBookVoiceCommandContextMessage.model_validate(item))
        except ValidationError:
            continue
    return context


def _book_context_from_payload(value: Any) -> MemoirBookVoiceCommandBookContext | None:
    if not isinstance(value, dict):
        return None
    try:
        return MemoirBookVoiceCommandBookContext.model_validate(value)
    except ValidationError:
        return None


def _pending_command_from_payload(value: Any) -> MemoirBookVoiceCommandParseResponse | None:
    if not isinstance(value, dict):
        return None
    try:
        return MemoirBookVoiceCommandParseResponse.model_validate(value)
    except ValidationError:
        return None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _model_dump(value: object | None) -> dict | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return, attr-defined]
    return value if isinstance(value, dict) else None
