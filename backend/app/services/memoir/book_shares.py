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

from app.auth import session_token_hash
from app.clock import utcnow_naive
from app.config import get_settings
from app.domain.memoir.types import MemoirCreatedBy, MemoirOutputStatus
from app.models.memoir import MemoirBookOutput, MemoirBookOutputVersion, MemoirBookShare
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


SHARE_LIFETIME = timedelta(days=7)
STABLE_TOKEN_PATTERN = re.compile(r"v1\.([0-9a-f-]{36})\.([0-9a-f]{64})\Z")


def _stable_token(output_id: str, token_hash: str) -> str:
    # Legacy shares retain only a hash; bind a recoverable token to that hash
    # without making the stored hash usable as a public link.
    signature = hmac.new(
        get_settings().app_secret_key.encode("utf-8"),
        f"memoir-book-share:v1\0{output_id}\0{token_hash}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v1.{output_id}.{signature}"


async def _owned_output(db: AsyncSession, user_id: str, project_id: str) -> MemoirBookOutput:
    await load_memoir_project(db, project_id, user_id)
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    if output is None:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    return output


async def _current_version(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> tuple[MemoirBookOutputVersion | None, bool]:
    output = await get_book_output(db, user_id=user_id, project_id=project_id)
    version_id = output.latest_version.id if output.latest_version else None
    version = await db.get(MemoirBookOutputVersion, version_id) if version_id else None
    return version, output.is_stale


async def share_status(db: AsyncSession, *, user_id: str, project_id: str) -> MemoirBookShareStatusResponse:
    output = await _owned_output(db, user_id, project_id)
    share = await db.get(MemoirBookShare, output.id, populate_existing=True)
    active = share is not None and share.expires_at > utcnow_naive()
    version, stale = await _current_version(db, user_id=user_id, project_id=project_id)
    return MemoirBookShareStatusResponse(
        active=active,
        expires_at=share.expires_at if active else None,
        token=_stable_token(share.output_id, share.token_hash) if active else None,
        stale=stale,
        current=bool(active and not stale and version and share.version_id == version.id),
    )


async def create_share(db: AsyncSession, *, user_id: str, project_id: str) -> MemoirBookShareCreateResponse:
    output = await _owned_output(db, user_id, project_id)
    version, stale = await _current_version(db, user_id=user_id, project_id=project_id)
    if stale:
        raise HTTPException(status_code=409, detail="书稿正在更新，请完成更新后再分享")
    if version is None or not version.content.strip():
        raise HTTPException(status_code=409, detail="请先生成可阅读的书稿")
    token = secrets.token_urlsafe(32)
    token_hash = session_token_hash(token)
    now = utcnow_naive()
    expires_at = now + SHARE_LIFETIME
    statement = insert(MemoirBookShare).values(
        output_id=output.id,
        version_id=version.id,
        token_hash=token_hash,
        expires_at=expires_at,
        created_at=now,
    )
    await db.execute(statement.on_conflict_do_update(
        index_elements=[MemoirBookShare.output_id],
        set_={
            "version_id": version.id,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "created_at": now,
        },
    ))
    await db.commit()
    return MemoirBookShareCreateResponse(token=_stable_token(output.id, token_hash), expires_at=expires_at)


async def update_share(db: AsyncSession, *, user_id: str, project_id: str) -> MemoirBookShareCreateResponse:
    output = await _owned_output(db, user_id, project_id)
    share = await db.get(MemoirBookShare, output.id, populate_existing=True)
    if share is None or share.expires_at <= utcnow_naive():
        raise HTTPException(status_code=409, detail="阅读链接已失效，请重新生成")
    version, stale = await _current_version(db, user_id=user_id, project_id=project_id)
    if stale:
        raise HTTPException(status_code=409, detail="书稿正在更新，请完成更新后再分享")
    if version is None or not version.content.strip():
        raise HTTPException(status_code=409, detail="请先生成可阅读的书稿")
    share.version_id = version.id
    await db.commit()
    return MemoirBookShareCreateResponse(
        token=_stable_token(share.output_id, share.token_hash),
        expires_at=share.expires_at,
    )


async def revoke_share(db: AsyncSession, *, user_id: str, project_id: str) -> None:
    output = await _owned_output(db, user_id, project_id)
    await db.execute(delete(MemoirBookShare).where(MemoirBookShare.output_id == output.id))
    await db.commit()


async def read_share(db: AsyncSession, token: str) -> MemoirBookOutputResponse:
    invalid = HTTPException(status_code=404, detail="分享链接已失效")
    stable_match = STABLE_TOKEN_PATTERN.fullmatch(token)
    if stable_match:
        output_id = stable_match.group(1)
        share = await db.get(MemoirBookShare, output_id, populate_existing=True)
        if share is None or not hmac.compare_digest(token, _stable_token(share.output_id, share.token_hash)):
            raise invalid
    elif len(token) == 43:
        share = await db.scalar(select(MemoirBookShare).where(
            MemoirBookShare.token_hash == session_token_hash(token),
        ))
    else:
        raise invalid
    if share is None or share.expires_at <= utcnow_naive():
        raise invalid
    output = await db.get(MemoirBookOutput, share.output_id)
    version = await db.get(MemoirBookOutputVersion, share.version_id)
    if output is None or version is None or version.output_id != output.id:
        raise invalid
    owner_id = await db.scalar(select(Project.user_id).where(Project.id == output.project_id))
    if owner_id is None:
        raise invalid
    current_version, stale = await _current_version(db, user_id=owner_id, project_id=output.project_id)
    if stale or current_version is None or share.version_id != current_version.id:
        raise HTTPException(status_code=409, detail="书稿正在更新，暂时无法阅读分享内容")
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
        }) for chapter in manuscript.chapters]
        back_matter = manuscript.back_matter.model_copy(update={
            key: getattr(manuscript.back_matter, key).model_copy(update={"content": ""})
            for key in ("afterword", "interview_records", "version_note", "unconfirmed_facts", "source_appendix")
        })
        reading_version = reading_version.model_copy(update={
            "manuscript": manuscript.model_copy(update={
                "profile": manuscript.profile.model_copy(update={
                    "narrator_name": "", "prepared_by": "", "interview_date_range": "",
                }),
                "chapters": chapters,
                "front_matter": [],
                "back_matter": back_matter,
                "image_appendix": [],
                "render_settings": manuscript.render_settings.model_copy(update={"back_cover_quote": ""}),
            }),
        })
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
