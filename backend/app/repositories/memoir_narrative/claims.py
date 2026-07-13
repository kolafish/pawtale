from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceSourceType,
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
)
from app.domain.memoir.types import MemoirImageAssetSourceType
from app.models.memoir import (
    MemoirChatMessage,
    MemoirImageAsset,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import MemoirClaim, MemoirClaimEvidence
from app.schemas.memoir_narrative import MemoirClaimCreate, MemoirClaimEvidenceCreate


class MemoirClaimRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, data: MemoirClaimCreate) -> MemoirClaim:
        claim = MemoirClaim(
            project_id=data.project_id,
            claim_type=data.claim_type.value,
            statement=data.statement,
            subject_entity_id=data.subject_entity_id,
            predicate_code=data.predicate_code.value,
            object_entity_id=data.object_entity_id,
            object_text=data.object_text,
            place_entity_id=data.place_entity_id,
            time_text=data.time_text,
            time_start=data.time_start,
            time_end=data.time_end,
            time_precision=data.time_precision.value,
            time_anchor_claim_id=data.time_anchor_claim_id,
            certainty=data.certainty.value,
            verification_status=data.verification_status.value,
            publication_status=data.publication_status.value,
            conflict_group_id=data.conflict_group_id,
            created_by=data.created_by.value,
            created_by_user_id=data.created_by_user_id,
            updated_by=data.created_by.value,
            updated_by_user_id=data.created_by_user_id,
        )
        self._db.add(claim)
        await self._db.flush()
        return claim

    async def add_evidence(self, data: MemoirClaimEvidenceCreate) -> MemoirClaimEvidence:
        await self._validate_evidence_references(data)
        evidence = MemoirClaimEvidence(
            project_id=data.project_id,
            claim_id=data.claim_id,
            evidence_role=data.evidence_role.value,
            evidence_source_type=data.evidence_source_type.value,
            utterance_id=data.utterance_id,
            transcript_version_id=data.transcript_version_id,
            audio_file_id=data.audio_file_id,
            image_asset_id=data.image_asset_id,
            speaker_entity_id=data.speaker_entity_id,
            speaker_role=data.speaker_role.value if data.speaker_role else None,
            start_ms=data.start_ms,
            end_ms=data.end_ms,
            quote=data.quote,
            evidence_note=data.evidence_note,
            source_text_hash=data.source_text_hash,
            created_by=data.created_by.value,
            created_by_user_id=data.created_by_user_id,
        )
        self._db.add(evidence)
        await self._db.flush()
        return evidence

    async def _validate_evidence_references(self, data: MemoirClaimEvidenceCreate) -> None:
        claim_exists = await self._db.scalar(
            select(MemoirClaim.id).where(
                MemoirClaim.project_id == data.project_id,
                MemoirClaim.id == data.claim_id,
            )
        )
        if claim_exists is None:
            raise ValueError("claim does not belong to the evidence project")

        if data.evidence_source_type == MemoirClaimEvidenceSourceType.TRANSCRIPT:
            source = (
                await self._db.execute(
                    select(
                        MemoirTranscriptVersion.voice_session_id,
                        MemoirTranscriptVersion.answer_id,
                        MemoirVoiceSession.project_id,
                    )
                    .outerjoin(
                        MemoirVoiceSession,
                        MemoirVoiceSession.id == MemoirTranscriptVersion.voice_session_id,
                    )
                    .where(MemoirTranscriptVersion.id == data.transcript_version_id)
                )
            ).one_or_none()
            if source is None:
                raise ValueError("transcript evidence source does not exist")
            voice_session_id, _answer_id, source_project_id = source
            if voice_session_id is None:
                raise ValueError(
                    "legacy transcript ownership must be resolved before creating narrative evidence"
                )
            if source_project_id != data.project_id:
                raise ValueError("transcript evidence source belongs to another project")

            if data.utterance_id is not None:
                utterance = (
                    await self._db.execute(
                        select(MemoirVoiceUtterance.voice_session_id, MemoirVoiceUtterance.audio_file_id)
                        .where(MemoirVoiceUtterance.id == data.utterance_id)
                    )
                ).one_or_none()
                if utterance is None or utterance.voice_session_id != voice_session_id:
                    raise ValueError("utterance evidence source does not belong to the transcript session")
                if data.audio_file_id is not None and utterance.audio_file_id != data.audio_file_id:
                    raise ValueError("audio evidence source does not belong to the utterance")
            elif data.audio_file_id is not None:
                utterance_audio = await self._db.scalar(
                    select(MemoirVoiceUtterance.id).where(
                        MemoirVoiceUtterance.voice_session_id == voice_session_id,
                        MemoirVoiceUtterance.audio_file_id == data.audio_file_id,
                    )
                )
                message_audio = await self._db.scalar(
                    select(MemoirChatMessage.id).where(
                        MemoirChatMessage.voice_session_id == voice_session_id,
                        MemoirChatMessage.audio_file_id == data.audio_file_id,
                    )
                )
                if utterance_audio is None and message_audio is None:
                    raise ValueError("audio evidence source does not belong to the transcript session")

        if data.evidence_source_type == MemoirClaimEvidenceSourceType.IMAGE:
            image_source_type = await self._db.scalar(
                select(MemoirImageAsset.source_type).where(
                    MemoirImageAsset.project_id == data.project_id,
                    MemoirImageAsset.id == data.image_asset_id,
                )
            )
            if image_source_type is None:
                raise ValueError("image evidence source does not belong to the evidence project")
            if image_source_type != MemoirImageAssetSourceType.MANUAL_UPLOAD.value:
                raise ValueError("generated images cannot be used as fact evidence")

    async def get(
        self,
        *,
        project_id: str,
        claim_id: str,
        include_evidence: bool = False,
    ) -> MemoirClaim | None:
        statement = select(MemoirClaim).where(
            MemoirClaim.project_id == project_id,
            MemoirClaim.id == claim_id,
        )
        if include_evidence:
            statement = statement.options(selectinload(MemoirClaim.evidence))
        return await self._db.scalar(statement)

    async def list_for_project(
        self,
        *,
        project_id: str,
        verification_status: MemoirClaimVerificationStatus | None = None,
        publication_status: MemoirClaimPublicationStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoirClaim]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        statement = select(MemoirClaim).where(MemoirClaim.project_id == project_id)
        if verification_status is not None:
            statement = statement.where(MemoirClaim.verification_status == verification_status.value)
        if publication_status is not None:
            statement = statement.where(MemoirClaim.publication_status == publication_status.value)
        statement = statement.order_by(MemoirClaim.created_at, MemoirClaim.id).limit(limit).offset(offset)
        return list((await self._db.scalars(statement)).all())
