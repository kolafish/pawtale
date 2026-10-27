from pathlib import Path

from sqlalchemy import func, select

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirSectionMaterialAssignedBy,
    MemoirSectionType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)


async def ensure_mobile_memoir_project(client, title: str = "测试回忆录") -> str:
    response = await client.get("/api/mobile/memoir/projects")
    assert response.status_code == 200
    active_project_id = response.json()["active_project_id"]
    if active_project_id:
        return active_project_id

    created = await client.post("/api/mobile/memoir/projects", json={"title": title})
    assert created.status_code == 201
    return created.json()["id"]


async def post_mobile_memoir_voice_session(
    client,
    json: dict,
    *,
    project_id: str | None = None,
):
    if project_id is None:
        project_id = await ensure_mobile_memoir_project(client)
    return await client.post(
        "/api/mobile/memoir/voice-sessions",
        params={"project_id": project_id},
        json=json,
    )


async def run_test_editing_job(test_db, job_id: str, *, advance_editorial_copy: bool = True) -> bool:
    from app.models.memoir import MemoirEditingJob
    from app.services.memoir.editing_jobs import claim_editing_job, run_editing_job

    job = await test_db.get(MemoirEditingJob, job_id)
    assert job is not None
    advance_copy = advance_editorial_copy and job.job_type == 'update_book_draft'
    lease = await claim_editing_job(test_db, job_id, now=job.run_after)
    assert lease is not None
    completed = await run_editing_job(test_db, lease)
    # Book/domain tests advance the copy dependency as the worker would. Ordering
    # regressions opt out and explicitly control each independent queue step.
    if not advance_copy:
        return completed
    job = await test_db.get(MemoirEditingJob, job_id, populate_existing=True)
    if (job.result_json or {}).get('stage') == 'writing_copy':
        copy_job = await test_db.scalar(select(MemoirEditingJob).where(
            MemoirEditingJob.dedupe_key == f'automatic-editorial-copy:{job.project_id}'))
        if copy_job and copy_job.status == 'pending':
            await run_test_editing_job(test_db, copy_job.id, advance_editorial_copy=False)
            return await run_test_editing_job(test_db, job_id, advance_editorial_copy=False)
    return completed


async def create_test_voice_transcript(
    client,
    test_db,
    text: str = "嗯，我小时候就是住在外婆家。",
    *,
    project_id: str | None = None,
    question_text: str | None = None,
    guidance_mode: str = "free_narration",
    current_guidance_module: str | None = None,
):
    user = (await client.get("/api/auth/me")).json()
    session_payload = {"guidance_mode": guidance_mode, "provider": "volc_streaming_asr"}
    if current_guidance_module:
        session_payload["current_guidance_module"] = current_guidance_module
    create_response = await post_mobile_memoir_voice_session(client, project_id=project_id, json=session_payload)
    assert create_response.status_code == 201
    session = create_response.json()["session"]

    audio_file = AudioFile(
        user_id=user["id"],
        storage_path=f"memoir/audio/{user['id']}/{len(text)}.pcm",
        mime_type="audio/pcm",
        size_bytes=3200,
        duration_seconds=8,
    )
    test_db.add(audio_file)

    question_audio_file = None
    question_message = None
    if question_text:
        question_audio_file = AudioFile(
            user_id=user["id"],
            storage_path=f"memoir/audio/{user['id']}/question.pcm",
            mime_type="audio/pcm",
            size_bytes=1600,
            duration_seconds=4,
        )
        test_db.add(question_audio_file)
        await test_db.flush()
        question_message = MemoirChatMessage(
            project_id=session["project_id"],
            voice_session_id=session["id"],
            role=MemoirChatMessageRole.ASSISTANT.value,
            content_text=question_text,
            content_status=MemoirChatContentStatus.FINAL.value,
            audio_file_id=question_audio_file.id,
            turn_index=0,
            guidance_intent="opening",
            metadata_json={"source": "test.question"},
        )
        test_db.add(question_message)

    version = MemoirTranscriptVersion(
        voice_session_id=session["id"],
        version_type=MemoirTranscriptVersionType.ASR_RAW.value,
        content=text,
        provider="volc_streaming_asr",
        created_by=MemoirCreatedBy.ASR.value,
    )
    test_db.add(version)
    await test_db.flush()

    message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text=text,
        content_status=MemoirChatContentStatus.FINAL.value,
        audio_file_id=audio_file.id,
        transcript_version_id=version.id,
        turn_index=1 if question_message else 0,
        metadata_json={"source": "test"},
    )
    test_db.add(message)
    await test_db.flush()

    utterance = MemoirVoiceUtterance(
        voice_session_id=session["id"],
        chat_message_id=message.id,
        audio_file_id=audio_file.id,
        transcript_raw=text,
        transcript_edited=text,
        start_ms=0,
        end_ms=8000,
        provider="volc_streaming_asr",
        provider_payload_json={"source": "test"},
    )
    test_db.add(utterance)
    await test_db.flush()
    version.source_artifact_id = utterance.id
    await test_db.commit()
    audio_relative_path = Path(audio_file.storage_path).relative_to(Path("memoir") / "audio")
    return {
        "user_id": user["id"],
        "session": session,
        "message_id": message.id,
        "utterance_id": utterance.id,
        "audio_file_id": audio_file.id,
        "audio_storage_path": audio_file.storage_path,
        "audio_relative_path": str(audio_relative_path),
        "transcript_version_id": version.id,
        "question_message_id": question_message.id if question_message else None,
        "question_audio_file_id": question_audio_file.id if question_audio_file else None,
    }


_USE_CREATED_TRANSCRIPT_VERSION = object()


def memoir_source_ref(
    created: dict,
    quote: str = "",
    *,
    transcript_version_id: str | None | object = _USE_CREATED_TRANSCRIPT_VERSION,
) -> dict:
    version_id = (
        created["transcript_version_id"]
        if transcript_version_id is _USE_CREATED_TRANSCRIPT_VERSION
        else transcript_version_id
    )
    return {
        "voice_session_id": created["session"]["id"],
        "message_id": created["message_id"],
        "utterance_id": created["utterance_id"],
        "transcript_version_id": version_id,
        "audio_file_id": created["audio_file_id"],
        "start_ms": 0,
        "end_ms": 8000,
        "quote": quote,
    }


def _string_ref_value(ref: dict, key: str) -> str | None:
    value = ref.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int_ref_value(ref: dict, key: str) -> int | None:
    value = ref.get(key)
    return value if isinstance(value, int) else None


async def add_test_section_materials(
    test_db,
    *,
    project_id: str,
    chapter_id: str,
    source_refs: list[dict],
) -> list[MemoirSectionMaterial]:
    section = await test_db.scalar(
        select(MemoirSection).where(
            MemoirSection.project_id == project_id,
            MemoirSection.chapter_id == chapter_id,
        )
    )
    if section is None:
        section = MemoirSection(
            project_id=project_id,
            chapter_id=chapter_id,
            title="本章",
            section_type=MemoirSectionType.LIFE_FRAGMENT.value,
            sort_order=0,
        )
        test_db.add(section)
        await test_db.flush()
    current_max_sort_order = await test_db.scalar(
        select(func.max(MemoirSectionMaterial.sort_order)).where(
            MemoirSectionMaterial.section_id == section.id
        )
    )
    next_sort_order = (
        int(current_max_sort_order) + 1
        if current_max_sort_order is not None
        else 0
    )
    materials: list[MemoirSectionMaterial] = []
    for ref in source_refs:
        utterance_id = _string_ref_value(ref, "utterance_id")
        if not utterance_id:
            continue
        material = MemoirSectionMaterial(
            project_id=project_id,
            section_id=section.id,
            utterance_id=utterance_id,
            transcript_version_id=_string_ref_value(ref, "transcript_version_id"),
            sort_order=next_sort_order + len(materials),
            assigned_by=MemoirSectionMaterialAssignedBy.SYSTEM.value,
            assignment_reason="sync_existing_source_ref",
        )
        test_db.add(material)
        materials.append(material)
    if materials:
        await test_db.flush()
    return materials


async def create_test_book_plan_with_chapters(
    test_db,
    *,
    project_id: str,
    title: str = "外婆回忆录章节规划",
    status: MemoirBookPlanStatus = MemoirBookPlanStatus.CONFIRMED,
    chapter_titles: list[str] | None = None,
    source_refs_by_chapter: list[list[dict]] | None = None,
    draft_contents: list[str | None] | None = None,
    draft_statuses: list[MemoirBookChapterDraftStatus] | None = None,
) -> tuple[MemoirBookPlan, list[MemoirBookPlanChapter]]:
    plan = MemoirBookPlan(
        project_id=project_id,
        title=title,
        status=status.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()

    chapters: list[MemoirBookPlanChapter] = []
    for index, chapter_title in enumerate(chapter_titles or ["外婆家的童年", "第一次离家"]):
        chapter = MemoirBookPlanChapter(
            plan_id=plan.id,
            project_id=project_id,
            title=chapter_title,
            sort_order=index,
        )
        test_db.add(chapter)
        chapters.append(chapter)
    await test_db.flush()

    for chapter in chapters:
        test_db.add(
            MemoirSection(
                project_id=project_id,
                chapter_id=chapter.id,
                title=chapter.title,
                section_type=MemoirSectionType.LIFE_FRAGMENT.value,
                sort_order=0,
            )
        )
    await test_db.flush()

    for chapter, refs in zip(chapters, source_refs_by_chapter or [], strict=False):
        await add_test_section_materials(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            source_refs=refs,
        )
    await test_db.commit()

    for index, content in enumerate(draft_contents or []):
        if not content:
            continue
        status = (
            draft_statuses[index]
            if draft_statuses and index < len(draft_statuses)
            else MemoirBookChapterDraftStatus.CONFIRMED
        )
        await create_test_chapter_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapters[index].id,
            status=status,
            content=content,
        )
    return plan, chapters


async def create_test_book_plan(
    test_db,
    *,
    project_id: str,
    source_ref: dict | None = None,
    source_refs: list[dict] | None = None,
    status: MemoirBookPlanStatus = MemoirBookPlanStatus.CONFIRMED,
    title: str = "外婆回忆录",
    chapter_title: str = "外婆家的童年",
):
    refs = source_refs if source_refs is not None else ([source_ref] if source_ref else [])
    plan, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        title=title,
        status=status,
        chapter_titles=[chapter_title],
        source_refs_by_chapter=[refs],
    )
    return plan, chapters[0]


async def create_test_chapter_draft(
    test_db,
    *,
    project_id: str,
    chapter_id: str,
    status: MemoirBookChapterDraftStatus = MemoirBookChapterDraftStatus.CONFIRMED,
    content: str = "已确认章节稿",
) -> MemoirBookChapterDraft:
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter_id,
        status=status.value,
    )
    test_db.add(draft)
    await test_db.flush()
    material_rows = list(
        (
            await test_db.execute(
                select(
                    MemoirSectionMaterial.utterance_id,
                    MemoirSectionMaterial.transcript_version_id,
                )
                .join(
                    MemoirSection,
                    MemoirSection.id
                    == MemoirSectionMaterial.section_id,
                )
                .where(MemoirSection.chapter_id == chapter_id)
                .order_by(MemoirSectionMaterial.sort_order)
            )
        ).all()
    )
    section = await test_db.scalar(
        select(MemoirSection)
        .where(
            MemoirSection.project_id == project_id,
            MemoirSection.chapter_id == chapter_id,
        )
        .order_by(MemoirSection.sort_order)
    )
    assert section is not None
    used_material_ids = [row.utterance_id for row in material_rows]
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=1,
        content=content,
        created_by=MemoirCreatedBy.USER.value,
        prompt_snapshot="",
        used_material_ids_json=used_material_ids,
        material_snapshot_json=[
            {
                "material_id": row.utterance_id,
                "transcript_version_id": row.transcript_version_id,
            }
            for row in material_rows
        ],
        section_snapshot_json=[
            {
                "section_id": section.id,
                "title": section.title,
                "section_type": section.section_type,
                "section_version": section.version,
                "sort_order": section.sort_order,
                "content": content,
                "used_material_ids": used_material_ids,
            }
        ],
    )
    test_db.add(version)
    await test_db.flush()
    await test_db.commit()
    return draft


async def save_test_body_summaries(db, project_id):
    """Seed completed AI bodies, with per-point provenance, for structure writer tests."""
    from app.services.memoir.story_tree_growth import _load_tree
    from app.schemas.memoir_section_digests import MemoirBodyDigest, MemoirBodyDigestPoint
    from app.services.memoir.section_digests import digest_snapshot
    from app.services.memoir.transcript_projection import current_material_text
    _, chapters, sections = await _load_tree(db, project_id=project_id)
    for chapter in chapters:
        snapshots = []
        for section in sections:
            if section.chapter_id != chapter.id:
                continue
            points = [MemoirBodyDigestPoint(text=current_material_text(m), material_ids=[m.utterance_id])
                for m in section.materials if m.include_in_book and current_material_text(m)]
            body = "\n".join(p.text for p in points) or "待补充"
            snapshots.append({"section_id": section.id, "title": section.title,
                "section_version": section.version, "section_type": section.section_type,
                "sort_order": section.sort_order, "content": body,
                "used_material_ids": [mid for p in points for mid in p.material_ids],
                **digest_snapshot(body, MemoirBodyDigest(summary=body, points=points))})
        draft = await db.scalar(select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == chapter.id))
        if draft is None:
            draft = MemoirBookChapterDraft(project_id=project_id, chapter_id=chapter.id, status="draft")
            db.add(draft)
            await db.flush()
        versions = list((await db.scalars(select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.draft_id == draft.id))).all())
        db.add(MemoirBookChapterDraftVersion(draft_id=draft.id, project_id=project_id, chapter_id=chapter.id,
            version=max((v.version for v in versions), default=0) + 1,
            content="\n\n".join(s["content"] for s in snapshots), created_by="ai",
            section_snapshot_json=snapshots, used_material_ids_json=[mid for s in snapshots for mid in s["used_material_ids"]]))
    await db.commit()
