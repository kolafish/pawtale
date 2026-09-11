from __future__ import annotations

from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirCreatedBy
from app.models.memoir import MemoirBookOutput, MemoirBookOutputVersion
from app.services.memoir.book_output_snapshots import book_content_from_chapter_snapshots


async def load_latest_book_output_version(
    db: AsyncSession,
    *,
    output_id: str,
) -> MemoirBookOutputVersion | None:
    return await db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.output_id == output_id)
        .order_by(
            MemoirBookOutputVersion.version.desc(),
            MemoirBookOutputVersion.created_at.desc(),
            MemoirBookOutputVersion.id.desc(),
        )
        .limit(1)
    )


async def append_book_output_version(
    db: AsyncSession,
    *,
    output: MemoirBookOutput,
    content: str,
    chapter_snapshot_json: list[dict[str, object]],
    manuscript_json: dict[str, object],
    created_by: MemoirCreatedBy,
) -> MemoirBookOutputVersion:
    await db.execute(select(MemoirBookOutput.id).where(MemoirBookOutput.id == output.id).with_for_update())
    latest = await load_latest_book_output_version(db, output_id=output.id)
    version = MemoirBookOutputVersion(
        output_id=output.id,
        project_id=output.project_id,
        plan_id=output.plan_id,
        version=(latest.version if latest else 0) + 1,
        content=content,
        chapter_snapshot_json=deepcopy(chapter_snapshot_json),
        manuscript_json=deepcopy(manuscript_json),
        created_by=created_by.value,
    )
    db.add(version)
    await db.flush()
    return version


def _same_book_body(left: MemoirBookOutputVersion, right: MemoirBookOutputVersion) -> bool:
    if left.chapter_snapshot_json != right.chapter_snapshot_json:
        return False
    if left.content != right.content and not all(
        version.content == book_content_from_chapter_snapshots(
            title=book_output_version_title(version, fallback=""),
            snapshots=version.chapter_snapshot_json,
        )
        for version in (left, right)
    ):
        return False
    # Design fields and image placement may change without revising the book.
    # Chapter introductions and quotations, however, are editorial content.
    def chapters(version: MemoirBookOutputVersion) -> list[dict]:
        return [
            {key: value for key, value in chapter.items() if key != "illustrations"}
            for chapter in version.manuscript_json.get("chapters", [])
        ]
    return chapters(left) == chapters(right)


async def book_body_is_protected(
    db: AsyncSession, *, output: MemoirBookOutput, version: MemoirBookOutputVersion,
) -> bool:
    from app.services.memoir import book_editorial_actions, book_review_answer_state

    applied_ids = {
        book_review_answer_state.application_marker(output).get("output_version_id"),
        book_editorial_actions.application_marker(output).get("output_version_id"),
    }
    # Trace unchanged body content through title/cover/image versions. This also
    # classifies existing design-only user versions without changing their audit provenance.
    current = version
    while True:
        if current.id in applied_ids:
            return True
        previous = await db.scalar(
            select(MemoirBookOutputVersion)
            .where(MemoirBookOutputVersion.output_id == output.id, MemoirBookOutputVersion.version < current.version)
            .order_by(MemoirBookOutputVersion.version.desc()).limit(1)
        )
        if previous is None or not _same_book_body(current, previous):
            return current.created_by == MemoirCreatedBy.USER.value
        current = previous


def book_output_version_title(
    version: MemoirBookOutputVersion,
    *,
    fallback: str,
) -> str:
    manuscript = (
        version.manuscript_json
        if isinstance(version.manuscript_json, dict)
        else {}
    )
    profile = manuscript.get("profile")
    if isinstance(profile, dict):
        title = profile.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    for line in version.content.splitlines():
        title = line.strip().lstrip("#").strip()
        if title:
            return title
    return fallback
