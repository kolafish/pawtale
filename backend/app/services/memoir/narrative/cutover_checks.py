from __future__ import annotations

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirClaimVerificationStatus,
    MemoirStoryLifecycleStatus,
)
from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.domain.projects.types import ProjectType
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirEditingJob,
    MemoirVoiceSession,
)
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirDraftBlock,
    MemoirStory,
    MemoirStoryClaim,
)
from app.models.projects import Project
from app.services.memoir.narrative.backfill import run_narrative_ingestion_backfill


async def active_memoir_project_ids(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> list[str]:
    statement = (
        select(Project.id)
        .where(
            Project.type == ProjectType.MEMOIR.value,
            or_(
                exists().where(MemoirVoiceSession.project_id == Project.id),
                exists().where(MemoirBookPlan.project_id == Project.id),
            ),
        )
        .order_by(Project.created_at, Project.id)
    )
    if project_id:
        statement = statement.where(Project.id == project_id)
    return list((await db.scalars(statement)).all())


async def _count(db: AsyncSession, statement) -> int:
    return int((await db.scalar(statement)) or 0)


async def _ingestion_job_counts(
    db: AsyncSession,
    *,
    project_id: str,
) -> dict[str, int]:
    rows = (
        await db.execute(
            select(MemoirEditingJob.status, func.count(MemoirEditingJob.id))
            .where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type
                == MemoirEditingJobType.INGEST_NARRATIVE_STATE.value,
            )
            .group_by(MemoirEditingJob.status)
        )
    ).all()
    counts = {status.value: 0 for status in MemoirEditingJobStatus}
    counts.update({str(status): int(count) for status, count in rows})
    return counts


async def project_migration_report(
    db: AsyncSession,
    *,
    project_id: str,
) -> dict[str, object]:
    ingestion = await run_narrative_ingestion_backfill(
        db,
        project_id=project_id,
        dry_run=True,
    )
    job_counts = await _ingestion_job_counts(db, project_id=project_id)
    chapter_count = await _count(
        db,
        select(func.count(MemoirBookPlanChapter.id)).where(
            MemoirBookPlanChapter.project_id == project_id,
        ),
    )
    missing_contract_count = await _count(
        db,
        select(func.count(MemoirBookPlanChapter.id))
        .outerjoin(
            MemoirChapterContract,
            and_(
                MemoirChapterContract.project_id == MemoirBookPlanChapter.project_id,
                MemoirChapterContract.chapter_id == MemoirBookPlanChapter.id,
            ),
        )
        .where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirChapterContract.id.is_(None),
        ),
    )
    confirmed_contract_count = await _count(
        db,
        select(func.count(func.distinct(MemoirChapterContract.chapter_id)))
        .join(
            MemoirBookPlanChapter,
            MemoirBookPlanChapter.id == MemoirChapterContract.chapter_id,
        )
        .where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.status == MemoirChapterContractStatus.CONFIRMED.value,
        ),
    )
    claim_count = await _count(
        db,
        select(func.count(MemoirClaim.id)).where(MemoirClaim.project_id == project_id),
    )
    claims_without_evidence = await _count(
        db,
        select(func.count(MemoirClaim.id))
        .outerjoin(
            MemoirClaimEvidence,
            and_(
                MemoirClaimEvidence.project_id == MemoirClaim.project_id,
                MemoirClaimEvidence.claim_id == MemoirClaim.id,
            ),
        )
        .where(
            MemoirClaim.project_id == project_id,
            MemoirClaimEvidence.id.is_(None),
        ),
    )
    chapters_with_structured_blocks = await _count(
        db,
        select(func.count(func.distinct(MemoirDraftBlock.chapter_id)))
        .join(
            MemoirBookPlanChapter,
            MemoirBookPlanChapter.id == MemoirDraftBlock.chapter_id,
        )
        .where(
            MemoirDraftBlock.project_id == project_id,
        ),
    )
    story_count = await _count(
        db,
        select(func.count(MemoirStory.id)).where(MemoirStory.project_id == project_id),
    )
    claims_without_story = await _count(
        db,
        select(func.count(MemoirClaim.id)).where(
            MemoirClaim.project_id == project_id,
            MemoirClaim.superseded_by_claim_id.is_(None),
            MemoirClaim.verification_status
            != MemoirClaimVerificationStatus.REJECTED.value,
            exists().where(
                MemoirClaimEvidence.project_id == MemoirClaim.project_id,
                MemoirClaimEvidence.claim_id == MemoirClaim.id,
            ),
            ~exists()
            .where(
                MemoirStoryClaim.project_id == MemoirClaim.project_id,
                MemoirStoryClaim.claim_id == MemoirClaim.id,
            )
            .where(
                exists().where(
                    MemoirStory.project_id == MemoirStoryClaim.project_id,
                    MemoirStory.id == MemoirStoryClaim.story_id,
                    MemoirStory.lifecycle_status
                    == MemoirStoryLifecycleStatus.ACTIVE.value,
                )
            ),
        ),
    )
    source_link_count = await _count(
        db,
        select(func.count(MemoirChapterSourceLink.id)).where(
            MemoirChapterSourceLink.project_id == project_id
        ),
    )
    blockers: list[str] = []
    if int(ingestion["candidate_count"]):
        blockers.append("narrative ingestion candidates remain")
    if job_counts[MemoirEditingJobStatus.PENDING.value]:
        blockers.append("narrative ingestion jobs are pending")
    if job_counts[MemoirEditingJobStatus.RUNNING.value]:
        blockers.append("narrative ingestion jobs are running")
    if job_counts[MemoirEditingJobStatus.FAILED.value]:
        blockers.append("narrative ingestion jobs failed")
    if claims_without_evidence:
        blockers.append("Claims without evidence remain")
    if claims_without_story:
        blockers.append("evidence-backed Claims are not grouped into active Stories")
    if missing_contract_count:
        blockers.append("chapter contract suggestions are missing")
    chapter_workflow_blockers: list[str] = []
    if confirmed_contract_count != chapter_count:
        chapter_workflow_blockers.append(
            "chapter contracts still require user confirmation"
        )
    if chapters_with_structured_blocks != chapter_count:
        chapter_workflow_blockers.append(
            "chapters do not all have structured draft blocks"
        )
    return {
        "project_id": project_id,
        "chapter_count": chapter_count,
        "source_link_count": source_link_count,
        "ingestion": ingestion,
        "ingestion_job_counts": job_counts,
        "claim_count": claim_count,
        "claims_without_evidence_count": claims_without_evidence,
        "story_count": story_count,
        "claims_without_story_count": claims_without_story,
        "missing_contract_count": missing_contract_count,
        "confirmed_contract_count": confirmed_contract_count,
        "chapters_with_structured_blocks_count": chapters_with_structured_blocks,
        "migration_complete": not blockers,
        "migration_blockers": blockers,
        "cutover_data_ready": not blockers,
        "cutover_data_blockers": blockers,
        "chapter_workflow_ready": not chapter_workflow_blockers,
        "chapter_workflow_blockers": chapter_workflow_blockers,
    }


async def collect_migration_report(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> dict[str, object]:
    project_ids = await active_memoir_project_ids(db, project_id=project_id)
    projects = [
        await project_migration_report(db, project_id=current_project_id)
        for current_project_id in project_ids
    ]
    return {
        "project_id": project_id,
        "active_project_count": len(projects),
        "migration_complete": all(bool(item["migration_complete"]) for item in projects),
        "cutover_data_ready": all(bool(item["cutover_data_ready"]) for item in projects),
        "chapter_workflow_ready": all(
            bool(item["chapter_workflow_ready"]) for item in projects
        ),
        "projects": projects,
    }


async def assert_ingestion_complete(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> None:
    report = await collect_migration_report(db, project_id=project_id)
    blockers = [
        item
        for item in report["projects"]
        if any(
            blocker.startswith("narrative ingestion")
            or blocker == "Claims without evidence remain"
            for blocker in item["migration_blockers"]
        )
    ]
    if blockers:
        raise RuntimeError(
            "Narrative ingestion must finish without failures before the next backfill stage"
        )


async def assert_story_graph_complete(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> None:
    report = await collect_migration_report(db, project_id=project_id)
    if any(int(item["claims_without_story_count"]) for item in report["projects"]):
        raise RuntimeError(
            "Evidence-backed Claims must belong to active Stories before chapter structures"
        )
