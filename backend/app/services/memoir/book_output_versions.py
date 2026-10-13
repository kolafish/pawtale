from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirCreatedBy
from app.models.memoir import MemoirBookOutput, MemoirBookOutputVersion
from app.schemas.memoir_book import MemoirBookLevelEditorialCopyTarget
from app.services.memoir.book_output_manuscript import stored_manuscript_metadata
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
    # Intro/quotes have field ownership; they do not make the assembled body
    # a manual rewrite. Continue tracing through them to protect real body edits.
    def chapters(version: MemoirBookOutputVersion) -> list[dict]:
        return [
            {key: value for key, value in chapter.items() if key not in {"intro", "pull_quotes", "illustrations"}}
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


def manuscript_chapters(version: MemoirBookOutputVersion) -> dict[str, dict[str, object]]:
    manuscript = version.manuscript_json if isinstance(version.manuscript_json, dict) else {}
    chapters = manuscript.get("chapters")
    return {
        chapter["chapter_id"]: chapter
        for chapter in (chapters if isinstance(chapters, list) else [])
        if isinstance(chapter, dict) and isinstance(chapter.get("chapter_id"), str)
    }


class AutomaticEditorialCopyState(BaseModel):
    input_signature: str = ""
    processed_version: int = 0
    status: Literal["succeeded", "failed", "not_started"] = "not_started"
    manual_targets: list[MemoirBookLevelEditorialCopyTarget] = Field(default_factory=list)
    manual_chapter_fields: dict[str, list[Literal["intro", "pull_quotes"]]] = Field(default_factory=dict)


AUTOMATIC_EDITORIAL_COPY_KEY = "automatic_editorial_copy"


def automatic_editorial_copy_state(
    output: MemoirBookOutput, versions: list[MemoirBookOutputVersion],
) -> AutomaticEditorialCopyState:
    state = AutomaticEditorialCopyState.model_validate((output.metadata_json or {}).get(AUTOMATIC_EDITORIAL_COPY_KEY, {}))
    if not state.processed_version and versions and versions[0].created_by in {"user", "ai"}:
        state.manual_targets = ["preface", "afterword", "back_cover_quote"]
        state.manual_chapter_fields = {
            chapter_id: ["intro", "pull_quotes"] for chapter_id in manuscript_chapters(versions[0])
        }
    # Before the first automatic pass, every AI copy change was explicitly
    # requested. Thereafter inspect only versions after our last saved pass.
    for before, after in zip(versions, versions[1:]):
        if after.version <= state.processed_version or after.created_by not in {"user", "ai"}:
            continue
        old = stored_manuscript_metadata(before.manuscript_json)
        new = stored_manuscript_metadata(after.manuscript_json)
        for target in ("preface", "afterword", "back_cover_quote"):
            if old.get(target) != new.get(target) and target not in state.manual_targets:
                state.manual_targets.append(target)
        old_chapters, new_chapters = manuscript_chapters(before), manuscript_chapters(after)
        for chapter_id, chapter in new_chapters.items():
            for field in ("intro", "pull_quotes"):
                if old_chapters.get(chapter_id, {}).get(field) != chapter.get(field):
                    fields = state.manual_chapter_fields.setdefault(chapter_id, [])
                    if field not in fields:
                        fields.append(field)
    return state
