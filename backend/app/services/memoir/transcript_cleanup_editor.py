from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_interview_editor import (
    MemoirBatchTranscriptCleanupConfirmRequest,
    MemoirBatchTranscriptCleanupConfirmResponse,
    MemoirBatchTranscriptCleanupPreviewItem,
    MemoirBatchTranscriptCleanupPreviewRequest,
    MemoirBatchTranscriptCleanupPreviewResponse,
    MemoirDialectGlossaryEntry,
    MemoirDialectGlossaryResponse,
    MemoirDialectGlossaryUpdateRequest,
)
from app.services.memoir import voice_transcripts
from app.services.memoir.project_ai_preferences import select_project_ai_model
from app.services.memoir.access import load_memoir_project
from app.services.memoir.material_roles import is_countable_material
from app.services.memoir.transcript_projection import current_utterance_text
from app.services.memoir.utterance_editing_pipeline import (
    TranscriptMutationGuard,
    claim_transcript_mutation_guard,
    transcript_mutation_guard,
)


GLOSSARY_METADATA_KEY = "memoir_dialect_glossary_entries"


def _entry_from_raw(value: object) -> MemoirDialectGlossaryEntry | None:
    if not isinstance(value, dict):
        return None
    source = str(value.get("source") or "").strip()
    target = str(value.get("target") or "").strip()
    note = str(value.get("note") or "").strip() or None
    if not source or not target:
        return None
    return MemoirDialectGlossaryEntry(source=source, target=target, note=note)


def _glossary_entries(project: Project) -> list[MemoirDialectGlossaryEntry]:
    raw_entries = (project.metadata_json or {}).get(GLOSSARY_METADATA_KEY)
    if not isinstance(raw_entries, list):
        return []
    entries: list[MemoirDialectGlossaryEntry] = []
    seen: set[tuple[str, str]] = set()
    for raw_entry in raw_entries:
        entry = _entry_from_raw(raw_entry)
        if entry is None:
            continue
        key = (entry.source, entry.target)
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries


def _glossary_payload(entries: list[MemoirDialectGlossaryEntry]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        source = entry.source.strip()
        target = entry.target.strip()
        note = (entry.note or "").strip()
        key = (source, target)
        if not source or not target or key in seen:
            continue
        seen.add(key)
        item = {"source": source, "target": target}
        if note:
            item["note"] = note
        payload.append(item)
    return payload


def _glossary_context_lines(entries: list[MemoirDialectGlossaryEntry]) -> tuple[str, ...]:
    lines: list[str] = []
    for entry in entries:
        line = f"{entry.source} => {entry.target}"
        if entry.note:
            line = f"{line}（{entry.note}）"
        lines.append(line)
    return tuple(lines)


def _detail_options():
    return (
        selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.messages),
        selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.project),
        selectinload(MemoirVoiceUtterance.chat_message).selectinload(MemoirChatMessage.parent_message),
        selectinload(MemoirVoiceUtterance.audio_file),
    )


async def _load_project_utterances(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    utterance_ids: list[str],
    allow_admin: bool = False,
) -> list[MemoirVoiceUtterance]:
    filters = [
        MemoirVoiceUtterance.id.in_(utterance_ids),
        MemoirVoiceSession.project_id == project_id,
    ]
    if not allow_admin:
        filters.append(Project.user_id == user_id)
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
        .options(*_detail_options())
    )
    utterances_by_id = {utterance.id: utterance for utterance in result.scalars().all()}
    missing_ids = [utterance_id for utterance_id in utterance_ids if utterance_id not in utterances_by_id]
    if missing_ids:
        raise HTTPException(status_code=404, detail="部分讲述记录不存在")
    return [utterances_by_id[utterance_id] for utterance_id in utterance_ids]


def _ensure_cleanup_source(utterance: MemoirVoiceUtterance) -> str:
    source_text = current_utterance_text(utterance)
    if not source_text:
        raise HTTPException(status_code=400, detail="讲述内容为空，无法整理")
    if not utterance.chat_message or utterance.chat_message.role != MemoirChatMessageRole.USER.value:
        raise HTTPException(status_code=400, detail="只能批量整理被访者回答")
    if not is_countable_material(utterance, source_text):
        raise HTTPException(status_code=400, detail="非正文素材不支持批量整理")
    return source_text


async def get_dialect_glossary(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    allow_admin: bool = False,
) -> MemoirDialectGlossaryResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    return MemoirDialectGlossaryResponse(project_id=project_id, entries=_glossary_entries(project))


async def update_dialect_glossary(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    req: MemoirDialectGlossaryUpdateRequest,
    allow_admin: bool = False,
) -> MemoirDialectGlossaryResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    saved_entries = [
        MemoirDialectGlossaryEntry(**entry)
        for entry in _glossary_payload(req.entries)
    ]
    metadata = dict(project.metadata_json or {})
    metadata[GLOSSARY_METADATA_KEY] = [entry.model_dump(exclude_none=True) for entry in saved_entries]
    project.metadata_json = metadata
    await db.commit()
    return MemoirDialectGlossaryResponse(project_id=project_id, entries=saved_entries)


async def preview_batch_cleanup(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    req: MemoirBatchTranscriptCleanupPreviewRequest,
    allow_admin: bool = False,
) -> MemoirBatchTranscriptCleanupPreviewResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    glossary_entries = _glossary_entries(project)
    glossary_lines = _glossary_context_lines(glossary_entries)
    utterances = await _load_project_utterances(
        db,
        project_id=project_id,
        user_id=user_id,
        utterance_ids=req.utterance_ids,
        allow_admin=allow_admin,
    )
    items: list[MemoirBatchTranscriptCleanupPreviewItem] = []
    for utterance in utterances:
        source_text = _ensure_cleanup_source(utterance)
        context = await voice_transcripts.load_voice_transcript_cleanup_context(
            db,
            utterance.voice_session,
            exclude_message_id=utterance.chat_message_id,
            dialect_glossary=glossary_lines,
        )
        cleaned = await voice_transcripts.generate_cleaned_transcript_text(
            source_text,
            ai_model=selected_ai_model,
            context=context,
            strict=True,
        )
        mutation_guard = transcript_mutation_guard(utterance)
        items.append(
            MemoirBatchTranscriptCleanupPreviewItem(
                utterance_id=utterance.id,
                source_text=source_text,
                source_text_hash=mutation_guard.source_text_hash,
                source_transcript_version_id=mutation_guard.transcript_version_id,
                cleaned_text=cleaned.content,
                changed=cleaned.content.strip() != source_text.strip(),
                provider=cleaned.provider,
                created_by=cleaned.created_by,
            )
        )
    return MemoirBatchTranscriptCleanupPreviewResponse(
        project_id=project_id,
        items=items,
        glossary_entries=glossary_entries,
    )


async def confirm_batch_cleanup(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    req: MemoirBatchTranscriptCleanupConfirmRequest,
    allow_admin: bool = False,
) -> MemoirBatchTranscriptCleanupConfirmResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    item_by_utterance_id = {item.utterance_id: item for item in req.items}
    utterances = await _load_project_utterances(
        db,
        project_id=project_id,
        user_id=user_id,
        utterance_ids=list(item_by_utterance_id),
        allow_admin=allow_admin,
    )
    try:
        for utterance in utterances:
            _ensure_cleanup_source(utterance)
            item = item_by_utterance_id[utterance.id]
            claimed = await claim_transcript_mutation_guard(
                db,
                utterance=utterance,
                expected=TranscriptMutationGuard(
                    source_text_hash=item.source_text_hash,
                    transcript_version_id=item.source_transcript_version_id,
                ),
            )
            if not claimed:
                raise HTTPException(status_code=409, detail="讲述内容已更新，请重新生成整理预览")

        responses = []
        for utterance in utterances:
            item = item_by_utterance_id[utterance.id]
            responses.append(
                await voice_transcripts.apply_cleaned_voice_transcript_text(
                    db,
                    utterance,
                    item.cleaned_text,
                    created_by=item.created_by,
                    provider=item.provider,
                    ai_model=selected_ai_model,
                    commit=False,
                )
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return MemoirBatchTranscriptCleanupConfirmResponse(
        project_id=project_id,
        updated_count=len(responses),
        items=responses,
    )
