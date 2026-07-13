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
    existing_chapter_ids = (
        set(
            (
                await db.scalars(
                    select(MemoirChapterContract.chapter_id).where(
                        MemoirChapterContract.chapter_id.in_(chapter_ids)
                    )
                )
            ).all()
        )
        if chapter_ids
        else set()
    )
    missing_contract_chapters = [
        chapter for chapter in chapters if chapter.id not in existing_chapter_ids
    ]
    legacy_contract_ids = {
        chapter.id: chapter_contracts.legacy_contract_suggestion_id(
            project_id=chapter.project_id,
            chapter_id=chapter.id,
        )
        for chapter in chapters
    }
    legacy_contracts = (
        list(
            (
                await db.scalars(
                    select(MemoirChapterContract).where(
                        MemoirChapterContract.id.in_(legacy_contract_ids.values())
                    )
                )
            ).all()
        )
        if legacy_contract_ids
        else []
    )
    legacy_contracts_by_chapter = {
        contract.chapter_id: contract for contract in legacy_contracts
    }

    action_limit = None if limit is None else max(0, limit)
    candidates = (
        missing_contract_chapters
        if action_limit is None
        else missing_contract_chapters[:action_limit]
    )
    remaining_limit = (
        None if action_limit is None else max(0, action_limit - len(candidates))
    )
    repair_candidates = [
        legacy_contracts_by_chapter[chapter.id]
        for chapter in chapters
        if chapter.id in legacy_contracts_by_chapter
    ]
    if remaining_limit is not None:
        repair_candidates = repair_candidates[:remaining_limit]

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
            idempotency_key=chapter_contracts.LEGACY_SUGGESTION_IDEMPOTENCY_KEY,
        )
        created_contract_ids.append(contract.id)

    repaired_contract_ids: list[str] = []
    would_repair_contract_ids: list[str] = []
    skipped_modified_contract_ids: list[str] = []
    unchanged_contract_ids: list[str] = []
    for contract in repair_candidates:
        repair_status = await chapter_contracts.repair_legacy_contract_suggestion(
            db,
            contract=contract,
        )
        if repair_status == "repaired":
            if dry_run:
                would_repair_contract_ids.append(contract.id)
            else:
                repaired_contract_ids.append(contract.id)
        elif repair_status == "skipped_modified":
            skipped_modified_contract_ids.append(contract.id)
        elif repair_status == "unchanged":
            unchanged_contract_ids.append(contract.id)

    if dry_run:
        await db.rollback()
    else:
        await db.commit()
    return {
        "dry_run": dry_run,
        "project_id": project_id,
        "scanned_chapter_count": len(chapters),
        "skipped_existing_contract_count": len(chapters)
        - len(missing_contract_chapters),
        "skipped_by_limit_count": (
            len(missing_contract_chapters)
            + len(legacy_contracts)
            - len(candidates)
            - len(repair_candidates)
        ),
        "candidate_count": len(candidates),
        "created_count": len(created_contract_ids),
        "created_contract_ids": created_contract_ids,
        "repair_scanned_count": len(repair_candidates),
        "repair_candidate_count": len(repaired_contract_ids)
        + len(would_repair_contract_ids),
        "repaired_count": len(repaired_contract_ids),
        "repaired_contract_ids": repaired_contract_ids,
        "would_repair_contract_ids": would_repair_contract_ids,
        "skipped_modified_contract_ids": skipped_modified_contract_ids,
        "unchanged_contract_ids": unchanged_contract_ids,
        "chapters": reports,
    }
