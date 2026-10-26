from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
from typing import Literal

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
    AudioFile, MemoirBookOutput, MemoirBookOutputVersion, MemoirBookShare,
    MemoirBookPlan, MemoirBookPlanChapter, MemoirBookChapterDraftVersion, MemoirChapterShare, MemoirSection, MemoirSectionMaterial, MemoirSectionShare, MemoirImageAsset,
)
from app.models.projects import Project
from app.schemas.memoir_book import (
    MemoirBookOutputResponse, MemoirBookManuscriptIllustration,
    MemoirBookOutputVersionSummaryResponse,
    MemoirBookShareCreateResponse,
    MemoirBookShareStatusResponse, MemoirAudioPlanRequest, MemoirSharedAudioPlanResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_output_versions import book_output_version_title, load_latest_book_output_version
from app.services.memoir.book_outputs import _output_version_response
from app.services.memoir.book_output_snapshots import chapter_snapshot, normalized_section_snapshots
from app.services.memoir.book_output_manuscript import manuscript_from_snapshot
from app.services.memoir.image_assets import manuscript_image_snapshots, image_asset_file_path
from app.services.memoir.book_source_links import chapter_source_change_states_by_chapter
from app.services.memoir.share_likes import ShareLikeTarget, book_target, chapter_target, section_target, like_summary


STABLE_TOKEN_PATTERN = re.compile(r"(v1|c1|s1)\.([0-9a-f-]{36})\.([0-9a-f]{64})\Z")
ShareScope = Literal["book", "chapter", "story"]
ShareResource = MemoirBookOutput | MemoirBookPlanChapter | MemoirSection
ShareVersion = MemoirBookOutputVersion | MemoirBookChapterDraftVersion


def _stable_token(output_id: str, token_hash: str, *, scope: ShareScope = "book") -> str:
    # Legacy shares retain only a hash; bind a recoverable token to that hash
    # without making the stored hash usable as a public link.
    namespace = f"memoir-{scope}-share:v1"
    signature = hmac.new(
        get_settings().app_secret_key.encode("utf-8"),
        f"{namespace}\0{output_id}\0{token_hash}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{dict(book='v1', chapter='c1', story='s1')[scope]}.{output_id}.{signature}"


def share_scope(resource: ShareResource) -> ShareScope:
    if isinstance(resource, MemoirSection):
        return "story"
    return "chapter" if isinstance(resource, MemoirBookPlanChapter) else "book"


def _share_table(scope: ShareScope) -> tuple[
    type[MemoirBookShare] | type[MemoirChapterShare] | type[MemoirSectionShare], InstrumentedAttribute[str],
]:
    if scope == "story":
        return MemoirSectionShare, MemoirSectionShare.section_id
    return (MemoirChapterShare, MemoirChapterShare.chapter_id) if scope == "chapter" else (MemoirBookShare, MemoirBookShare.output_id)


async def _owned_resource(
    db: AsyncSession, user_id: str, project_id: str, chapter_id: str | None, section_id: str | None = None,
) -> ShareResource:
    await load_memoir_project(db, project_id, user_id)
    if section_id is not None:
        resource = await db.scalar(select(MemoirSection).where(
            MemoirSection.project_id == project_id, MemoirSection.id == section_id, MemoirSection.status == "active",
            *(() if chapter_id is None else (MemoirSection.chapter_id == chapter_id,)),
        ))
    elif chapter_id is not None:
        resource = await db.scalar(select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id, MemoirBookPlanChapter.id == chapter_id,
        ))
    else:
        resource = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    if resource is None:
        raise HTTPException(status_code=404, detail="章节不存在" if chapter_id is not None else "全书稿不存在")
    return resource


async def _current_version(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None, section_id: str | None = None,
) -> tuple[ShareVersion | None, bool]:
    """Return the last complete saved text; additions may be assembling separately."""
    if section_id is not None:
        section = await db.get(MemoirSection, section_id)
        if section is None or section.project_id != project_id or section.status != "active":
            return None, True
        version = await db.scalar(select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.project_id == project_id,
            MemoirBookChapterDraftVersion.chapter_id == section.chapter_id,
        ).order_by(MemoirBookChapterDraftVersion.version.desc()).limit(1))
        saved = next((item for item in normalized_section_snapshots(version.section_snapshot_json if version else [])
                      if item["section_id"] == section_id), None)
        if saved is None:
            return None, False
        included_sources = set(await db.scalars(select(MemoirSectionMaterial.utterance_id).where(
            MemoirSectionMaterial.section_id == section_id, MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.include_in_book.is_(True),
        )))
        unsafe = not section.include_in_book or bool(set(saved["used_material_ids"]) - included_sources)
        return version, unsafe
    if chapter_id is not None:
        version = await db.scalar(select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.project_id == project_id,
            MemoirBookChapterDraftVersion.chapter_id == chapter_id,
        ).order_by(MemoirBookChapterDraftVersion.version.desc()).limit(1))
    else:
        output = await _owned_resource(db, user_id, project_id, None)
        version = await load_latest_book_output_version(db, output_id=output.id)
    if version is None:
        return None, False
    if isinstance(version, MemoirBookChapterDraftVersion):
        versions = {version.chapter_id: version}
    else:
        snapshots = version.chapter_snapshot_json or []
        version_ids = [item.get("draft_version_id") for item in snapshots if isinstance(item, dict)]
        drafts = list(await db.scalars(select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.id.in_(version_ids),
            MemoirBookChapterDraftVersion.project_id == project_id,
        )))
        versions = {draft.chapter_id: draft for draft in drafts}
        expected_chapters = {item.get("chapter_id") for item in snapshots if isinstance(item, dict)}
        included = set(await db.scalars(select(MemoirBookPlanChapter.id).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.include_in_book.is_(True),
        )))
        if expected_chapters - included or len(drafts) != len(version_ids):
            return version, True
    # Removal is different from an addition: never keep showing withdrawn material
    # while a replacement is being generated. Compare the actual saved versions.
    plan_id = version.plan_id if isinstance(version, MemoirBookOutputVersion) else await db.scalar(
        select(MemoirBookPlanChapter.plan_id).where(MemoirBookPlanChapter.id == version.chapter_id)
    )
    plan = await db.get(MemoirBookPlan, plan_id) if plan_id else None
    changes = await chapter_source_change_states_by_chapter(db, plan, latest_versions=versions)
    current_sections = {section.id: section.chapter_id for section in await db.scalars(select(MemoirSection).where(
        MemoirSection.project_id == project_id, MemoirSection.status == "active", MemoirSection.include_in_book.is_(True),
    ))}
    for saved in versions.values():
        if changes.get(saved.chapter_id) and changes[saved.chapter_id].removed_source_count:
            return version, True
        if any(current_sections.get(item.get("section_id")) != saved.chapter_id
               for item in saved.section_snapshot_json or [] if isinstance(item, dict)):
            return version, True
    return version, False


def _active(share: MemoirBookShare | MemoirChapterShare | MemoirSectionShare | None) -> bool:
    return share is not None and (share.expires_at is None or share.expires_at > utcnow_naive())


async def active_share_key(db: AsyncSession, scope: ShareScope, resource_id: str) -> str | None:
    """Publication identity only; callers needing readable content must validate it separately."""
    model, _ = _share_table(scope)
    share = await db.get(model, resource_id, populate_existing=True)
    return share.token_hash if _active(share) else None


async def validate_share_key(db: AsyncSession, scope: ShareScope, resource_id: str, key: str) -> None:
    if await active_share_key(db, scope, resource_id) != key:
        raise HTTPException(status_code=404, detail="阅读分享已停止")
    await readable_share(db, _stable_token(resource_id, key, scope=scope))


def reading_share_title(resource: ShareResource, version: ShareVersion) -> str:
    if isinstance(resource, MemoirSection):
        section = next((item for item in normalized_section_snapshots(version.section_snapshot_json)
                        if item["section_id"] == resource.id), None)
        if section is None:
            raise HTTPException(404, "这个故事还没有已保存正文")
        return section["title"]
    if isinstance(version, MemoirBookOutputVersion):
        return book_output_version_title(version, fallback=resource.title)
    return resource.title


async def share_status(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None, section_id: str | None = None,
) -> MemoirBookShareStatusResponse:
    output = await _owned_resource(db, user_id, project_id, chapter_id, section_id)
    share_model, share_key = _share_table(share_scope(output))
    share = await db.get(share_model, output.id, populate_existing=True)
    active = _active(share)
    version, stale = await _current_version(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id, section_id=section_id)
    return MemoirBookShareStatusResponse(
        active=active,
        expires_at=share.expires_at if active else None,
        token=_stable_token(output.id, share.token_hash, scope=share_scope(output)) if active else None,
        stale=stale,
        current=bool(active and not stale and version),
        likes=await like_summary(db, _like_target(output), limit=None),
    )


async def create_share(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None, section_id: str | None = None, rotate: bool = True,
) -> MemoirBookShareCreateResponse:
    output = await _owned_resource(db, user_id, project_id, chapter_id, section_id)
    # Select the saved version only after serializing with author saves. A story
    # may move chapters, so use the existing project -> plan -> chapters order.
    if isinstance(output, MemoirSection):
        from app.services.memoir.sections import lock_project_chapters
        await lock_project_chapters(db, project_id=project_id)
    await db.execute(select(type(output)).where(type(output).id == output.id).with_for_update())
    output = await _owned_resource(db, user_id, project_id, chapter_id, section_id)
    await db.refresh(output)
    share_model, share_key = _share_table(share_scope(output))
    version, stale = await _current_version(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id, section_id=section_id)
    if stale:
        raise HTTPException(status_code=409, detail="书稿正在更新，请完成更新后再分享")
    if version is None or not version.content.strip():
        raise HTTPException(status_code=409, detail="请先生成可阅读的书稿")
    existing = await db.get(share_model, output.id, populate_existing=True)
    if not rotate and _active(existing):
        result = MemoirBookShareCreateResponse(
            token=_stable_token(output.id, existing.token_hash, scope=share_scope(output)),
            expires_at=existing.expires_at,
        )
        await db.commit()
        return result
    token = secrets.token_urlsafe(32)
    token_hash = session_token_hash(token)
    now = utcnow_naive()
    expires_at = None
    values = {"token_hash": token_hash, "expires_at": expires_at, "created_at": now}
    # A section can move to a different chapter whose saved versions have a
    # separate lifetime. Its authorization belongs to the section alone.
    if not isinstance(output, MemoirSection):
        values["version_id"] = version.id
    statement = insert(share_model).values(**{share_key.key: output.id}, **values)
    await db.execute(statement.on_conflict_do_update(index_elements=[share_key], set_=values))
    await db.commit()
    return MemoirBookShareCreateResponse(token=_stable_token(output.id, token_hash, scope=share_scope(output)), expires_at=expires_at)


async def update_share(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None, section_id: str | None = None,
) -> MemoirBookShareCreateResponse:
    # Keep the old PUT contract while making repeated share actions idempotent.
    return await create_share(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id, section_id=section_id, rotate=False)


async def revoke_share(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None, section_id: str | None = None,
) -> None:
    output = await _owned_resource(db, user_id, project_id, chapter_id, section_id)
    share_model, share_key = _share_table(share_scope(output))
    await db.execute(select(type(output)).where(type(output).id == output.id).with_for_update())
    await db.execute(delete(share_model).where(share_key == output.id))
    await db.commit()


async def readable_share(db: AsyncSession, token: str) -> tuple[ShareResource, ShareVersion]:
    """The book or chapter behind a reading link, while a reader can open it."""
    invalid = HTTPException(status_code=404, detail="分享链接已失效")
    stable_match = STABLE_TOKEN_PATTERN.fullmatch(token)
    scope: ShareScope = {"v1": "book", "c1": "chapter", "s1": "story"}[stable_match.group(1)] if stable_match else "book"
    if stable_match:
        resource_id = stable_match.group(2)
        share_model, _ = _share_table(scope)
        share = await db.get(share_model, resource_id, populate_existing=True)
        if share is None or not hmac.compare_digest(token, _stable_token(resource_id, share.token_hash, scope=scope)):
            raise invalid
    elif len(token) == 43:
        share = await db.scalar(select(MemoirBookShare).where(
            MemoirBookShare.token_hash == session_token_hash(token),
        ))
    else:
        raise invalid
    if not _active(share):
        raise invalid
    if isinstance(share, MemoirSectionShare):
        resource = await db.get(MemoirSection, share.section_id)
        if resource is None or resource.status != "active":
            raise invalid
    elif isinstance(share, MemoirChapterShare):
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
        chapter_id=resource.id if scope == "chapter" else None, section_id=resource.id if scope == "story" else None,
    )
    if stale or current_version is None:
        raise HTTPException(status_code=409, detail="作者已移除部分内容，正在更新分享书稿")
    return resource, current_version


def _like_target(resource: ShareResource) -> ShareLikeTarget:
    if isinstance(resource, MemoirSection):
        return section_target(resource)
    return chapter_target(resource) if isinstance(resource, MemoirBookPlanChapter) else book_target(resource)


async def reading_like_target(db: AsyncSession, token: str) -> ShareLikeTarget:
    """Readers like what the link shows: the whole book or one chapter."""
    resource, _ = await readable_share(db, token)
    return _like_target(resource)


async def read_share(db: AsyncSession, token: str) -> MemoirBookOutputResponse:
    resource, version = await readable_share(db, token)
    return await _resource_reading_response(db, resource, version, token=token)


async def shared_card(db: AsyncSession, token: str, *, version_id: str = "", excerpt_start: int = 0, excerpt_length: int = 0) -> bytes:
    from app.services.memoir.reading_share_card import render_reading_share_card

    # Use the same revocation and withdrawn-material checks as the reader, even
    # when rendering an already cached card. Never read private source material.
    resource, version = await readable_share(db, token)
    result = await _resource_reading_response(db, resource, version, presentation=False)
    is_book = share_scope(resource) == "book"
    book_title = result.title
    if not is_book:
        output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == resource.project_id))
        saved = await load_latest_book_output_version(db, output_id=output.id) if output else None
        project = await db.get(Project, resource.project_id)
        book_title = book_output_version_title(saved, fallback=output.title) if saved else (output.title if output else project.title)
    reading = result.latest_version
    # Selection offsets refer to this exact scoped reading version. An update
    # must never replace confirmed words with newly added, unconfirmed content.
    excerpt = ""
    if not is_book and version_id == reading.id and excerpt_length:
        excerpt = reading.content[excerpt_start:excerpt_start + excerpt_length].strip()
    chapters = reading.manuscript.chapters if reading.manuscript else []
    return await asyncio.to_thread(render_reading_share_card,
        title=result.title, book_title=book_title, is_book=is_book,
        chapter_count=len(chapters), char_count=len(reading.content.strip()), excerpt=excerpt)


async def _resource_reading_response(
    db: AsyncSession, resource: ShareResource, version: ShareVersion, *, token: str = "", presentation: bool = True,
) -> MemoirBookOutputResponse:
    scope = share_scope(resource)
    if isinstance(version, MemoirBookChapterDraftVersion):
        chapter = await db.get(MemoirBookPlanChapter, resource.chapter_id) if isinstance(resource, MemoirSection) else resource
        snapshot = chapter_snapshot(chapter, version, chapter_index=1)
        content = version.content
        title = resource.title
        if isinstance(resource, MemoirSection):
            sections = [item for item in snapshot["sections"] if item["section_id"] == resource.id]
            if not sections:
                raise HTTPException(404, "这个故事还没有已保存正文")
            title, content = sections[0]["title"], sections[0]["content"]
            snapshot.update(chapter_title=title, content=content, sections=sections, section_count=1,
                            source_refs=[], source_count=0, metadata_json={})
        output = MemoirBookOutput(
            id=resource.id, project_id=resource.project_id, plan_id=chapter.plan_id,
            title=title, status=MemoirOutputStatus.DRAFT.value,
            created_at=resource.created_at, updated_at=version.created_at,
        )
        manuscript = manuscript_from_snapshot(title=title, content=content, snapshot=[snapshot])
        reading_version = MemoirBookOutputVersion(
            id=version.id, output_id=resource.id, project_id=resource.project_id, plan_id=chapter.plan_id,
            version=version.version, content=content, created_by=version.created_by,
            created_at=version.created_at,
            chapter_snapshot_json=[snapshot],
            manuscript_json=manuscript.model_dump(mode="json") if manuscript else {},
        )
        result = public_reading_response(output, reading_version)
    else:
        result = public_reading_response(resource, version, include_book_details=True)
    return await _share_presentation(db, result, token=token, scope=scope) if presentation else result


def public_reading_response(
    output: MemoirBookOutput,
    version: MemoirBookOutputVersion,
    chapter_ids: set[str] | None = None,
    *, include_book_details: bool = False,
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
        if chapter_ids is not None:
            chapters = [chapter.model_copy(update={"index": index}) for index, chapter in enumerate(chapters, 1)]
        public_indices = {chapter.chapter_id: chapter.index for chapter in chapters}
        back_matter = manuscript.back_matter.model_copy(update={
            key: getattr(manuscript.back_matter, key).model_copy(update={"content": ""})
            for key in ("afterword", "interview_records", "version_note", "unconfirmed_facts", "source_appendix")
            if key != "afterword" or not include_book_details or not manuscript.back_matter.afterword.enabled
        })
        reading_version = reading_version.model_copy(update={
            **({} if chapter_ids is None else {
                "content": "\n\n".join(chapter.content for chapter in chapters),
            }),
            "manuscript": manuscript.model_copy(update={
                "profile": manuscript.profile.model_copy(update={
                    "narrator_name": manuscript.profile.narrator_name if include_book_details else "",
                    "prepared_by": manuscript.profile.prepared_by if include_book_details else "", "interview_date_range": "",
                }),
                "chapters": chapters,
                "table_of_contents": [
                    item.model_copy(update={"index": public_indices[item.chapter_id]}) for item in manuscript.table_of_contents
                    if item.chapter_id in public_indices
                ],
                "front_matter": [item for item in manuscript.front_matter if item.enabled and item.kind == "preface"] if include_book_details else [],
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


async def preview_share(db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str | None = None, section_id: str | None = None) -> MemoirBookOutputResponse:
    resource = await _owned_resource(db, user_id, project_id, chapter_id, section_id)
    version, unsafe = await _current_version(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id, section_id=section_id)
    if version is None or unsafe:
        raise HTTPException(status_code=409, detail="请完成书稿更新后预览分享内容")
    return await _resource_reading_response(db, resource, version)


async def _share_presentation(db: AsyncSession, result: MemoirBookOutputResponse, *, token: str, scope: ShareScope) -> MemoirBookOutputResponse:
    result.share_scope = scope
    manuscript = result.latest_version.manuscript if result.latest_version else None
    if manuscript is None:
        return result
    chapter_images, _, cover = await manuscript_image_snapshots(db, project_id=result.project_id)

    def public_image(raw: dict[str, object]) -> MemoirBookManuscriptIllustration:
        item = MemoirBookManuscriptIllustration.model_validate(raw)
        base = f"/api/mobile/memoir/book-shares/{token}/images" if token else f"/api/mobile/memoir/projects/{result.project_id}/image-assets"
        return item.model_copy(update={
            "storage_path": "", "source_quote": "", "source_utterance_id": None, "source_utterance_ids": [],
            "image_url": f"{base}/{item.image_asset_id}" + ("" if token else "/file"),
        })

    for item in manuscript.chapters:
        section_ids = {section.section_id for section in item.sections}
        item.illustrations = [public_image(raw) for raw in chapter_images.get(item.chapter_id, [])
                             if (raw.get("section_id") in section_ids if scope == "story" else not raw.get("section_id") or raw["section_id"] in section_ids)]
    if cover and scope == "book":
        manuscript.render_settings.cover_image = public_image(cover)
    if scope != "book":
        saved_manuscript = await db.scalar(
            select(MemoirBookOutputVersion.manuscript_json)
            .join(MemoirBookOutput, MemoirBookOutput.id == MemoirBookOutputVersion.output_id)
            .where(MemoirBookOutput.project_id == result.project_id)
            .order_by(MemoirBookOutputVersion.version.desc())
            .limit(1)
        )
        saved_profile = saved_manuscript.get("profile") if isinstance(saved_manuscript, dict) else None
        saved_name = saved_profile.get("narrator_name") if isinstance(saved_profile, dict) else ""
        manuscript.profile.narrator_name = saved_name if isinstance(saved_name, str) else ""
    return result


async def shared_image_file(db: AsyncSession, token: str, asset_id: str):
    result = await read_share(db, token)
    manuscript = result.latest_version.manuscript if result.latest_version else None
    images = [image for chapter in manuscript.chapters for image in chapter.illustrations] if manuscript else []
    if manuscript and manuscript.render_settings.cover_image:
        images.append(manuscript.render_settings.cover_image)
    if asset_id not in {image.image_asset_id for image in images}:
        raise HTTPException(status_code=404, detail="这张图片不在分享内容中")
    asset = await db.get(MemoirImageAsset, asset_id)
    if asset is None or asset.project_id != result.project_id or asset.status != "ready":
        raise HTTPException(status_code=404, detail="图片已移除")
    return asset, await asyncio.to_thread(image_asset_file_path, asset, branded=True)


async def _resource_audio_plan(
    db: AsyncSession, resource: ShareResource, version: ShareVersion, *, generate: bool,
    chapter_id: str | None = None, req: MemoirAudioPlanRequest | None = None,
) -> MemoirSharedAudioPlanResponse:
    owner_id = await db.scalar(select(Project.user_id).where(Project.id == resource.project_id))
    response = await _resource_reading_response(db, resource, version, presentation=False)
    text = response.latest_version.content
    if chapter_id:
        chapters = response.latest_version.manuscript.chapters if response.latest_version.manuscript else []
        chapter = next((item for item in chapters if item.chapter_id == chapter_id), None)
        if share_scope(resource) != "book" or chapter is None:
            raise HTTPException(404, "分享中没有这个章节")
        text = chapter.content
    from app.services.memoir.book_audio_jobs import AudioSource, prepare_audio_plan

    plan = await prepare_audio_plan(
        db, user_id=owner_id, project_id=resource.project_id,
        source=AudioSource(version.id, text), req=req or MemoirAudioPlanRequest(),
        generate=generate, commit=generate,
    )
    return MemoirSharedAudioPlanResponse(**plan.model_dump(), content=text)


async def shared_audio_plan(
    db: AsyncSession, token: str, version_id: str, chapter_id: str | None = None,
    *, generate: bool | None = None, req: MemoirAudioPlanRequest | None = None,
) -> MemoirSharedAudioPlanResponse:
    resource, version = await readable_share(db, token)
    if version.id != version_id:
        raise HTTPException(409, "故事已更新，请重新打开后继续朗读")
    plan = await _resource_audio_plan(db, resource, version, generate=generate is True, chapter_id=chapter_id, req=req)
    for segment in plan.segments:
        segment.job_id = None
        # Older clients only poll a prepared playlist and cannot request generation.
        if generate is None and segment.status == "idle":
            segment.status, segment.error_message = "failed", "请更新小程序后再发起朗读"
        elif segment.status == "failed":
            segment.error_message = "这段朗读暂时不可用，可以先阅读正文"
    return plan


async def shared_audio_file(db: AsyncSession, token: str, version_id: str, audio_id: str, chapter_id: str | None = None):
    from app.services.memoir.audio_files import audio_file_path

    plan = await shared_audio_plan(db, token, version_id, chapter_id)
    if audio_id not in {segment.audio_file_id for segment in plan.segments if segment.audio_file_id}:
        raise HTTPException(404, "朗读音频不存在")
    audio = await db.get(AudioFile, audio_id)
    return audio, audio_file_path(audio)
