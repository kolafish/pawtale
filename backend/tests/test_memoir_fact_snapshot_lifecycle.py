"""Synthetic regressions spanning confirmed facts, edits and book assembly."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirBookChapterDraftStatus
from app.models.memoir import MemoirBookReview, MemoirCompletionIssue, MemoirSectionMaterial
from app.services.memoir import book_review_answer_state, chapter_generation as cg, fact_decisions
from app.services.memoir.narrative.book_output_assembly import assemble_book_output
from tests.test_memoir_book_completion import _book, _create_current_draft


LEGACY_NOTE = "用户已确认按当前建议处理"


async def decision(db, project_id, chapter_id, material_id, *, note="比赛是在1978年。", scope="chapter"):
    issue = MemoirCompletionIssue(
        project_id=project_id, chapter_id=chapter_id if scope == "chapter" else None,
        scope=scope, issue_type="conflict", channel="manual_review",
        question="比赛是哪一年？", reason="年份冲突", priority="high",
        fingerprint=uuid4().hex * 2, input_signature="synthetic",
        related_chapter_ids_json=[chapter_id], related_material_ids_json=[material_id],
        resolution="user_decision", status="closed", outcome="confirmed",
        outcome_note=note, closed_at=utcnow_naive(),
    )
    db.add(issue)
    await db.commit()
    return issue


@pytest.mark.ci_smoke
@pytest.mark.parametrize("missing_old_stamp", [False, True])
async def test_confirmed_fact_edit_stays_current_and_readable(client, test_db, missing_old_stamp):
    project_id, chapters, sources = await _book(client, test_db)
    chapter_id = chapters[0].id
    await decision(test_db, project_id, chapter_id, sources[0]["utterance_id"])
    _, version = await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter_id,
                                           status=MemoirBookChapterDraftStatus.CONFIRMED)
    ctx = await cg.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter_id)
    section = ctx.sections[0]
    # The old workbench omitted just the fact stamp; its input signature was correct.
    if not missing_old_stamp:
        snapshot = deepcopy(version.section_snapshot_json)
        snapshot[0]["fact_decision_signature"] = fact_decisions.signature(section.decisions)
        snapshot[0]["fact_decision_ids"] = [d.issue_id for d in section.decisions]
        version.section_snapshot_json = snapshot
        await test_db.commit()
    original = version.content
    assert cg.chapter_draft_is_current(ctx, version)
    assert cg.chapter_draft_facts_current(ctx, version)
    for title, content in [(section.title, "1978年，我参加比赛。\n\n这一段是我亲手补写的。"),
                           ("比赛往事", "1978年，我参加比赛。\n\n这一段是我亲手补写的。\n还有一件事。")]:
        _, version = await cg.apply_chapter_section_candidate(
            test_db, project_id=project_id, chapter_id=chapter_id, section_id=section.section_id,
            expected_version_id=version.id, expected_input_signature=ctx.input_signature,
            candidate_title=title, candidate_content=content, feedback="仅修改当前小节",
            requested_ai_model=None, resolved_ai_model=None,
        )
        await test_db.commit()
        ctx = await cg.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter_id)
        assert version.content == content
        assert cg.chapter_draft_is_current(ctx, version)
        assert cg.chapter_draft_facts_current(ctx, version)
        plan = await cg.load_chapter_draft_generation_plan(test_db, context=ctx)
        assert not plan.target_context.sections
        assert plan.preserved_sections[0].content == content
        assert not await cg.pending_fact_chapter_ids(test_db, project_id=project_id, chapter_versions=[(chapters[0], version)])
        assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
        assert not assembled.omitted_chapter_ids
    assert original != version.content
    # Missing stamps are never a blanket exemption for newly corrected facts.
    await decision(test_db, project_id, chapter_id, sources[0]["utterance_id"], note="比赛是在1982年。", scope="book")
    ctx = await cg.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter_id)
    assert not cg.chapter_draft_is_current(ctx, version)
    assert not cg.chapter_draft_facts_current(ctx, version)


@pytest.mark.ci_smoke
@pytest.mark.parametrize("scope", ["chapter", "book"])
async def test_legacy_confirmation_is_not_an_answer_or_fact(client, test_db, scope):
    project_id, chapters, sources = await _book(client, test_db)
    chapter_id = chapters[0].id
    _, version = await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter_id,
                                           status=MemoirBookChapterDraftStatus.CONFIRMED)
    issue = await decision(test_db, project_id, chapter_id, sources[0]["utterance_id"], note=LEGACY_NOTE, scope=scope)
    assert not fact_decisions.is_confirmed_fact(issue)
    assert not await fact_decisions.load_fact_decisions(test_db, project_id=project_id)
    review = MemoirBookReview(project_id=project_id, input_signature="synthetic", engine="synthetic", status="ready")
    test_db.add(review)
    await test_db.commit()
    assert not (await book_review_answer_state.load_answer_context(test_db, review=review)).answers
    ctx = await cg.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter_id)
    assert cg.chapter_draft_is_current(ctx, version)
    assert LEGACY_NOTE not in cg.build_chapter_generation_prompt(ctx, editorial_guidance=[])
    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert not assembled.omitted_chapter_ids
    issue.status, issue.outcome, issue.outcome_note, issue.closed_at = "open", "", "", None
    await test_db.commit()
    response = await client.post(f"/api/memoir/projects/{project_id}/completion-issues/{issue.id}/close",
                                json={"outcome": "confirmed", "note": LEGACY_NOTE})
    assert response.status_code == 422
    await test_db.refresh(issue)
    assert issue.status == "open"


@pytest.mark.ci_smoke
async def test_book_fact_projection_uses_bounded_queries(client, test_db):
    project_id, chapters, sources = await _book(client, test_db, chapter_count=3)
    pairs = []
    for chapter, source in zip(chapters, sources, strict=True):
        await decision(test_db, project_id, chapter.id, source["utterance_id"])
        _, version = await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter.id,
                                               status=MemoirBookChapterDraftStatus.CONFIRMED)
        ctx = await cg.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
        snapshot = deepcopy(version.section_snapshot_json)
        snapshot[0]["fact_decision_signature"] = fact_decisions.signature(ctx.sections[0].decisions)
        version.section_snapshot_json = snapshot
        pairs.append((chapter, version))
    await test_db.commit()
    statements = []
    def count_queries(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = test_db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", count_queries)
    try:
        assert not await cg.pending_fact_chapter_ids(test_db, project_id=project_id, chapter_versions=pairs)
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)
    assert len(statements) <= 2, len(statements)

    # Real source moves must still invalidate both sides, even when the old
    # chapter now has no active material. Compare against the full-context path.
    material = await test_db.scalar(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.utterance_id == sources[0]["utterance_id"],
    ))
    material.section_id = pairs[1][1].section_snapshot_json[0]["section_id"]
    material.sort_order += 10
    await test_db.commit()
    assert await cg.pending_fact_chapter_ids(test_db, project_id=project_id, chapter_versions=pairs) == {
        chapters[0].id, chapters[1].id,
    }


@pytest.mark.ci_smoke
async def test_book_review_batch_keeps_the_same_fact_and_manual_edit_history(client, test_db):
    from app.models.memoir import MemoirTranscriptVersion
    from app.services.memoir import book_reviews
    from tests.test_memoir_book_completion import LayeredBookAI

    project_id, chapters, sources = await _book(client, test_db, chapter_count=2)
    expected = {}
    for chapter, source in zip(chapters, sources, strict=True):
        await decision(test_db, project_id, chapter.id, source["utterance_id"])
        edited = MemoirTranscriptVersion(
            voice_session_id=source["session"]["id"], source_artifact_id=source["utterance_id"],
            version_type="user_edited", created_by="user", content="比赛地点是武汉，我后来补上了这一点。",
        )
        test_db.add(edited)
        await test_db.flush()
        material = await test_db.scalar(select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.utterance_id == source["utterance_id"],
        ))
        material.transcript_version_id = edited.id
        await test_db.commit()
        await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter.id,
                                    status=MemoirBookChapterDraftStatus.CONFIRMED)
        ctx = await cg.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
        expected[chapter.id] = (ctx.sections[0].decisions, tuple(edit for m in ctx.materials for edit in m.edits))
    await book_reviews.ensure_chapter_digests(test_db, project_id=project_id, ai_model=None, ai_client=LayeredBookAI())
    context = await book_reviews.load_book_review_context(test_db, project_id=project_id)
    for chapter in context.chapters:
        assert (chapter.decisions, chapter.edits) == expected[chapter.chapter_id]
        assert chapter.edits and "武汉" in chapter.edits[0].after
