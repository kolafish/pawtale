from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR
from app.database import Base, register_models
from app.models.audit import AuditEvent
from app.models.git_sync import ProjectGitSync
from app.models.memoir import (
    MemoirBookOutput,
    MemoirChatMessage,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
    MemoirTranscriptAlignment,
    MemoirTranscriptVersion,
    MemoirVoiceAuthorizationConsent,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_common import AudioFile
from app.models.memoir_narrative import MemoirDraftBlock
from app.models.projects import Project
import app.services.project_documents as project_documents
from app.services.memoir import audio_files


logger = logging.getLogger(__name__)
PROJECT_DELETE_MARKER = "__delete_in_progress"
_VOICE_PROJECT_TABLES = {"memoir_chat_messages", "memoir_voice_sessions"}


def _metadata_storage_paths(value: object) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("storage_path") and isinstance(item, str) and item.strip():
                paths.add(item.strip())
            paths.update(_metadata_storage_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(_metadata_storage_paths(item))
    return paths


def _image_paths(asset: MemoirImageAsset) -> set[str]:
    return {
        value
        for value in (
            asset.storage_path,
            asset.thumbnail_storage_path,
            *_metadata_storage_paths(asset.metadata_json),
        )
        if value
    }


def _share_video_paths(job: MemoirMaterialShareVideo) -> set[str]:
    return {
        value
        for value in (
            job.output.storage_path if job.output is not None else None,
            *_metadata_storage_paths(job.metadata_json),
        )
        if value
    }


def _data_file_path(storage_path: str) -> Path | None:
    raw_path = Path(storage_path)
    path = raw_path.resolve() if raw_path.is_absolute() else (DATA_DIR / raw_path).resolve()
    try:
        path.relative_to(DATA_DIR.resolve())
    except ValueError:
        logger.warning("Refusing to delete project media outside DATA_DIR path=%s", path)
        return None
    return path


async def _project_audio_files(
    db: AsyncSession,
    *,
    project_id: str,
) -> list[AudioFile]:
    audio_ids: set[str] = set()
    for statement in (
        select(MemoirChatMessage.audio_file_id).where(
            MemoirChatMessage.project_id == project_id,
            MemoirChatMessage.audio_file_id.is_not(None),
        ),
        select(MemoirVoiceUtterance.audio_file_id)
        .join(
            MemoirVoiceSession,
            MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id,
        )
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceUtterance.audio_file_id.is_not(None),
        ),
        select(MemoirMaterialShareVideo.audio_file_id).where(
            MemoirMaterialShareVideo.project_id == project_id,
        ),
    ):
        audio_ids.update(value for value in (await db.scalars(statement)).all() if value)
    if not audio_ids:
        return []
    return list(
        (
            await db.scalars(select(AudioFile).where(AudioFile.id.in_(audio_ids)))
        ).all()
    )


async def _referenced_audio_ids(
    db: AsyncSession,
    audio_ids: set[str],
) -> set[str]:
    if not audio_ids:
        return set()
    referenced: set[str] = set()
    for statement in (
        select(MemoirChatMessage.audio_file_id).where(
            MemoirChatMessage.audio_file_id.in_(audio_ids)
        ),
        select(MemoirVoiceUtterance.audio_file_id).where(
            MemoirVoiceUtterance.audio_file_id.in_(audio_ids)
        ),
        select(MemoirMaterialShareVideo.audio_file_id).where(
            MemoirMaterialShareVideo.audio_file_id.in_(audio_ids)
        ),
    ):
        referenced.update(value for value in (await db.scalars(statement)).all() if value)
    return referenced


async def _remaining_media_paths(db: AsyncSession) -> set[str]:
    paths: set[str] = set()
    for asset in (await db.scalars(select(MemoirImageAsset))).all():
        paths.update(_image_paths(asset))
    for job in (await db.scalars(select(MemoirMaterialShareVideo))).all():
        paths.update(_share_video_paths(job))
    return paths


async def _purge_project_tables(db: AsyncSession, project_id: str) -> None:
    register_models()
    await db.execute(
        update(MemoirBookOutput)
        .where(MemoirBookOutput.project_id == project_id)
        .values(formal_version_id=None, formal_audit_job_id=None)
    )
    await db.execute(
        update(MemoirDraftBlock)
        .where(MemoirDraftBlock.project_id == project_id)
        .values(current_version_id=None)
    )

    for table in reversed(Base.metadata.sorted_tables):
        if (
            not table.name.startswith("memoir_")
            or "project_id" not in table.c
            or table.name in _VOICE_PROJECT_TABLES
        ):
            continue
        await db.execute(delete(table).where(table.c.project_id == project_id))


async def _purge_voice_data(
    db: AsyncSession,
    *,
    project_id: str,
    session_ids: list[str],
    utterance_ids: list[str],
) -> None:
    if session_ids:
        await db.execute(
            update(MemoirVoiceAuthorizationConsent)
            .where(
                MemoirVoiceAuthorizationConsent.source_session_id.in_(session_ids)
            )
            .values(source_session_id=None)
        )
    if utterance_ids:
        await db.execute(
            delete(MemoirTranscriptAlignment).where(
                MemoirTranscriptAlignment.utterance_id.in_(utterance_ids)
            )
        )
        await db.execute(
            delete(MemoirVoiceUtterance).where(
                MemoirVoiceUtterance.id.in_(utterance_ids)
            )
        )
    await db.execute(
        delete(MemoirChatMessage).where(MemoirChatMessage.project_id == project_id)
    )
    if session_ids:
        await db.execute(
            delete(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.voice_session_id.in_(session_ids)
            )
        )
        await db.execute(
            delete(MemoirVoiceSession).where(MemoirVoiceSession.id.in_(session_ids))
        )


def _delete_media_paths(storage_paths: set[str]) -> None:
    for storage_path in storage_paths:
        path = _data_file_path(storage_path)
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
            if path.suffix == ".mp4":
                path.with_name(f"{path.name}.complete").unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete memoir project media path=%s", path, exc_info=True)


async def delete_project(db: AsyncSession, project: Project) -> None:
    project_id = project.id
    audio_candidates = await _project_audio_files(db, project_id=project_id)
    image_candidates = list(
        (
            await db.scalars(
                select(MemoirImageAsset).where(
                    MemoirImageAsset.project_id == project_id
                )
            )
        ).all()
    )
    video_candidates = list(
        (
            await db.scalars(
                select(MemoirMaterialShareVideo).where(
                    MemoirMaterialShareVideo.project_id == project_id
                )
            )
        ).all()
    )
    media_candidates = {
        path
        for asset in image_candidates
        for path in _image_paths(asset)
    } | {
        path
        for job in video_candidates
        for path in _share_video_paths(job)
    }
    session_ids = list(
        (
            await db.scalars(
                select(MemoirVoiceSession.id).where(
                    MemoirVoiceSession.project_id == project_id
                )
            )
        ).all()
    )
    utterance_ids = (
        list(
            (
                await db.scalars(
                    select(MemoirVoiceUtterance.id).where(
                        MemoirVoiceUtterance.voice_session_id.in_(session_ids)
                    )
                )
            ).all()
        )
        if session_ids
        else []
    )

    try:
        project.metadata_json = {
            **(project.metadata_json or {}),
            PROJECT_DELETE_MARKER: True,
        }
        await db.flush()
        await _purge_project_tables(db, project_id)
        await _purge_voice_data(
            db,
            project_id=project_id,
            session_ids=session_ids,
            utterance_ids=utterance_ids,
        )
        audit_filters = [AuditEvent.project_id == project_id]
        if session_ids:
            audit_filters.append(AuditEvent.voice_session_id.in_(session_ids))
        await db.execute(
            update(AuditEvent)
            .where(or_(*audit_filters))
            .values(project_id=None, voice_session_id=None)
        )
        await db.execute(
            delete(ProjectGitSync).where(ProjectGitSync.project_id == project_id)
        )

        candidate_audio_ids = {audio_file.id for audio_file in audio_candidates}
        referenced_audio_ids = await _referenced_audio_ids(db, candidate_audio_ids)
        deletable_audio = [
            audio_file
            for audio_file in audio_candidates
            if audio_file.id not in referenced_audio_ids
        ]
        if deletable_audio:
            await db.execute(
                delete(AudioFile).where(
                    AudioFile.id.in_([audio_file.id for audio_file in deletable_audio])
                )
            )
        remaining_media_paths = await _remaining_media_paths(db)
        await project_documents.delete_project(db, project)
    except Exception:
        await db.rollback()
        raise

    try:
        await audio_files.delete_audio_file_paths(deletable_audio)
        _delete_media_paths(media_candidates - remaining_media_paths)
    except Exception:
        logger.warning(
            "Failed to delete memoir project files project_id=%s",
            project_id,
            exc_info=True,
        )
