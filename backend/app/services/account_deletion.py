from __future__ import annotations

import logging
import shutil
import uuid
from contextlib import AsyncExitStack

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR
from app.models.audit import AuditDailyFeatureUsage, AuditDailyUserUsage, AuditEvent, AuditSession
from app.models.git_sync import GitHubInstallation, GitHubSetupState, ProjectGitSync
from app.models.memoir import MemoirImageAsset, MemoirMaterialShareVideo, MemoirVoiceAuthorizationConsent
from app.models.memoir_common import AudioFile
from app.models.projects import Project
from app.models.users import AuthIdentity, AuthSession, User
from app.services import user_avatars
from app.services import content_storage
from app.services.project_documents import PostCommitCleanup
from app.services.projects import core as project_core


logger = logging.getLogger(__name__)


async def delete_account(db: AsyncSession, user: User) -> None:
    """Delete all account data in one DB transaction, then clean up its files."""
    user_id = user.id
    if str(uuid.UUID(user_id)) != user_id:
        raise ValueError("Account id is not a canonical UUID")
    post_commit_cleanups: list[PostCommitCleanup] = []
    async with AsyncExitStack() as project_locks:
        try:
            # Prevent new projects from being attached while ownership is enumerated.
            await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
            project_ids = sorted((await db.scalars(select(Project.id).where(Project.user_id == user_id))).all())
            for project_id in project_ids:
                await project_locks.enter_async_context(content_storage.project_content_lock(project_id))
            for project_id in project_ids:
                await project_core.delete_project(
                    db, project_id, user, post_commit_cleanups=post_commit_cleanups,
                )

            # Project deletion should remove these project-bound records. Fail closed if
            # another relationship still retains data, rather than orphaning it.
            for model in (MemoirImageAsset, MemoirMaterialShareVideo):
                if await db.scalar(select(model.id).where(model.user_id == user_id).limit(1)):
                    raise RuntimeError(f"Account still has project-bound {model.__tablename__} records")

            await db.execute(delete(MemoirVoiceAuthorizationConsent).where(MemoirVoiceAuthorizationConsent.user_id == user_id))
            await db.execute(delete(AuditEvent).where(AuditEvent.user_id == user_id))
            await db.execute(delete(AuditSession).where(AuditSession.user_id == user_id))
            await db.execute(delete(AuditDailyFeatureUsage).where(AuditDailyFeatureUsage.user_id == user_id))
            await db.execute(delete(AuditDailyUserUsage).where(AuditDailyUserUsage.user_id == user_id))
            await db.execute(delete(ProjectGitSync).where(ProjectGitSync.user_id == user_id))
            await db.execute(delete(GitHubSetupState).where(GitHubSetupState.user_id == user_id))
            await db.execute(delete(GitHubInstallation).where(GitHubInstallation.user_id == user_id))
            await db.execute(delete(AudioFile).where(AudioFile.user_id == user_id))
            await db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
            await db.execute(delete(AuthIdentity).where(AuthIdentity.user_id == user_id))
            await db.delete(user)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

        for cleanup in post_commit_cleanups:
            try:
                await cleanup()
            except Exception:
                logger.exception("Failed to remove deleted account project files user_id=%s", user_id)

    for directory in (
        DATA_DIR / "memoir" / "audio" / user_id,
        DATA_DIR / "memoir" / "share-videos" / user_id,
        DATA_DIR / "memoir" / "share-video-backgrounds" / "uploads" / user_id,
        user_avatars.avatar_directory(user_id),
    ):
        try:
            if directory.is_dir() and not directory.is_symlink():
                shutil.rmtree(directory)
        except OSError:
            logger.exception("Failed to remove deleted account files path=%s", directory)
