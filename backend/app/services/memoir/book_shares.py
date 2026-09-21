from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import session_token_hash
from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirCreatedBy, MemoirOutputStatus
from app.models.memoir import MemoirBookOutput, MemoirBookOutputVersion, MemoirBookShare
from app.schemas.memoir_book import (
    MemoirBookOutputResponse,
    MemoirBookOutputVersionSummaryResponse,
    MemoirBookShareCreateResponse,
    MemoirBookShareStatusResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_outputs import _output_version_response
from app.services.memoir.book_output_versions import book_output_version_title


SHARE_LIFETIME = timedelta(days=7)


async def _owned_output(db: AsyncSession, user_id: str, project_id: str) -> MemoirBookOutput:
    await load_memoir_project(db, project_id, user_id)
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    if output is None:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    return output


async def share_status(db: AsyncSession, *, user_id: str, project_id: str) -> MemoirBookShareStatusResponse:
    output = await _owned_output(db, user_id, project_id)
    share = await db.get(MemoirBookShare, output.id, populate_existing=True)
    active = share is not None and share.expires_at > utcnow_naive()
    return MemoirBookShareStatusResponse(active=active, expires_at=share.expires_at if active else None)


async def create_share(db: AsyncSession, *, user_id: str, project_id: str) -> MemoirBookShareCreateResponse:
    output = await _owned_output(db, user_id, project_id)
    version = await db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.output_id == output.id)
        .order_by(MemoirBookOutputVersion.version.desc())
        .limit(1)
    )
    if version is None or not version.content.strip():
        raise HTTPException(status_code=409, detail="请先生成可阅读的书稿")
    token = secrets.token_urlsafe(32)
    now = utcnow_naive()
    expires_at = now + SHARE_LIFETIME
    statement = insert(MemoirBookShare).values(
        output_id=output.id,
        version_id=version.id,
        token_hash=session_token_hash(token),
        expires_at=expires_at,
        created_at=now,
    )
    await db.execute(statement.on_conflict_do_update(
        index_elements=[MemoirBookShare.output_id],
        set_={
            "version_id": version.id,
            "token_hash": session_token_hash(token),
            "expires_at": expires_at,
            "created_at": now,
        },
    ))
    await db.commit()
    return MemoirBookShareCreateResponse(token=token, expires_at=expires_at)


async def revoke_share(db: AsyncSession, *, user_id: str, project_id: str) -> None:
    output = await _owned_output(db, user_id, project_id)
    await db.execute(delete(MemoirBookShare).where(MemoirBookShare.output_id == output.id))
    await db.commit()


async def read_share(db: AsyncSession, token: str) -> MemoirBookOutputResponse:
    invalid = HTTPException(status_code=404, detail="分享链接已失效")
    if len(token) != 43:
        raise invalid
    share = await db.scalar(select(MemoirBookShare).where(
        MemoirBookShare.token_hash == session_token_hash(token),
        MemoirBookShare.expires_at > utcnow_naive(),
    ))
    if share is None:
        raise invalid
    output = await db.get(MemoirBookOutput, share.output_id)
    version = await db.get(MemoirBookOutputVersion, share.version_id)
    if output is None or version is None or version.output_id != output.id:
        raise invalid
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
