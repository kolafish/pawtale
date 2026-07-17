from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import MemoirNarrativeDecisionTargetType
from app.models.memoir import MemoirTranscriptVersion, MemoirVoiceSession
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirCompletionItem,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
    MemoirStory,
)
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir_narrative import MemoirNarrativeDecisionCreate


class MemoirNarrativeDecisionRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(
        self,
        data: MemoirNarrativeDecisionCreate,
    ) -> tuple[MemoirNarrativeDecision, bool]:
        existing = await self.get_by_idempotency_key(
            project_id=data.project_id,
            idempotency_key=data.idempotency_key,
        )
        if existing is not None:
            if not self._matches_create(existing, data):
                raise ValueError("idempotency key was already used for another decision")
            return existing, False

        await self._validate_references(data)
        decision = MemoirNarrativeDecision(
            project_id=data.project_id,
            target_type=data.target_type.value,
            target_id=data.target_id,
            action=data.action.value,
            actor_type=data.actor_type.value,
            actor_user_id=data.actor_user_id,
            source_transcript_version_id=data.source_transcript_version_id,
            before_state_json=data.before_state,
            after_state_json=data.after_state,
            reason=data.reason,
            idempotency_key=data.idempotency_key,
        )
        try:
            async with self._db.begin_nested():
                self._db.add(decision)
                await self._db.flush()
            return decision, True
        except IntegrityError:
            existing = await self.get_by_idempotency_key(
                project_id=data.project_id,
                idempotency_key=data.idempotency_key,
            )
            if existing is None:
                raise
            if not self._matches_create(existing, data):
                raise ValueError("idempotency key was already used for another decision")
            return existing, False

    async def validate_transcript_source(
        self,
        *,
        project_id: str,
        source_transcript_version_id: str | None,
    ) -> None:
        if source_transcript_version_id is None:
            return
        source_exists = await self._db.scalar(
            select(MemoirTranscriptVersion.id)
            .join(
                MemoirVoiceSession,
                MemoirVoiceSession.id == MemoirTranscriptVersion.voice_session_id,
            )
            .where(
                MemoirVoiceSession.project_id == project_id,
                MemoirTranscriptVersion.id == source_transcript_version_id,
            )
        )
        if source_exists is None:
            raise ValueError("decision transcript source does not belong to the project")

    @staticmethod
    def _matches_create(
        existing: MemoirNarrativeDecision,
        data: MemoirNarrativeDecisionCreate,
    ) -> bool:
        return (
            existing.target_type == data.target_type.value
            and existing.target_id == data.target_id
            and existing.action == data.action.value
            and existing.actor_type == data.actor_type.value
            and existing.actor_user_id == data.actor_user_id
            and existing.source_transcript_version_id == data.source_transcript_version_id
        )

    async def _validate_references(self, data: MemoirNarrativeDecisionCreate) -> None:
        project_exists = await self._db.scalar(
            select(Project.id).where(Project.id == data.project_id)
        )
        if project_exists is None:
            raise ValueError("decision project does not exist")
        if data.actor_user_id is not None:
            actor_exists = await self._db.scalar(
                select(User.id).where(User.id == data.actor_user_id)
            )
            if actor_exists is None:
                raise ValueError("decision actor does not exist")

        if data.source_transcript_version_id is not None:
            transcript_exists = await self._db.scalar(
                select(MemoirTranscriptVersion.id).where(
                    MemoirTranscriptVersion.id == data.source_transcript_version_id
                )
            )
            if transcript_exists is None:
                raise ValueError("decision transcript source does not exist")

        if data.target_type == MemoirNarrativeDecisionTargetType.ENTITY:
            target_exists = await self._db.scalar(
                select(MemoirNarrativeEntity.id).where(
                    MemoirNarrativeEntity.project_id == data.project_id,
                    MemoirNarrativeEntity.id == data.target_id,
                )
            )
        elif data.target_type == MemoirNarrativeDecisionTargetType.CLAIM:
            target_exists = await self._db.scalar(
                select(MemoirClaim.id).where(
                    MemoirClaim.project_id == data.project_id,
                    MemoirClaim.id == data.target_id,
                )
            )
        elif data.target_type == MemoirNarrativeDecisionTargetType.STORY:
            target_exists = await self._db.scalar(
                select(MemoirStory.id).where(
                    MemoirStory.project_id == data.project_id,
                    MemoirStory.id == data.target_id,
                )
            )
        elif data.target_type == MemoirNarrativeDecisionTargetType.COMPLETION_ITEM:
            target_exists = await self._db.scalar(
                select(MemoirCompletionItem.id).where(
                    MemoirCompletionItem.project_id == data.project_id,
                    MemoirCompletionItem.id == data.target_id,
                )
            )
        else:
            raise ValueError(f"unsupported decision target type: {data.target_type.value}")
        if target_exists is None:
            raise ValueError("decision target does not belong to the decision project")

    async def get_by_idempotency_key(
        self,
        *,
        project_id: str,
        idempotency_key: str,
    ) -> MemoirNarrativeDecision | None:
        return await self._db.scalar(
            select(MemoirNarrativeDecision).where(
                MemoirNarrativeDecision.project_id == project_id,
                MemoirNarrativeDecision.idempotency_key == idempotency_key,
            )
        )

    async def list_for_target(
        self,
        *,
        project_id: str,
        target_type: MemoirNarrativeDecisionTargetType,
        target_id: str,
        limit: int = 100,
    ) -> list[MemoirNarrativeDecision]:
        statement = (
            select(MemoirNarrativeDecision)
            .where(
                MemoirNarrativeDecision.project_id == project_id,
                MemoirNarrativeDecision.target_type == target_type.value,
                MemoirNarrativeDecision.target_id == target_id,
            )
            .order_by(
                MemoirNarrativeDecision.created_at.desc(),
                MemoirNarrativeDecision.id.desc(),
            )
            .limit(max(1, min(limit, 500)))
        )
        return list((await self._db.scalars(statement)).all())
