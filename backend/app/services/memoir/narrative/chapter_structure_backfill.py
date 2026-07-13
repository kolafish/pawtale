from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import MemoirChapterContract
from app.models.projects import Project
from app.services.memoir.narrative import chapter_contracts


async def run_chapter_structure_backfill(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    dry_run: bool = True,
    limit: int | None = None,
) -> dict[str, object]:
    statement = (
        select(MemoirBookPlanChapter)
        .join(Project, Project.id == MemoirBookPlanChapter.project_id)
        .where(Project.type == ProjectType.MEMOIR.value)
        .order_by(
            MemoirBookPlanChapter.project_id,
            MemoirBookPlanChapter.sort_order,
            MemoirBookPlanChapter.id,
        )
    )
    if project_id:
        statement = statement.where(MemoirBookPlanChapter.project_id == project_id)
    chapters = list((await db.scalars(statement)).all())
    chapter_ids = [chapter.id for chapter in chapters]
    existing_chapter_ids = set(
        (
            await db.scalars(
                select(MemoirChapterContract.chapter_id).where(
                    MemoirChapterContract.chapter_id.in_(chapter_ids)
                )
            )
        ).all()
    ) if chapter_ids else set()
    missing_contract_chapters = [
        chapter for chapter in chapters if chapter.id not in existing_chapter_ids
    ]
    candidates = missing_contract_chapters
    if limit is not None:
        candidates = candidates[: max(0, limit)]

    reports: list[dict[str, object]] = []
    created_contract_ids: list[str] = []
    for chapter in candidates:
        stories = await chapter_contracts.legacy_story_candidates(
            db,
            project_id=chapter.project_id,
            chapter_id=chapter.id,
        )
        report = {
            "project_id": chapter.project_id,
            "chapter_id": chapter.id,
            "chapter_title": chapter.title,
            "story_candidate_count": len(stories),
        }
        reports.append(report)
        if dry_run:
            continue
        contract = await chapter_contracts.create_legacy_contract_suggestion(
            db,
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            idempotency_key="chapter-structure-backfill-v1",
        )
        created_contract_ids.append(contract.id)

    if dry_run:
        await db.rollback()
    else:
        await db.commit()
    return {
        "dry_run": dry_run,
        "project_id": project_id,
        "scanned_chapter_count": len(chapters),
        "skipped_existing_contract_count": len(chapters) - len(missing_contract_chapters),
        "skipped_by_limit_count": len(missing_contract_chapters) - len(candidates),
        "candidate_count": len(candidates),
        "created_count": len(created_contract_ids),
        "created_contract_ids": created_contract_ids,
        "chapters": reports,
    }
