from __future__ import annotations

from datetime import datetime
import json
from types import SimpleNamespace

import pytest

from app.domain.memoir.narrative_types import MemoirNarrativeGapType
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
)
from app.services.memoir.narrative.book_consistency_context import (
    BookConsistencyContext,
)
from app.services.memoir.narrative.book_consistency_rules import (
    evaluate_book_consistency_rules,
)
from app.services.memoir.narrative.book_consistency_semantic import (
    evaluate_book_semantic_gaps,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    ensure_mobile_memoir_project,
)


class InvalidSemanticAuditAI:
    provider_name = "test"
    model_name = "test-book-audit"

    async def _call_llm(self, *_args, **_kwargs):
        return json.dumps(
            {
                "schema_version": "1",
                "gaps": [
                    {
                        "gap_type": "repetition",
                        "severity": "medium",
                        "dimension": "repetition",
                        "title": "可能重复",
                        "description": "引用了输入之外的故事。",
                        "story_ids": ["story-outside"],
                        "claim_ids": [],
                        "chapter_ids": ["chapter-1"],
                    }
                ],
            }
        )


def _story(story_id: str, title: str, year: int):
    start = datetime(year, 1, 1)
    return SimpleNamespace(
        id=story_id,
        title=title,
        time_start=start,
        time_end=start,
        time_precision="year",
        created_at=start,
        updated_at=start,
        version=1,
        lifecycle_status="active",
        publication_status="eligible",
        verification_status="confirmed",
        summary=title,
        story_type="event",
    )


def _chapter(chapter_id: str, story):
    chapter = SimpleNamespace(
        id=chapter_id,
        title=chapter_id,
        sort_order=0,
        updated_at=datetime(2026, 1, 1),
    )
    return SimpleNamespace(
        chapter=chapter,
        stories={story.id: story},
        story_claims={story.id: []},
        claims={},
        evidence_counts={},
        draft=None,
    )


def _context(*chapters) -> BookConsistencyContext:
    return BookConsistencyContext(
        plan=SimpleNamespace(
            id="plan-1",
            project_id="project-1",
            status="confirmed",
            updated_at=datetime(2026, 1, 1),
        ),
        chapters=tuple(chapters),
        output=None,
        output_version=None,
        claims={},
        evidence_counts={},
        entities={},
    )


async def test_book_output_assembles_confirmed_whole_chapter_drafts_idempotently(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        title="我的回忆录",
        chapter_titles=["童年", "工作"],
        draft_contents=["我在院子里长大。", "后来，我开始工作。"],
        draft_statuses=[
            MemoirBookChapterDraftStatus.CONFIRMED,
            MemoirBookChapterDraftStatus.CONFIRMED,
        ],
    )

    first = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble"
    )
    repeated = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble"
    )

    assert first.status_code == 200, first.text
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["output_version_id"] == first.json()["output_version_id"]
    assert first.json()["version"] == 1
    assert "第1章 童年" in first.json()["content"]
    assert "第2章 工作" in first.json()["content"]
    snapshots = first.json()["chapter_snapshot_json"]
    assert [item["chapter_title"] for item in snapshots] == ["童年", "工作"]
    assert all("draft_version_id" in item for item in snapshots)
    assert all("block_snapshot" not in item for item in snapshots)


async def test_book_output_requires_a_confirmed_plan_and_confirmed_drafts(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        status=MemoirBookPlanStatus.DRAFT,
        chapter_titles=["童年"],
        draft_contents=["我在院子里长大。"],
        draft_statuses=[MemoirBookChapterDraftStatus.DRAFT],
    )
    chapter_title = chapters[0].title

    unconfirmed_plan = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble"
    )
    assert unconfirmed_plan.status_code == 409

    plan.status = MemoirBookPlanStatus.CONFIRMED.value
    await test_db.commit()
    unconfirmed_draft = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble"
    )
    assert unconfirmed_draft.status_code == 409
    assert chapter_title in unconfirmed_draft.json()["detail"]


def test_explicit_story_time_reversal_is_a_high_book_gap():
    later = _story("story-later", "后来工作", 2010)
    earlier = _story("story-earlier", "早年求学", 1990)

    gaps = evaluate_book_consistency_rules(
        _context(
            _chapter("chapter-1", later),
            _chapter("chapter-2", earlier),
        )
    )

    chronology = [
        gap for gap in gaps if gap.gap_type == MemoirNarrativeGapType.CHRONOLOGY
    ]
    assert len(chronology) == 1
    assert chronology[0].severity.value == "high"
    assert chronology[0].involved_chapter_ids == {"chapter-1", "chapter-2"}


async def test_semantic_book_audit_rejects_ids_outside_its_input():
    story = _story("story-1", "童年", 1990)

    with pytest.raises(ValueError, match="did not match the evidence schema"):
        await evaluate_book_semantic_gaps(
            _context(_chapter("chapter-1", story)),
            ai_client=InvalidSemanticAuditAI(),
        )
