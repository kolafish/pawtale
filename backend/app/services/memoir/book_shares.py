from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.auth import session_token_hash
from app.clock import utcnow_naive
from app.config import get_settings
from app.domain.memoir.types import MemoirCreatedBy, MemoirOutputStatus
from app.models.memoir import (
    MemoirBookOutput, MemoirBookOutputVersion, MemoirBookShare,
    MemoirBookPlanChapter, MemoirBookChapterDraftVersion, MemoirChapterShare,
)
from app.models.projects import Project
from app.schemas.memoir_book import (
    MemoirBookOutputResponse,
    MemoirBookOutputVersionSummaryResponse,
    MemoirBookShareCreateResponse,
    MemoirBookShareStatusResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_output_versions import book_output_version_title
from app.services.memoir.book_outputs import _output_version_response, get_book_output
from app.services.memoir.book_output_snapshots import chapter_snapshot
from app.services.memoir.book_output_manuscript import manuscript_from_snapshot
from app.services.memoir.completion_read_model import chapter_workspace


SHARE_LIFETIME = timedelta(days=7)
STABLE_TOKEN_PATTERN = re.compile(r"(v1|c1)\.([0-9a-f-]{36})\.([0-9a-f]{64})\Z")
ShareResource = MemoirBookOutput | MemoirBookPlanChapter
ShareVersion = MemoirBookOutputVersion | MemoirBookChapterDraftVersion


def _stable_token(output_id: str, token_hash: str, *, chapter: bool = False) -> str:
    # Legacy shares retain only a hash; bind a recoverable token to that hash
    # without making the stored hash usable as a public link.
    namespace = "memoir-chapter-share:v1" if chapter else "memoir-book-share:v1"
    signature = hmac.new(
        get_settings().app_secret_key.encode("utf-8"),
        f"{namespace}\0{output_id}\0{token_hash}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{'c1' if chapter else 'v1'}.{output_id}.{signature}"


def _share_table(chapter: bool) -> tuple[
    type[MemoirBookShare] | type[MemoirChapterShare], InstrumentedAttribute[str],
]:
    return (MemoirChapterShare, MemoirChapterShare.chapter_id) if chapter else (MemoirBookShare, MemoirBookShare.output_id)


async def _owned_resource(
    db: AsyncSession, user_id: str, project_id: str, chapter_id: str | None,
) -> ShareResource:
    await load_memoir_project(db, project_id, user_id)
    if chapter_id is not None:
        resource = await db.scalar(select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id, MemoirBookPlanChapter.id == chapter_id,
        ))
    else:
        resource = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    if resource is None:
        raise HTTPException(status_code=404, detail="章节不存在" if chapter_id is not None else "全书稿不存在")
    return resource


async def _current_version(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str | None = None,
) -> tuple[ShareVersion | None, bool]:
    if chapter_id is not None:
        workspace = await chapter_workspace(db, project_id=project_id, chapter_id=chapter_id)
        version = await db.get(MemoirBookChapterDraftVersion, workspace.draft_version_id) if workspace.draft_version_id else None
        return version, workspace.draft_needs_rewrite
    output = await get_book_output(db, user_id=user_id, project_id=project_id)
    version_id = output.latest_version.id if output.latest_version else None
    version = await db.get(MemoirBookOutputVersion, version_id) if version_id else None
    return version, output.is_stale


async def share_status(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None,
) -> MemoirBookShareStatusResponse:
    output = await _owned_resource(db, user_id, project_id, chapter_id)
    share_model, share_key = _share_table(chapter_id is not None)
    share = await db.get(share_model, output.id, populate_existing=True)
    active = share is not None and share.expires_at > utcnow_naive()
    version, stale = await _current_version(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    return MemoirBookShareStatusResponse(
        active=active,
        expires_at=share.expires_at if active else None,
        token=_stable_token(output.id, share.token_hash, chapter=chapter_id is not None) if active else None,
        stale=stale,
        current=bool(active and not stale and version and share.version_id == version.id),
    )


async def create_share(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None,
) -> MemoirBookShareCreateResponse:
    output = await _owned_resource(db, user_id, project_id, chapter_id)
    share_model, share_key = _share_table(chapter_id is not None)
    version, stale = await _current_version(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    if stale:
        raise HTTPException(status_code=409, detail="书稿正在更新，请完成更新后再分享")
    if version is None or not version.content.strip():
        raise HTTPException(status_code=409, detail="请先生成可阅读的书稿")
    token = secrets.token_urlsafe(32)
    token_hash = session_token_hash(token)
    now = utcnow_naive()
    expires_at = now + SHARE_LIFETIME
    statement = insert(share_model).values(
        **{share_key.key: output.id},
        version_id=version.id,
        token_hash=token_hash,
        expires_at=expires_at,
        created_at=now,
    )
    await db.execute(statement.on_conflict_do_update(
        index_elements=[share_key],
        set_={
            "version_id": version.id,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "created_at": now,
        },
    ))
    await db.commit()
    return MemoirBookShareCreateResponse(token=_stable_token(output.id, token_hash, chapter=chapter_id is not None), expires_at=expires_at)


async def update_share(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None,
) -> MemoirBookShareCreateResponse:
    output = await _owned_resource(db, user_id, project_id, chapter_id)
    share_model, share_key = _share_table(chapter_id is not None)
    share = await db.get(share_model, output.id, populate_existing=True)
    if share is None or share.expires_at <= utcnow_naive():
        raise HTTPException(status_code=409, detail="阅读链接已失效，请重新生成")
    version, stale = await _current_version(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    if stale:
        raise HTTPException(status_code=409, detail="书稿正在更新，请完成更新后再分享")
    if version is None or not version.content.strip():
        raise HTTPException(status_code=409, detail="请先生成可阅读的书稿")
    share.version_id = version.id
    await db.commit()
    return MemoirBookShareCreateResponse(
        token=_stable_token(output.id, share.token_hash, chapter=chapter_id is not None),
        expires_at=share.expires_at,
    )


async def revoke_share(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None,
) -> None:
    output = await _owned_resource(db, user_id, project_id, chapter_id)
    share_model, share_key = _share_table(chapter_id is not None)
    await db.execute(delete(share_model).where(share_key == output.id))
    await db.commit()


async def readable_share_token(db: AsyncSession, project_id: str) -> str | None:
    """The author's live book link, if a reader could open it right now."""
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    share = await db.get(MemoirBookShare, output.id, populate_existing=True) if output else None
    if share is None or share.expires_at <= utcnow_naive():
        return None
    owner_id = await db.scalar(select(Project.user_id).where(Project.id == project_id))
    if owner_id is None:
        return None
    version, stale = await _current_version(db, user_id=owner_id, project_id=project_id)
    if stale or version is None or share.version_id != version.id:
        return None
    return _stable_token(share.output_id, share.token_hash)


async def read_share(db: AsyncSession, token: str) -> MemoirBookOutputResponse:
    invalid = HTTPException(status_code=404, detail="分享链接已失效")
    stable_match = STABLE_TOKEN_PATTERN.fullmatch(token)
    chapter_link = bool(stable_match and stable_match.group(1) == "c1")
    if stable_match:
        resource_id = stable_match.group(2)
        share_model, _ = _share_table(chapter_link)
        share = await db.get(share_model, resource_id, populate_existing=True)
        if share is None or not hmac.compare_digest(token, _stable_token(resource_id, share.token_hash, chapter=chapter_link)):
            raise invalid
    elif len(token) == 43:
        share = await db.scalar(select(MemoirBookShare).where(
            MemoirBookShare.token_hash == session_token_hash(token),
        ))
    else:
        raise invalid
    if share is None or share.expires_at <= utcnow_naive():
        raise invalid
    if isinstance(share, MemoirChapterShare):
        resource = await db.get(MemoirBookPlanChapter, share.chapter_id)
        version = await db.get(MemoirBookChapterDraftVersion, share.version_id)
        if resource is None or version is None or version.chapter_id != resource.id:
            raise invalid
    else:
        resource = await db.get(MemoirBookOutput, share.output_id)
        version = await db.get(MemoirBookOutputVersion, share.version_id)
        if resource is None or version is None or version.output_id != resource.id:
            raise invalid
    owner_id = await db.scalar(select(Project.user_id).where(Project.id == resource.project_id))
    if owner_id is None:
        raise invalid
    current_version, stale = await _current_version(
        db, user_id=owner_id, project_id=resource.project_id,
        chapter_id=resource.id if chapter_link else None,
    )
    if stale or current_version is None or share.version_id != current_version.id:
        raise HTTPException(status_code=409, detail="书稿正在更新，暂时无法阅读分享内容")
    if isinstance(resource, MemoirBookPlanChapter) and isinstance(version, MemoirBookChapterDraftVersion):
        # Adapt the saved chapter to the existing public reader without creating a book.
        output = MemoirBookOutput(
            id=resource.id, project_id=resource.project_id, plan_id=resource.plan_id,
            title=resource.title, status=MemoirOutputStatus.DRAFT.value,
            created_at=resource.created_at, updated_at=version.created_at,
        )
        snapshot = [chapter_snapshot(resource, version, chapter_index=1)]
        manuscript = manuscript_from_snapshot(title=resource.title, content=version.content, snapshot=snapshot)
        reading_version = MemoirBookOutputVersion(
            id=version.id, output_id=resource.id, project_id=resource.project_id, plan_id=resource.plan_id,
            version=version.version, content=version.content, created_by=version.created_by,
            created_at=version.created_at,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript.model_dump(mode="json") if manuscript else {},
        )
        return public_reading_response(output, reading_version)
    return public_reading_response(resource, version)


def public_reading_response(
    output: MemoirBookOutput,
    version: MemoirBookOutputVersion,
    chapter_ids: set[str] | None = None,
) -> MemoirBookOutputResponse:
    """A reader-only copy of a book version, optionally limited to some chapters."""
    reading_version = _output_version_response(version, fallback_title=output.title)
    manuscript = reading_version.manuscript
    if manuscript is not None:
        # The reader needs chapter text, not audio provenance or storage paths.
        chapters = [chapter.model_copy(update={
            "source_refs": [],
            "illustrations": [],
            "people_notes": [],
            "uncertain_facts": [],
            "pull_quotes": [],
            "intro": "",
            "time_hint": "",
            "place_hint": "",
            "sections": [section.model_copy(update={"source_ref_ids": []}) for section in chapter.sections],
        }) for chapter in manuscript.chapters if chapter_ids is None or chapter.chapter_id in chapter_ids]
        back_matter = manuscript.back_matter.model_copy(update={
            key: getattr(manuscript.back_matter, key).model_copy(update={"content": ""})
            for key in ("afterword", "interview_records", "version_note", "unconfirmed_facts", "source_appendix")
        })
        reading_version = reading_version.model_copy(update={
            **({} if chapter_ids is None else {
                "content": "\n\n".join(chapter.content for chapter in chapters),
            }),
            "manuscript": manuscript.model_copy(update={
                "profile": manuscript.profile.model_copy(update={
                    "narrator_name": "", "prepared_by": "", "interview_date_range": "",
                }),
                "chapters": chapters,
                "table_of_contents": [
                    item for item in manuscript.table_of_contents
                    if chapter_ids is None or item.chapter_id in chapter_ids
                ],
                "front_matter": [],
                "back_matter": back_matter,
                "image_appendix": [],
                "render_settings": manuscript.render_settings.model_copy(update={
                    "back_cover_quote": "", "cover_image": None,
                }),
            }),
        })
    elif chapter_ids is not None:
        # Without chapter structure the text cannot be limited, so nothing is shown.
        reading_version = reading_version.model_copy(update={"content": ""})
    return MemoirBookOutputResponse(
        id=output.id,
        project_id=output.project_id,
        plan_id=output.plan_id,
        title=book_output_version_title(version, fallback=output.title),
        status=MemoirOutputStatus(output.status),
        latest_version=reading_version,
        versions=[MemoirBookOutputVersionSummaryResponse(
            id=version.id,
            version=version.version,
            created_by=MemoirCreatedBy(version.created_by),
            created_at=version.created_at,
        )],
        created_at=output.created_at,
        updated_at=output.updated_at,
    )
