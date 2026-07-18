from sqlalchemy import select

from app.domain.memoir.types import MemoirSectionStatus
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.services.memoir.book_source_links import (
    chapter_source_change_state,
    included_chapters_by_utterance,
    plan_source_ref_dicts_by_chapter,
)
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_voice_transcript,
    memoir_source_ref,
)


async def test_book_material_refs_are_derived_from_section_membership(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候住在外婆家。",
    )
    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=created["session"]["project_id"],
        source_ref=memoir_source_ref(created),
    )

    refs = await plan_source_ref_dicts_by_chapter(test_db, plan)
    included = await included_chapters_by_utterance(test_db, plan)

    assert refs[chapter.id][0]["utterance_id"] == created["utterance_id"]
    assert refs[chapter.id][0]["quote"] == "我小时候住在外婆家。"
    assert included[created["utterance_id"]][0].chapter_id == chapter.id


async def test_book_material_refs_exclude_hidden_or_archived_membership(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="这段素材只在启用时进入正文。",
    )
    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=created["session"]["project_id"],
        source_ref=memoir_source_ref(created),
    )
    material = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.utterance_id == created["utterance_id"]
        )
    )
    assert material is not None
    section = await test_db.get(MemoirSection, material.section_id)
    assert section is not None

    material.include_in_book = False
    await test_db.commit()
    assert (await plan_source_ref_dicts_by_chapter(test_db, plan))[chapter.id] == []

    material.include_in_book = True
    section.include_in_book = False
    await test_db.commit()
    assert (await plan_source_ref_dicts_by_chapter(test_db, plan))[chapter.id] == []

    section.include_in_book = True
    section.status = MemoirSectionStatus.ARCHIVED.value
    await test_db.commit()
    assert (await plan_source_ref_dicts_by_chapter(test_db, plan))[chapter.id] == []


async def test_one_material_move_has_only_one_chapter_owner(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    retained = await create_test_voice_transcript(
        client,
        test_db,
        project_id=created["session"]["project_id"],
        text="这一段仍留在原章节。",
    )
    plan, first = await create_test_book_plan(
        test_db,
        project_id=created["session"]["project_id"],
        source_refs=[memoir_source_ref(created), memoir_source_ref(retained)],
    )
    response = await client.patch(
        f"/api/mobile/memoir/projects/{plan.project_id}/book-plan",
        json={
            "chapters": [
                {
                    "id": first.id,
                    "title": first.title,
                    "source_refs": [memoir_source_ref(retained)],
                },
                {
                    "title": "新章节",
                    "source_refs": [memoir_source_ref(created)],
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    memberships = list(
        (
            await test_db.scalars(
                select(MemoirSectionMaterial).where(
                    MemoirSectionMaterial.utterance_id
                    == created["utterance_id"]
                )
            )
        ).all()
    )
    assert len(memberships) == 1
    section = await test_db.get(MemoirSection, memberships[0].section_id)
    assert section is not None
    assert section.chapter_id == response.json()["chapters"][1]["id"]


async def test_draft_change_state_compares_material_snapshot(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "第一段。")
    second = await create_test_voice_transcript(client, test_db, "第二段。")
    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=first["session"]["project_id"],
        source_refs=[memoir_source_ref(first), memoir_source_ref(second)],
    )
    version = MemoirBookChapterDraftVersion(
        draft_id="draft-placeholder",
        project_id=plan.project_id,
        chapter_id=chapter.id,
        version=1,
        content="正文",
        material_snapshot_json=[
            {"material_id": first["utterance_id"]},
            {"material_id": "removed-material"},
        ],
    )
    # The draft FK is required, so use the existing helper-created draft shell.
    from tests.memoir_helpers import create_test_chapter_draft

    draft = await create_test_chapter_draft(
        test_db,
        project_id=plan.project_id,
        chapter_id=chapter.id,
        content="正文",
    )
    stored = await test_db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.draft_id == draft.id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
    )
    assert stored is not None
    stored.material_snapshot_json = version.material_snapshot_json
    test_db.add(stored)
    await test_db.commit()

    state = await chapter_source_change_state(test_db, chapter=chapter)
    assert state.pending_source_ids == {second["utterance_id"]}
    assert state.removed_source_ids == {"removed-material"}
