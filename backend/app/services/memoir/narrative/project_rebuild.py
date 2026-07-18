from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirCompletionItemStatus,
    MemoirFrameworkEvidenceState,
    MemoirFrameworkUserResolution,
    MemoirNarrativeActorType,
    MemoirNarrativeGapDetectedBy,
)
from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirVoiceSessionStatus,
)
from app.domain.projects.types import ProjectType
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlanChapter,
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirCompletionItem,
    MemoirFrameworkItem,
    MemoirFrameworkItemClaim,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
    MemoirProjectFramework,
    MemoirStory,
    MemoirStoryClaim,
)
from app.models.projects import Project
from app.services.memoir.automation_event_metadata import (
    RESOLUTION_KEY,
    RESOLUTION_REASON_KEY,
    RESOLVED_AT_KEY,
)
from app.services.memoir.narrative import chapter_draft_state, completion_refresh
from app.services.memoir.narrative.ingestion_jobs import (
    schedule_narrative_ingestion_job,
)
from app.services.memoir.project_ai_preferences import project_ai_model
from app.services.memoir.source_disposition import is_countable_source_utterance


ACTIVE_SESSION_STATUSES = {
    MemoirVoiceSessionStatus.CREATED.value,
    MemoirVoiceSessionStatus.LISTENING.value,
    MemoirVoiceSessionStatus.SPEAKING.value,
}
ACTIVE_JOB_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}
ACTIVE_COMPLETION_STATUSES = {
    MemoirCompletionItemStatus.OPEN.value,
    MemoirCompletionItemStatus.IN_PROGRESS.value,
}
REBUILD_JOB_TYPES = {
    MemoirEditingJobType.INGEST_NARRATIVE_STATE.value,
    MemoirEditingJobType.EVALUATE_TOPIC_MAP_CLAIMS.value,
    MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value,
}
INVALIDATED_AUDIT_SOURCES = {
    "book_quality_audit",
    "chapter_completion_refresh",
    "followup_planner",
    "narrative_gap_audit",
}
RECENT_ACTIVE_SESSION_WINDOW = timedelta(minutes=30)
REBUILD_SOURCE = "project_narrative_state_rebuild"
REBUILD_RESOLUTION_REASON = "narrative_state_rebuilt"


class NarrativeProjectRebuildError(ValueError):
    pass


@dataclass(frozen=True)
class NarrativeProjectRebuildPreview:
    project_id: str
    project_title: str
    derived_counts: dict[str, int]
    preserved_counts: dict[str, int]
    source_utterance_count: int
    rebuildable_utterance_count: int
    invalid_utterance_ids: tuple[str, ...]
    active_session_ids: tuple[str, ...]
    active_job_ids: tuple[str, ...]
    user_decision_counts: dict[str, int]

    @property
    def blocker_count(self) -> int:
        return (
            len(self.invalid_utterance_ids)
            + len(self.active_job_ids)
            + sum(self.user_decision_counts.values())
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "blocker_count": self.blocker_count,
        }


@dataclass(frozen=True)
class NarrativeProjectRebuildStart:
    project_id: str
    project_title: str
    run_id: str
    ingestion_job_ids: tuple[str, ...]
    closed_session_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NarrativeProjectRebuildStatus:
    project_id: str
    run_id: str
    ingestion: dict[str, int]
    topic_matching: dict[str, int]
    completion_refresh: dict[str, int]
    failed_jobs: tuple[dict[str, str], ...]

    @property
    def ready_to_finalize(self) -> bool:
        total = self.ingestion.get("total", 0)
        return total > 0 and self.ingestion.get("succeeded", 0) == total

    @property
    def complete(self) -> bool:
        completion_total = self.completion_refresh.get("total", 0)
        return (
            self.ready_to_finalize
            and self.topic_matching.get("pending", 0) == 0
            and self.topic_matching.get("running", 0) == 0
            and completion_total > 0
            and self.completion_refresh.get("succeeded", 0) == completion_total
            and not self.failed_jobs
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "ready_to_finalize": self.ready_to_finalize,
            "complete": self.complete,
        }


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _count(db: AsyncSession, model, *filters) -> int:
    return int(
        await db.scalar(select(func.count()).select_from(model).where(*filters))
        or 0
    )


async def _load_project(db: AsyncSession, project_id: str) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.type != ProjectType.MEMOIR.value:
        raise NarrativeProjectRebuildError("回忆录项目不存在")
    return project


async def _source_utterances(
    db: AsyncSession,
    *,
    project_id: str,
) -> tuple[list[MemoirVoiceUtterance], list[str], int]:
    utterances = list(
        (
            await db.scalars(
                select(MemoirVoiceUtterance)
                .join(
                    MemoirVoiceSession,
                    MemoirVoiceSession.id == MemoirVoiceUtterance.voice_session_id,
                )
                .where(MemoirVoiceSession.project_id == project_id)
                .options(selectinload(MemoirVoiceUtterance.chat_message))
                .order_by(
                    MemoirVoiceUtterance.created_at,
                    MemoirVoiceUtterance.id,
                )
            )
        ).all()
    )
    countable = [
        utterance
        for utterance in utterances
        if is_countable_source_utterance(utterance)
    ]
    version_ids = {
        utterance.chat_message.transcript_version_id
        for utterance in countable
        if utterance.chat_message
        and utterance.chat_message.transcript_version_id
    }
    versions = {
        version.id: version
        for version in (
            (
                await db.scalars(
                    select(MemoirTranscriptVersion).where(
                        MemoirTranscriptVersion.id.in_(version_ids)
                    )
                )
            ).all()
            if version_ids
            else []
        )
    }
    rebuildable: list[MemoirVoiceUtterance] = []
    invalid_ids: list[str] = []
    for utterance in countable:
        message = utterance.chat_message
        version = versions.get(
            message.transcript_version_id
            if message and message.transcript_version_id
            else ""
        )
        current_text = (utterance.transcript_edited or "").strip() or (
            utterance.transcript_raw or ""
        ).strip()
        if (
            message is None
            or version is None
            or version.voice_session_id != utterance.voice_session_id
            or version.source_artifact_id != utterance.id
            or not version.content.strip()
            or message.content_text.strip() != version.content.strip()
            or current_text != version.content.strip()
        ):
            invalid_ids.append(utterance.id)
            continue
        rebuildable.append(utterance)
    return rebuildable, invalid_ids, len(countable)


async def _user_decision_counts(
    db: AsyncSession,
    *,
    project_id: str,
) -> dict[str, int]:
    framework_item_ids = select(MemoirFrameworkItem.id).join(
        MemoirProjectFramework,
        MemoirProjectFramework.id == MemoirFrameworkItem.project_framework_id,
    ).where(MemoirProjectFramework.project_id == project_id)
    return {
        "claims": await _count(
            db,
            MemoirClaim,
            MemoirClaim.project_id == project_id,
            (
                (MemoirClaim.created_by == MemoirNarrativeActorType.USER.value)
                | (MemoirClaim.updated_by == MemoirNarrativeActorType.USER.value)
                | MemoirClaim.created_by_user_id.is_not(None)
                | MemoirClaim.updated_by_user_id.is_not(None)
            ),
        ),
        "claim_evidence": await _count(
            db,
            MemoirClaimEvidence,
            MemoirClaimEvidence.project_id == project_id,
            (
                (MemoirClaimEvidence.created_by == MemoirNarrativeActorType.USER.value)
                | MemoirClaimEvidence.created_by_user_id.is_not(None)
            ),
        ),
        "stories": await _count(
            db,
            MemoirStory,
            MemoirStory.project_id == project_id,
            (
                (MemoirStory.created_by == MemoirNarrativeActorType.USER.value)
                | (MemoirStory.updated_by == MemoirNarrativeActorType.USER.value)
                | MemoirStory.created_by_user_id.is_not(None)
                | MemoirStory.updated_by_user_id.is_not(None)
                | MemoirStory.verification_decided_by_user_id.is_not(None)
                | MemoirStory.publication_decided_by_user_id.is_not(None)
            ),
        ),
        "story_claims": await _count(
            db,
            MemoirStoryClaim,
            MemoirStoryClaim.project_id == project_id,
            (
                (MemoirStoryClaim.created_by == MemoirNarrativeActorType.USER.value)
                | MemoirStoryClaim.created_by_user_id.is_not(None)
            ),
        ),
        "entities": await _count(
            db,
            MemoirNarrativeEntity,
            MemoirNarrativeEntity.project_id == project_id,
            (
                (MemoirNarrativeEntity.created_by == MemoirNarrativeActorType.USER.value)
                | (MemoirNarrativeEntity.updated_by == MemoirNarrativeActorType.USER.value)
                | MemoirNarrativeEntity.created_by_user_id.is_not(None)
                | MemoirNarrativeEntity.updated_by_user_id.is_not(None)
            ),
        ),
        "narrative_decisions": await _count(
            db,
            MemoirNarrativeDecision,
            MemoirNarrativeDecision.project_id == project_id,
            MemoirNarrativeDecision.actor_type
            == MemoirNarrativeActorType.USER.value,
        ),
        "topic_map_resolutions": await _count(
            db,
            MemoirFrameworkItem,
            MemoirFrameworkItem.id.in_(framework_item_ids),
            (
                (
                    MemoirFrameworkItem.user_resolution
                    != MemoirFrameworkUserResolution.NONE.value
                )
                | MemoirFrameworkItem.resolved_by_user_id.is_not(None)
                | MemoirFrameworkItem.resolved_at.is_not(None)
                | MemoirFrameworkItem.deferred_until.is_not(None)
            ),
        ),
        "user_completion_items": await _count(
            db,
            MemoirCompletionItem,
            MemoirCompletionItem.project_id == project_id,
            MemoirCompletionItem.detected_by
            == MemoirNarrativeGapDetectedBy.USER.value,
        ),
    }


async def preview_project_narrative_rebuild(
    db: AsyncSession,
    *,
    project_id: str,
) -> NarrativeProjectRebuildPreview:
    project = await _load_project(db, project_id)
    rebuildable, invalid_ids, source_count = await _source_utterances(
        db,
        project_id=project_id,
    )
    active_sessions = tuple(
        (
            await db.scalars(
                select(MemoirVoiceSession.id)
                .where(
                    MemoirVoiceSession.project_id == project_id,
                    MemoirVoiceSession.status.in_(ACTIVE_SESSION_STATUSES),
                )
                .order_by(MemoirVoiceSession.created_at, MemoirVoiceSession.id)
            )
        ).all()
    )
    active_jobs = tuple(
        (
            await db.scalars(
                select(MemoirEditingJob.id)
                .where(
                    MemoirEditingJob.project_id == project_id,
                    MemoirEditingJob.status.in_(ACTIVE_JOB_STATUSES),
                )
                .order_by(MemoirEditingJob.created_at, MemoirEditingJob.id)
            )
        ).all()
    )
    framework_item_ids = select(MemoirFrameworkItem.id).join(
        MemoirProjectFramework,
        MemoirProjectFramework.id == MemoirFrameworkItem.project_framework_id,
    ).where(MemoirProjectFramework.project_id == project_id)
    chapter_ids = select(MemoirBookPlanChapter.id).where(
        MemoirBookPlanChapter.project_id == project_id
    )
    return NarrativeProjectRebuildPreview(
        project_id=project.id,
        project_title=project.title,
        derived_counts={
            "claims": await _count(
                db, MemoirClaim, MemoirClaim.project_id == project_id
            ),
            "claim_evidence": await _count(
                db,
                MemoirClaimEvidence,
                MemoirClaimEvidence.project_id == project_id,
            ),
            "stories": await _count(
                db, MemoirStory, MemoirStory.project_id == project_id
            ),
            "story_claims": await _count(
                db,
                MemoirStoryClaim,
                MemoirStoryClaim.project_id == project_id,
            ),
            "entities": await _count(
                db,
                MemoirNarrativeEntity,
                MemoirNarrativeEntity.project_id == project_id,
            ),
            "topic_map_claim_links": await _count(
                db,
                MemoirFrameworkItemClaim,
                MemoirFrameworkItemClaim.framework_item_id.in_(
                    framework_item_ids
                ),
            ),
            "completion_items": await _count(
                db,
                MemoirCompletionItem,
                MemoirCompletionItem.project_id == project_id,
            ),
            "chapter_cards": await _count(
                db, MemoirChapterCard, MemoirChapterCard.project_id == project_id
            ),
            "book_quality_snapshots": await _count(
                db,
                MemoirBookQualityAuditSnapshot,
                MemoirBookQualityAuditSnapshot.project_id == project_id,
            ),
        },
        preserved_counts={
            "voice_sessions": await _count(
                db,
                MemoirVoiceSession,
                MemoirVoiceSession.project_id == project_id,
            ),
            "utterances": await _count(
                db,
                MemoirVoiceUtterance,
                MemoirVoiceUtterance.voice_session_id.in_(
                    select(MemoirVoiceSession.id).where(
                        MemoirVoiceSession.project_id == project_id
                    )
                ),
            ),
            "transcript_versions": await _count(
                db,
                MemoirTranscriptVersion,
                MemoirTranscriptVersion.voice_session_id.in_(
                    select(MemoirVoiceSession.id).where(
                        MemoirVoiceSession.project_id == project_id
                    )
                ),
            ),
            "chapters": await _count(
                db,
                MemoirBookPlanChapter,
                MemoirBookPlanChapter.project_id == project_id,
            ),
            "chapter_drafts": await _count(
                db,
                MemoirBookChapterDraft,
                MemoirBookChapterDraft.project_id == project_id,
            ),
            "chapter_draft_versions": await _count(
                db,
                MemoirBookChapterDraftVersion,
                MemoirBookChapterDraftVersion.chapter_id.in_(chapter_ids),
            ),
            "narrative_decision_audit_log": await _count(
                db,
                MemoirNarrativeDecision,
                MemoirNarrativeDecision.project_id == project_id,
            ),
        },
        source_utterance_count=source_count,
        rebuildable_utterance_count=len(rebuildable),
        invalid_utterance_ids=tuple(invalid_ids),
        active_session_ids=active_sessions,
        active_job_ids=active_jobs,
        user_decision_counts=await _user_decision_counts(
            db,
            project_id=project_id,
        ),
    )


def _assert_preview_is_rebuildable(
    preview: NarrativeProjectRebuildPreview,
) -> None:
    if preview.invalid_utterance_ids:
        raise NarrativeProjectRebuildError(
            "存在无法从当前转写重放的讲述："
            + ", ".join(preview.invalid_utterance_ids)
        )
    if preview.active_job_ids:
        raise NarrativeProjectRebuildError(
            "项目仍有运行中或等待中的后台任务："
            + ", ".join(preview.active_job_ids)
        )
    user_decisions = {
        key: count
        for key, count in preview.user_decision_counts.items()
        if count
    }
    if user_decisions:
        detail = ", ".join(
            f"{key}={count}" for key, count in sorted(user_decisions.items())
        )
        raise NarrativeProjectRebuildError(
            f"项目包含不能自动覆盖的用户决定：{detail}"
        )


async def _close_active_sessions(
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str,
    allow_close: bool,
) -> tuple[str, ...]:
    sessions = list(
        (
            await db.scalars(
                select(MemoirVoiceSession).where(
                    MemoirVoiceSession.project_id == project_id,
                    MemoirVoiceSession.status.in_(ACTIVE_SESSION_STATUSES),
                )
            )
        ).all()
    )
    if not sessions:
        return ()
    if not allow_close:
        raise NarrativeProjectRebuildError(
            "项目仍有活跃语音会话；确认会话已结束后使用 --close-active-sessions"
        )
    now = _utcnow()
    session_ids = [session.id for session in sessions]
    latest_utterance_activity = {
        session_id: created_at
        for session_id, created_at in (
            await db.execute(
                select(
                    MemoirVoiceUtterance.voice_session_id,
                    func.max(MemoirVoiceUtterance.created_at),
                )
                .where(
                    MemoirVoiceUtterance.voice_session_id.in_(session_ids)
                )
                .group_by(MemoirVoiceUtterance.voice_session_id)
            )
        ).all()
    }
    latest_message_activity = {
        session_id: updated_at
        for session_id, updated_at in (
            await db.execute(
                select(
                    MemoirChatMessage.voice_session_id,
                    func.max(MemoirChatMessage.updated_at),
                )
                .where(MemoirChatMessage.voice_session_id.in_(session_ids))
                .group_by(MemoirChatMessage.voice_session_id)
            )
        ).all()
    }
    recent = [
        session.id
        for session in sessions
        if now
        - max(
            timestamp
            for timestamp in (
                session.updated_at,
                session.created_at,
                latest_utterance_activity.get(session.id),
                latest_message_activity.get(session.id),
            )
            if timestamp is not None
        )
        < RECENT_ACTIVE_SESSION_WINDOW
    ]
    if recent:
        raise NarrativeProjectRebuildError(
            "最近 30 分钟内仍有活跃语音会话，拒绝自动关闭："
            + ", ".join(recent)
        )
    for session in sessions:
        session.status = MemoirVoiceSessionStatus.STOPPED.value
        session.stopped_at = now
        session.current_completion_item_id = None
        session.metadata_json = {
            **(session.metadata_json or {}),
            "narrative_rebuild_closed_at": now.isoformat(),
            "narrative_rebuild_run_id": run_id,
        }
        db.add(session)
    await db.flush()
    return tuple(session.id for session in sessions)


async def _invalidate_audit_events(
    db: AsyncSession,
    *,
    project_id: str,
) -> None:
    events = list(
        (
            await db.scalars(
                select(MemoirAutomationEvent).where(
                    MemoirAutomationEvent.project_id == project_id,
                    MemoirAutomationEvent.event_type.in_(
                        {
                            MemoirAutomationEventType.AUDIT_COMPLETED.value,
                            MemoirAutomationEventType.NARRATIVE_INGESTED.value,
                        }
                    ),
                )
            )
        ).all()
    )
    now_iso = datetime.now(UTC).isoformat()
    for event in events:
        metadata = dict(event.metadata_json or {})
        source = str(metadata.get("source") or "").strip()
        if (
            event.event_type
            == MemoirAutomationEventType.AUDIT_COMPLETED.value
            and source not in INVALIDATED_AUDIT_SOURCES
        ):
            continue
        event.metadata_json = {
            **metadata,
            RESOLUTION_KEY: "confirmed",
            RESOLUTION_REASON_KEY: REBUILD_RESOLUTION_REASON,
            RESOLVED_AT_KEY: now_iso,
        }
        db.add(event)


async def _reset_derived_state(
    db: AsyncSession,
    *,
    project_id: str,
) -> None:
    now = _utcnow()
    protected_completion_item_ids = set(
        (
            await db.scalars(
                select(MemoirVoiceUtterance.completion_item_id)
                .join(
                    MemoirVoiceSession,
                    MemoirVoiceSession.id
                    == MemoirVoiceUtterance.voice_session_id,
                )
                .where(
                    MemoirVoiceSession.project_id == project_id,
                    MemoirVoiceUtterance.completion_item_id.is_not(None),
                )
                .distinct()
            )
        ).all()
    )
    await db.execute(
        update(MemoirVoiceSession)
        .where(MemoirVoiceSession.project_id == project_id)
        .values(current_completion_item_id=None)
    )
    await db.execute(
        update(MemoirCompletionItem)
        .where(MemoirCompletionItem.project_id == project_id)
        .values(story_id=None, claim_id=None, related_claim_id=None)
    )
    delete_completion_filters = [
        MemoirCompletionItem.project_id == project_id,
        MemoirCompletionItem.status.in_(ACTIVE_COMPLETION_STATUSES),
    ]
    if protected_completion_item_ids:
        delete_completion_filters.append(
            MemoirCompletionItem.id.not_in(protected_completion_item_ids)
        )
    await db.execute(
        delete(MemoirCompletionItem).where(*delete_completion_filters)
    )

    framework_item_ids = select(MemoirFrameworkItem.id).join(
        MemoirProjectFramework,
        MemoirProjectFramework.id == MemoirFrameworkItem.project_framework_id,
    ).where(MemoirProjectFramework.project_id == project_id)
    await db.execute(
        delete(MemoirFrameworkItemClaim).where(
            MemoirFrameworkItemClaim.framework_item_id.in_(framework_item_ids)
        )
    )
    await db.execute(
        update(MemoirFrameworkItem)
        .where(MemoirFrameworkItem.id.in_(framework_item_ids))
        .values(
            evidence_state=MemoirFrameworkEvidenceState.NONE.value,
            assessment_summary="",
            assessment_version="",
            updated_at=now,
        )
    )
    await db.execute(
        delete(MemoirStoryClaim).where(
            MemoirStoryClaim.project_id == project_id
        )
    )
    await db.execute(
        delete(MemoirClaimEvidence).where(
            MemoirClaimEvidence.project_id == project_id
        )
    )
    await db.execute(
        update(MemoirClaim)
        .where(MemoirClaim.project_id == project_id)
        .values(time_anchor_claim_id=None, superseded_by_claim_id=None)
    )
    await db.execute(
        delete(MemoirClaim).where(MemoirClaim.project_id == project_id)
    )
    await db.execute(
        delete(MemoirStory).where(MemoirStory.project_id == project_id)
    )
    await db.execute(
        update(MemoirNarrativeEntity)
        .where(MemoirNarrativeEntity.project_id == project_id)
        .values(merged_into_entity_id=None)
    )
    await db.execute(
        delete(MemoirNarrativeEntity).where(
            MemoirNarrativeEntity.project_id == project_id
        )
    )
    await db.execute(
        delete(MemoirChapterCard).where(
            MemoirChapterCard.project_id == project_id
        )
    )
    await db.execute(
        delete(MemoirBookQualityAuditSnapshot).where(
            MemoirBookQualityAuditSnapshot.project_id == project_id
        )
    )
    chapter_ids = set(
        (
            await db.scalars(
                select(MemoirBookPlanChapter.id).where(
                    MemoirBookPlanChapter.project_id == project_id
                )
            )
        ).all()
    )
    await chapter_draft_state.mark_chapter_drafts_stale(
        db,
        project_id=project_id,
        chapter_ids=chapter_ids,
        reason=REBUILD_RESOLUTION_REASON,
    )
    await _invalidate_audit_events(db, project_id=project_id)
    await db.flush()


async def start_project_narrative_rebuild(
    db: AsyncSession,
    *,
    project_id: str,
    confirm_project_title: str,
    close_active_sessions: bool = False,
) -> NarrativeProjectRebuildStart:
    preview = await preview_project_narrative_rebuild(
        db,
        project_id=project_id,
    )
    if confirm_project_title != preview.project_title:
        raise NarrativeProjectRebuildError("项目标题确认不匹配")
    _assert_preview_is_rebuildable(preview)
    project = await _load_project(db, project_id)
    ai_model = project_ai_model(project)
    run_id = str(uuid.uuid4())
    closed_session_ids = await _close_active_sessions(
        db,
        project_id=project_id,
        run_id=run_id,
        allow_close=close_active_sessions,
    )
    rebuildable, invalid_ids, _ = await _source_utterances(
        db,
        project_id=project_id,
    )
    if invalid_ids or len(rebuildable) != preview.rebuildable_utterance_count:
        raise NarrativeProjectRebuildError(
            "预演后讲述数据发生变化，请重新执行 preview"
        )
    await _reset_derived_state(db, project_id=project_id)
    jobs: list[MemoirEditingJob] = []
    for utterance in rebuildable:
        job = await schedule_narrative_ingestion_job(
            db,
            project_id=project_id,
            utterance=utterance,
            source=REBUILD_SOURCE,
            ai_model=ai_model,
            rebuild_run_id=run_id,
        )
        if job is None:
            raise NarrativeProjectRebuildError(
                f"讲述无法进入重建队列：{utterance.id}"
            )
        jobs.append(job)
    if not jobs:
        raise NarrativeProjectRebuildError("项目没有可用于重建的有效讲述")
    await db.flush()
    return NarrativeProjectRebuildStart(
        project_id=project_id,
        project_title=preview.project_title,
        run_id=run_id,
        ingestion_job_ids=tuple(job.id for job in jobs),
        closed_session_ids=closed_session_ids,
    )


def _job_status_counts(jobs: list[MemoirEditingJob]) -> dict[str, int]:
    counts = {
        "total": len(jobs),
        "pending": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
    }
    for job in jobs:
        if job.status in counts:
            counts[job.status] += 1
    return counts


async def get_project_narrative_rebuild_status(
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str,
) -> NarrativeProjectRebuildStatus:
    await _load_project(db, project_id)
    candidates = list(
        (
            await db.scalars(
                select(MemoirEditingJob)
                .where(
                    MemoirEditingJob.project_id == project_id,
                    MemoirEditingJob.job_type.in_(REBUILD_JOB_TYPES),
                )
                .order_by(MemoirEditingJob.created_at, MemoirEditingJob.id)
            )
        ).all()
    )
    jobs = [
        job
        for job in candidates
        if isinstance(job.payload_json, dict)
        and job.payload_json.get("rebuild_run_id") == run_id
    ]
    if not jobs:
        raise NarrativeProjectRebuildError("找不到指定的重建批次")
    by_type = {
        job_type: [job for job in jobs if job.job_type == job_type]
        for job_type in REBUILD_JOB_TYPES
    }
    failed_jobs = tuple(
        {
            "id": job.id,
            "type": job.job_type,
            "status": job.status,
            "error": job.error_message,
        }
        for job in jobs
        if job.status
        in {
            MemoirEditingJobStatus.FAILED.value,
            MemoirEditingJobStatus.SKIPPED.value,
        }
    )
    return NarrativeProjectRebuildStatus(
        project_id=project_id,
        run_id=run_id,
        ingestion=_job_status_counts(
            by_type[MemoirEditingJobType.INGEST_NARRATIVE_STATE.value]
        ),
        topic_matching=_job_status_counts(
            by_type[MemoirEditingJobType.EVALUATE_TOPIC_MAP_CLAIMS.value]
        ),
        completion_refresh=_job_status_counts(
            by_type[MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value]
        ),
        failed_jobs=failed_jobs,
    )


async def finalize_project_narrative_rebuild(
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str,
) -> tuple[str, ...]:
    status = await get_project_narrative_rebuild_status(
        db,
        project_id=project_id,
        run_id=run_id,
    )
    if not status.ready_to_finalize:
        raise NarrativeProjectRebuildError(
            "叙事抽取尚未全部成功，不能开始章节补全检查"
        )
    if status.failed_jobs:
        raise NarrativeProjectRebuildError(
            "重建批次存在失败或跳过任务，不能继续"
        )
    existing_candidates = list(
        (
            await db.scalars(
                select(MemoirEditingJob).where(
                    MemoirEditingJob.project_id == project_id,
                    MemoirEditingJob.job_type
                    == MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value,
                )
            )
        ).all()
    )
    existing_jobs = [
        job
        for job in existing_candidates
        if isinstance(job.payload_json, dict)
        and job.payload_json.get("rebuild_run_id") == run_id
    ]
    if existing_jobs:
        return tuple(job.id for job in existing_jobs)
    chapter_ids = list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter.id)
                .where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.include_in_book.is_(True),
                )
                .order_by(
                    MemoirBookPlanChapter.sort_order,
                    MemoirBookPlanChapter.id,
                )
            )
        ).all()
    )
    if not chapter_ids:
        raise NarrativeProjectRebuildError("项目没有纳入成书的章节")
    jobs: list[MemoirEditingJob] = []
    for chapter_id in chapter_ids:
        jobs.append(
            await completion_refresh.enqueue_chapter_completion_refresh(
                db,
                project_id=project_id,
                chapter_id=chapter_id,
                include_semantic_audit=True,
                ai_model=None,
                source=REBUILD_SOURCE,
                rebuild_run_id=run_id,
            )
        )
    await db.flush()
    return tuple(job.id for job in jobs)
