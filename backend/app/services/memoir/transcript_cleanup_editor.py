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
    MemoirDialectGlossaryResponse,
    MemoirDialectGlossaryUpdateRequest,
)
from app.services.memoir import voice_transcripts
from app.services.memoir.dialect_glossary import (
    glossary_context_lines as _glossary_context_lines,
    glossary_entries as _glossary_entries,
    save_glossary_entries,
)
from app.services.memoir.project_ai_preferences import (
    project_transcript_polish_level,
    resolve_project_ai_model,
    select_project_ai_model,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.material_roles import is_countable_material
from app.services.memoir.transcript_projection import cleanup_source_text, current_utterance_text
from app.services.memoir.utterance_editing_pipeline import (
    TranscriptMutationGuard,
    claim_transcript_mutation_guard,
    transcript_mutation_guard,
)


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
    saved_entries = save_glossary_entries(project, req.entries)
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
    selected_ai_model = resolve_project_ai_model(project, req.ai_model)
    polish_level = project_transcript_polish_level(project)
    glossary_entries = _glossary_entries(project)
    glossary_lines = _glossary_context_lines(glossary_entries)
    utterances = await _load_project_utterances(
        db,
        project_id=project_id,
        user_id=user_id,
        utterance_ids=req.utterance_ids,
        allow_admin=allow_admin,
    )
    prepared = []
    for utterance in utterances:
        source_text = _ensure_cleanup_source(utterance)
        context = await voice_transcripts.load_voice_transcript_cleanup_context(
            db,
            utterance.voice_session,
            exclude_message_id=utterance.chat_message_id,
            dialect_glossary=glossary_lines,
        )
        baseline = await cleanup_source_text(db, utterance)
        prepared.append((utterance, source_text, baseline, context, transcript_mutation_guard(utterance)))

    # Preview is read-only. Release its database transaction before making one
    # or more provider calls; the returned guards protect the later confirm.
    await db.commit()
    items: list[MemoirBatchTranscriptCleanupPreviewItem] = []
    for utterance, source_text, baseline, context, mutation_guard in prepared:
        cleaned = await voice_transcripts.generate_cleaned_transcript_text(
            baseline,
            ai_model=selected_ai_model,
            context=context,
            transcript_polish_level=polish_level,
            strict=True,
        )
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
    # Persist a changed preference in its own short transaction before taking
    # any material guards.
    await db.commit()
    item_by_utterance_id = {item.utterance_id: item for item in req.items}
    utterances = await _load_project_utterances(
        db,
        project_id=project_id,
        user_id=user_id,
        utterance_ids=list(item_by_utterance_id),
        allow_admin=allow_admin,
    )
    try:
        # Acquire multi-row guards in a stable order so overlapping batch edits
        # cannot deadlock by presenting the same materials in a different order.
        for utterance in sorted(utterances, key=lambda item: item.id):
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
