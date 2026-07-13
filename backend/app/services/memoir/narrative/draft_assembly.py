from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirChapterNodeStatus,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
)
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.models.memoir_narrative import (
    MemoirChapterNode,
    MemoirDraftBlock,
    MemoirDraftBlockVersion,
)
from app.models.projects import Project
from app.schemas.memoir_narrative import (
    MemoirBookDraftBlockAssemblyResponse,
    MemoirChapterDraftBlockAssemblyResponse,
    MemoirDraftBlockAssemblyItem,
)
from app.services.memoir.narrative.draft_generation_context import (
    load_confirmed_contract_nodes,
)
from app.services.memoir.narrative.book_readiness import invalidate_formal_book_output
from app.services.memoir.book_output_manuscript import (
    effective_book_metadata,
    formal_book_title,
    manuscript_from_snapshot,
    stored_cover_style,
)
from app.services.memoir.book_output_snapshots import chapter_snapshot
from app.services.memoir.book_plan_sources import (
    DRAFT_STATUS_REASON_KEY,
    REMOVED_SOURCE_UTTERANCE_IDS_KEY,
    metadata_without_keys,
    source_change_metadata_keys,
)
from app.services.memoir.book_source_links import mark_chapter_sources_merged
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter
from app.services.memoir.image_assets import manuscript_image_snapshots


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _load_latest_chapter_draft_version(
    db: AsyncSession,
    *,
    draft_id: str,
) -> MemoirBookChapterDraftVersion | None:
    return await db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.draft_id == draft_id)
        .order_by(
            MemoirBookChapterDraftVersion.version.desc(),
            MemoirBookChapterDraftVersion.created_at.desc(),
            MemoirBookChapterDraftVersion.id.desc(),
        )
        .limit(1)
    )


async def _latest_chapter_version_number(db: AsyncSession, *, draft_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirBookChapterDraftVersion.version)).where(
            MemoirBookChapterDraftVersion.draft_id == draft_id
        )
    )
    return int(value or 0)


async def _acknowledge_assembled_chapter_sources(
    db: AsyncSession,
    *,
    chapter: MemoirBookPlanChapter,
    draft: MemoirBookChapterDraft,
    draft_version_id: str,
) -> None:
    draft.metadata_json = metadata_without_keys(
        draft.metadata_json,
        {DRAFT_STATUS_REASON_KEY, REMOVED_SOURCE_UTTERANCE_IDS_KEY},
    )
    chapter.metadata_json = metadata_without_keys(
        chapter.metadata_json,
        source_change_metadata_keys(),
    )
    db.add(draft)
    db.add(chapter)
    await mark_chapter_sources_merged(
        db,
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        draft_version_id=draft_version_id,
        include_removed=True,
    )


async def _current_chapter_assembly(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> tuple[
    str,
    list[tuple[MemoirDraftBlock, MemoirDraftBlockVersion, int]],
]:
    contract, nodes = await load_confirmed_contract_nodes(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    node_ids = [node.id for node in nodes]
    rows = list(
        (
            await db.execute(
                select(
                    MemoirDraftBlock,
                    MemoirDraftBlockVersion,
                    MemoirChapterNode.sort_order,
                )
                .join(
                    MemoirDraftBlockVersion,
                    MemoirDraftBlockVersion.id == MemoirDraftBlock.current_version_id,
                )
                .join(
                    MemoirChapterNode,
                    MemoirChapterNode.id == MemoirDraftBlock.node_id,
                )
                .where(
                    MemoirDraftBlock.project_id == project_id,
                    MemoirDraftBlock.chapter_id == chapter_id,
                    MemoirDraftBlock.node_id.in_(node_ids),
                    MemoirDraftBlock.block_role
                    != MemoirDraftBlockRole.LEGACY_SNAPSHOT.value,
                    MemoirDraftBlock.editorial_status
                    != MemoirDraftBlockEditorialStatus.EXCLUDED.value,
                    MemoirChapterNode.contract_id == contract.id,
                )
                .order_by(
                    MemoirChapterNode.sort_order,
                    MemoirDraftBlock.sort_order,
                    MemoirDraftBlock.id,
                )
            )
        ).tuples().all()
    )
    if not rows:
        raise ValueError("chapter has no structured draft blocks to assemble")
    if any(
        block.editorial_status != MemoirDraftBlockEditorialStatus.CONFIRMED.value
        for block, _, _ in rows
    ):
        raise ValueError("confirm every included draft block before assembling the chapter")
    if any(
        block.freshness_status != MemoirDraftBlockFreshnessStatus.CURRENT.value
        for block, _, _ in rows
    ):
        raise ValueError("refresh stale draft blocks before assembling the chapter")
    block_node_ids = {block.node_id for block, _, _ in rows}
    missing_ready_nodes = [
        node.title
        for node in nodes
        if node.status == MemoirChapterNodeStatus.READY.value
        and node.id not in block_node_ids
    ]
    if missing_ready_nodes:
        raise ValueError(
            "ready chapter nodes are missing draft blocks: "
            + ", ".join(missing_ready_nodes)
        )
    return contract.id, rows


async def assert_chapter_draft_snapshot_current(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    block_snapshot: list[dict[str, object]],
) -> None:
    _, rows = await _current_chapter_assembly(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    expected = [(block.id, version.id) for block, version, _ in rows]
    actual = [
        (str(item.get("block_id") or ""), str(item.get("block_version_id") or ""))
        for item in block_snapshot
        if isinstance(item, dict)
    ]
    if actual != expected:
        raise ValueError("chapter draft snapshot no longer matches current structured blocks")


async def assemble_chapter_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterDraftBlockAssemblyResponse:
    contract_id, rows = await _current_chapter_assembly(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )

    snapshots = [
        MemoirDraftBlockAssemblyItem(
            block_id=block.id,
            block_version_id=version.id,
            node_id=str(block.node_id),
            block_role=block.block_role,
            node_sort_order=node_sort_order,
            block_sort_order=block.sort_order,
            editorial_status=block.editorial_status,
            freshness_status=block.freshness_status,
            locked=block.locked,
            content_char_count=len(version.content.strip()),
        )
        for block, version, node_sort_order in rows
    ]
    content = "\n\n".join(version.content.strip() for _, version, _ in rows).strip()
    snapshot_payload = [item.model_dump(mode="json") for item in snapshots]
    draft = await db.scalar(
        select(MemoirBookChapterDraft)
        .where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
    )
    if draft is None:
        draft = MemoirBookChapterDraft(
            project_id=project_id,
            chapter_id=chapter_id,
            status=MemoirBookChapterDraftStatus.DRAFT.value,
            metadata_json={"source": "draft_blocks", "contract_id": contract_id},
        )
        db.add(draft)
        await db.flush()
    else:
        latest = await _load_latest_chapter_draft_version(
            db,
            draft_id=draft.id,
        )
        if (
            latest is not None
            and latest.content == content
            and latest.block_snapshot_json == snapshot_payload
        ):
            chapter = await db.get(MemoirBookPlanChapter, chapter_id)
            if chapter is None or chapter.project_id != project_id:
                raise ValueError("chapter does not belong to the project")
            await _acknowledge_assembled_chapter_sources(
                db,
                chapter=chapter,
                draft=draft,
                draft_version_id=latest.id,
            )
            return MemoirChapterDraftBlockAssemblyResponse(
                project_id=project_id,
                chapter_id=chapter_id,
                draft_id=draft.id,
                draft_version_id=latest.id,
                version=latest.version,
                content=latest.content,
                blocks=snapshots,
                created_at=latest.created_at,
            )
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = {
            **(draft.metadata_json if isinstance(draft.metadata_json, dict) else {}),
            "source": "draft_blocks",
            "contract_id": contract_id,
        }
        draft.updated_at = _utcnow()
        db.add(draft)
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=await _latest_chapter_version_number(db, draft_id=draft.id) + 1,
        content=content,
        created_by=MemoirCreatedBy.SYSTEM.value,
        model=None,
        prompt_snapshot="",
        feedback=None,
        block_snapshot_json=snapshot_payload,
    )
    db.add(version)
    await db.flush()
    chapter = await db.get(MemoirBookPlanChapter, chapter_id)
    if chapter is None or chapter.project_id != project_id:
        raise ValueError("chapter does not belong to the project")
    await _acknowledge_assembled_chapter_sources(
        db,
        chapter=chapter,
        draft=draft,
        draft_version_id=version.id,
    )
    return MemoirChapterDraftBlockAssemblyResponse(
        project_id=project_id,
        chapter_id=chapter_id,
        draft_id=draft.id,
        draft_version_id=version.id,
        version=version.version,
        content=version.content,
        blocks=snapshots,
        created_at=version.created_at,
    )


async def _latest_output_version_number(db: AsyncSession, *, output_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirBookOutputVersion.version)).where(
            MemoirBookOutputVersion.output_id == output_id
        )
    )
    return int(value or 0)


def _latest_chapter_version(
    chapter: MemoirBookPlanChapter,
) -> MemoirBookChapterDraftVersion | None:
    if chapter.draft is None or not chapter.draft.versions:
        return None
    return max(
        chapter.draft.versions,
        key=lambda version: (version.version, version.created_at, version.id),
    )


async def _load_latest_book_output_version(
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


async def assemble_book_output(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookDraftBlockAssemblyResponse:
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
    )
    if plan is None:
        raise ValueError("book plan does not belong to the project")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise ValueError("confirm the book plan before assembling the book output")
    chapters = sorted(plan.chapters, key=lambda chapter: (chapter.sort_order, chapter.id))
    if not chapters:
        raise ValueError("book plan has no chapters")
    chapter_versions: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]] = []
    incomplete_titles: list[str] = []
    for chapter in chapters:
        version = _latest_chapter_version(chapter)
        if (
            chapter.draft is None
            or chapter.draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value
            or version is None
            or not [
                item
                for item in (version.block_snapshot_json or [])
                if isinstance(item, dict)
            ]
        ):
            incomplete_titles.append(chapter.title or "未命名章节")
            continue
        try:
            await assert_chapter_draft_snapshot_current(
                db,
                project_id=project_id,
                chapter_id=chapter.id,
                block_snapshot=version.block_snapshot_json,
            )
        except ValueError:
            incomplete_titles.append(chapter.title or "未命名章节")
            continue
        chapter_versions.append((chapter, version))
    if incomplete_titles:
        raise ValueError(
            "book output requires confirmed structured chapter snapshots: "
            + ", ".join(incomplete_titles)
        )

    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError("project does not exist")
    source_refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    toc = "\n".join(
        f"{index}. {chapter.title}"
        for index, (chapter, _) in enumerate(chapter_versions, start=1)
    )
    chapter_snapshots: list[dict[str, object]] = []
    body: list[str] = []
    for index, (chapter, version) in enumerate(chapter_versions, start=1):
        chapter_content = version.content.strip()
        body.append(f"第{index}章 {chapter.title}\n\n{chapter_content}")
        chapter_snapshots.append(
            chapter_snapshot(
                chapter,
                version,
                chapter_index=index,
                source_refs=source_refs_by_chapter.get(chapter.id),
            )
        )
    output = await db.scalar(
        select(MemoirBookOutput)
        .where(MemoirBookOutput.project_id == project_id)
    )
    title = output.title if output is not None else formal_book_title(plan.title or "我的回忆录")
    content = (f"{title}\n\n目录\n{toc}\n\n" + "\n\n".join(body)).strip()
    previous_version: MemoirBookOutputVersion | None = None
    if output is None:
        output = MemoirBookOutput(
            project_id=project_id,
            plan_id=plan.id,
            title=title,
            status=MemoirOutputStatus.DRAFT.value,
            metadata_json={"source": "draft_blocks"},
        )
        db.add(output)
        await db.flush()
    else:
        previous_version = await _load_latest_book_output_version(
            db,
            output_id=output.id,
        )
        if (
            previous_version is not None
            and previous_version.content == content
            and previous_version.chapter_snapshot_json == chapter_snapshots
        ):
            return MemoirBookDraftBlockAssemblyResponse(
                project_id=project_id,
                output_id=output.id,
                output_version_id=previous_version.id,
                version=previous_version.version,
                content=previous_version.content,
                chapter_snapshot_json=chapter_snapshots,
                created_at=previous_version.created_at,
            )
        output.plan_id = plan.id
        output.title = title
        invalidate_formal_book_output(output)
        output.metadata_json = {
            **(output.metadata_json if isinstance(output.metadata_json, dict) else {}),
            "source": "draft_blocks",
            "book_tts": None,
        }
        output.updated_at = _utcnow()
        db.add(output)
    chapter_illustrations, image_appendix = await manuscript_image_snapshots(
        db,
        project_id=project_id,
    )
    manuscript = manuscript_from_snapshot(
        title=title,
        content=content,
        snapshot=chapter_snapshots,
        cover_style_value=stored_cover_style(previous_version) if previous_version else "classic_plain",
        metadata=effective_book_metadata(
            project=project,
            stored=previous_version.manuscript_json if previous_version else None,
        ),
        chapter_illustrations_by_chapter=chapter_illustrations,
        image_appendix=image_appendix,
    )
    version = MemoirBookOutputVersion(
        output_id=output.id,
        project_id=project_id,
        plan_id=plan.id,
        version=await _latest_output_version_number(db, output_id=output.id) + 1,
        content=content,
        chapter_snapshot_json=chapter_snapshots,
        manuscript_json=manuscript.model_dump(mode="json") if manuscript else {},
        created_by=MemoirCreatedBy.SYSTEM.value,
    )
    db.add(version)
    await db.flush()
    return MemoirBookDraftBlockAssemblyResponse(
        project_id=project_id,
        output_id=output.id,
        output_version_id=version.id,
        version=version.version,
        content=version.content,
        chapter_snapshot_json=chapter_snapshots,
        created_at=version.created_at,
    )
