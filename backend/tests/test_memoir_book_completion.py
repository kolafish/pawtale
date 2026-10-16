from __future__ import annotations

import json

from httpx import Response
from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from app.config import DEFAULT_AI_TEXT_MODEL
from app.services.ai_base import AITextServiceBase
from app.services.ai_references import ModelReferenceMap
from app.domain.memoir.types import (
    MemoirBookCompletionJobStatus,
    MemoirBookChapterDraftStatus,
    MemoirCompletionIssueScope,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookCompletionJob,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookReview,
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
)
from app.models.projects import Project
from app.schemas.memoir_narrative import (
    MemoirChapterCompletionNextAction,
    MemoirChapterCompletionSummary,
    MemoirChapterSectionRevisionJobRequest,
)
from app.schemas.memoir_book import MemoirBookChapterDraftRevisionRequest
from app.schemas.memoir_book_review import MemoirBookRevisionPlan
from app.services.memoir import (
    automatic_book_draft,
    book_completion_jobs,
    book_revision_generation,
    book_revision_proposals,
    book_review_jobs,
    book_review_answer_state,
    book_reviews,
    completion_interviews,
    completion_issues,
    chapter_preparation_jobs,
    chapter_digests,
    chapter_generation,
    completion_read_model,
    section_digests,
)
from app.services.memoir.book_reviews import (
    BookReviewChapter,
    BookReviewContext,
    normalize_book_review,
)
from app.services.memoir.prompt_budget import MAX_REVIEW_PROMPT_CHARS
from app.services.memoir.editing_job_handlers import (
    register_memoir_editing_job_handlers,
)
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from tests.memoir_helpers import (
    add_test_section_materials,
    create_test_book_plan_with_chapters,
    create_test_chapter_draft,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    run_test_editing_job,
)


class LayeredBookAI(AITextServiceBase):
    provider_name = "test"
    model_name = "test-layered-book"

    def __init__(
        self,
        *,
        section_issue: bool = False,
        chapter_interview_issue: bool = False,
        chapter_manual_issue: bool = False,
        book_issue: bool = False,
        book_actions: bool = False,
        book_profile_action: bool = False,
        section_revision_title: str | None = None,
        section_revision_content: str | None = None,
    ):
        self.section_issue = section_issue
        self.chapter_interview_issue = chapter_interview_issue
        self.chapter_manual_issue = chapter_manual_issue
        self.book_issue = book_issue
        self.book_actions = book_actions
        self.book_profile_action = book_profile_action
        self.section_revision_title = section_revision_title
        self.section_revision_content = section_revision_content
        self.system_prompts: list[str] = []
        self.prompts: list[str] = []

    async def _call_llm(
        self,
        system_prompt,
        user_prompt,
        temperature=0.7,
        max_tokens=4000,
        timeout=60.0,
        return_raw=False,
    ):
        self.system_prompts.append(system_prompt)
        self.prompts.append(user_prompt)
        material_ids = _prompt_ids(user_prompt, "material_id")
        section_ids = _prompt_ids(user_prompt, "section_id")
        chapter_ids = _prompt_ids(user_prompt, "chapter_id")
        if "请审阅下面这个回忆录小章节" in user_prompt:
            candidates = []
            if self.section_issue:
                candidates = [
                    {
                        "issue_type": "meaning",
                        "question": "这件事后来对你有什么影响？",
                        "reason": "个人影响会让这一段的重点更清楚。",
                        "priority": "high",
                        "material_ids": material_ids[:1],
                    }
                ]
            payload = {
                "summary": "讲述者回忆了一次学校比赛。",
                "focus": "这次比赛留下的记忆。",
                "covered_points": ["事情经过"],
                "candidate_issues": candidates,
            }
        elif "请通读回忆录章节" in user_prompt:
            candidates = []
            if self.chapter_interview_issue:
                candidates = [
                    {
                        "issue_type": "meaning",
                        "channel": "interview",
                        "question": "这段经历后来对你有什么影响？",
                        "reason": "补充个人影响会让这一章更丰富。",
                        "priority": "medium",
                        "section_ids": section_ids[:1],
                    }
                ]
            elif self.chapter_manual_issue:
                candidates = [
                    {
                        "issue_type": "conflict",
                        "channel": "manual_review",
                        "question": "这一章比赛年份的两种说法，哪一种准确？",
                        "reason": "年份冲突需要讲述者确认。",
                        "priority": "high",
                        "section_ids": section_ids[:1],
                    }
                ]
            payload = {
                "summary": "本章围绕一次学校比赛展开。",
                "focus": "比赛经历和它留下的记忆。",
                "structure_guidance": ["从比赛现场开始。"],
                "candidate_issues": candidates,
            }
        elif "请通读下面这本回忆录" in user_prompt:
            candidates = []
            if self.book_issue:
                candidates = [
                    {
                        "issue_type": "structure",
                        "question": "是否保留两章相近的学校经历？",
                        "reason": "两章主题接近，需要用户决定取舍。",
                        "priority": "medium",
                        "chapter_ids": chapter_ids[:2],
                        "resolution": "user_decision",
                    }
                ]
            payload = {
                "summary": "全书记录了几段重要成长经历。",
                "strengths": ["人生阶段清楚"],
                "structure_guidance": ["统一章节之间的过渡。"],
                "editorial_actions": (
                    [
                        {
                            "action_type": "remove_repetition",
                            "title": "精简两章重复的学校经历",
                            "reason": "两章都重复描述了同一场比赛。",
                            "chapter_ids": chapter_ids[:2],
                        },
                        {
                            "action_type": "improve_transition",
                            "title": "补充学校到工作的衔接",
                            "reason": "两章之间的时间跳转较突然。",
                            "chapter_ids": chapter_ids[:2],
                        },
                        {
                            "action_type": "reorder_chapters",
                            "title": "先写第二段经历",
                            "reason": "这样更符合回忆展开的顺序。",
                            "chapter_ids": list(reversed(chapter_ids[:2])),
                        },
                        *(
                            [
                                {
                                    "action_type": "revise_book_profile",
                                    "title": "调整前言、后记与封底文案",
                                    "reason": "当前成书信息没有体现故园主题。",
                                    "chapter_ids": [],
                                    "metadata_fields": [
                                        "preface",
                                        "afterword",
                                        "back_cover_quote",
                                    ],
                                }
                            ]
                            if self.book_profile_action
                            else []
                        ),
                    ]
                    if self.book_actions
                    else []
                ),
                "candidate_issues": candidates,
            }
        elif "请为一个回忆录小节制定可供用户确认的修改思路" in user_prompt:
            payload = {
                "summary": "保留事实，只把这一小节写得更平实。",
                "items": [{
                    "title": "调整叙述语气",
                    "reason": "当前表达可以更克制。",
                    "approach": "保留事情经过，压缩重复表达并改成平实语气。",
                    "targets": ["比赛后的回想峰"],
                }],
                "book_metadata_patch": {"title": "不应修改书名"},
            }
        elif (
            "请根据全书检查问题和用户回答，判断当前书稿是否需要修改" in user_prompt
            or "请根据用户选中的全书编辑建议，制定书稿修改思路" in user_prompt
            or "请根据用户选中的问题回答和编辑建议，制定一份统一的书稿修改思路" in user_prompt
        ):
            profile_only = (
                "调整前言、后记与封底文案" in user_prompt
                and "精简两章重复的学校经历" not in user_prompt
            )
            payload = {
                "summary": "先保留 C1 的两段经历，再统一 S1 的相关人物称谓。",
                "items": (
                    [{
                        "title": "调整前言、后记与封底文案",
                        "reason": "当前成书信息没有体现故园主题。",
                        "approach": "保持正文不变，只调整三项成书信息。",
                        "targets": ["前言", "后记", "封底文案"],
                    }]
                    if profile_only
                    else [{
                        "title": "保留 C1 经历并统一 S1 称谓",
                        "reason": "C1 的两段经历发生在不同阶段，S1 的人物称谓需要一致。",
                        "approach": "保留 C1 的两段经历，只统一 S1 的相关人物称谓。",
                        "targets": ["C1", "S1"],
                    }]
                ),
                "book_metadata_patch": (
                    {
                        "preface": "写下这些故园往事，是为了留住家人的声音。",
                        "afterword": "故事写到这里，故园的风仍在记忆里。",
                        "back_cover_quote": "把故园与家人的声音，留在这本书里。",
                    }
                    if "调整前言、后记与封底文案" in user_prompt
                    else {}
                ),
            }
        elif "请根据用户反馈更新完整修改思路" in user_prompt:
            payload = {
                "summary": "保留两段经历，统一称谓并适当精简。",
                "items": [
                    {
                        "title": "统一称谓并精简重复",
                        "reason": "保留不同阶段经历，同时避免表述重复。",
                        "approach": "将相关人物统一称为老同学，并压缩重复叙述。",
                        "targets": ["学校经历"],
                    }
                ],
            }
        elif "请按照已确认的修改思路生成书稿修改稿" in user_prompt:
            payload = {
                "section_revisions": [] if "保持正文不变" in user_prompt else [
                    {
                        "section_id": section_ids[0],
                        "content": "两段经历都保留，相关人物统一称为老同学。",
                    }
                ],
            }
        else:
            section_materials = _prompt_section_materials(user_prompt)
            is_revision = "用户修改要求：" in user_prompt
            current_title = (
                user_prompt.partition("当前小节标题：\n")[2].splitlines()[0]
                if "当前小节标题：\n" in user_prompt
                else ""
            )
            payload = {
                "sections": [
                    {
                        "section_id": section_id,
                        "title": (
                            self.section_revision_title or current_title
                            if is_revision
                            else ""
                        ),
                        "content": (
                            self.section_revision_content
                            if is_revision and self.section_revision_content is not None
                            else (
                                ("那天刮着风，我参加了学校比赛。" if is_revision else "那年，我参加了学校比赛。")
                                + "这段经历让我一直记得，也让我第一次认真看待输赢。"
                            )
                        ),
                        "used_material_ids": allowed_material_ids,
                    }
                    for section_id, allowed_material_ids in section_materials
                ],
                "missing_source_candidates": [],
                "editor_notes": [],
            }
        return json.dumps(payload, ensure_ascii=False)


def test_book_review_rejects_prompt_that_cannot_be_read_in_full():
    context = BookReviewContext(
        project_id="project-1",
        plan_id="plan-1",
        book_metadata={},
        chapters=(
            BookReviewChapter(
                chapter_id="chapter-1",
                title="童年",
                sort_order=0,
                digest_id="digest-1",
                digest_signature="digest-signature-1",
                digest={"summary": "童年回忆"},
                draft_version_id="version-1",
                draft_hash="hash-1",
                material_ids=frozenset(),
                draft_content="回" * MAX_REVIEW_PROMPT_CHARS,
            ),
        ),
        input_signature="review-signature",
    )
    with pytest.raises(HTTPException) as caught:
        book_reviews.build_book_review_prompt(context)
    assert caught.value.status_code == 422


def test_book_review_restores_chapter_titles_and_promotes_editorial_changes():
    context = BookReviewContext(
        project_id="project-1",
        plan_id="plan-1",
        book_metadata={"title": "爷爷的故事"},
        chapters=(
            BookReviewChapter(
                chapter_id="chapter-1",
                title="故园旧忆与 C2 家人琐记",
                sort_order=1,
                digest_id="digest-1",
                digest_signature="digest-signature-1",
                digest={},
                draft_version_id="version-1",
                draft_hash="hash-1",
                material_ids=frozenset({"material-1"}),
            ),
            BookReviewChapter(
                chapter_id="chapter-2",
                title="工作与选择",
                sort_order=2,
                digest_id="digest-2",
                digest_signature="digest-signature-2",
                digest={},
                draft_version_id="version-2",
                draft_hash="hash-2",
                material_ids=frozenset({"material-2"}),
            ),
        ),
        input_signature="book-signature-1",
    )

    review = normalize_book_review(
        {
            "summary": "C1 讲家庭，C2 讲工作。",
            "strengths": ["C2 的职业主线完整。"],
            "structure_guidance": [
                "在 C1 与 C2 之间增加过渡。",
                *[f"普通编辑建议 {index}" for index in range(2, 9)],
            ],
            "candidate_issues": [
                {
                    "issue_type": "meaning",
                    "question": "书名是否应从 C1 的爷爷主题改为个人成长？",
                    "reason": "这是 C1、C2 共同决定的全书定位。",
                    "priority": "medium",
                    "chapter_ids": ["C1", "C2"],
                    "resolution": "editorial_change",
                }
            ],
        },
        context=context,
    )

    visible = json.dumps(review, ensure_ascii=False)
    assert "C1" not in visible
    assert review["summary"] == ("第1章《故园旧忆与 C2 家人琐记》 讲家庭，第2章《工作与选择》 讲工作。")
    assert "第1章《故园旧忆与 C2 家人琐记》" in visible
    assert "第2章《工作与选择》" in visible
    assert review["candidate_issues"] == []
    assert len(review["structure_guidance"]) == 8
    assert "书名是否应" in review["structure_guidance"][0]


@pytest.mark.parametrize("question", [
    "先生和亲友的婚姻经历是否需要匿名？",
    "完整联系方式是否适合写入书稿？",
])
def test_private_review_rejects_privacy_questions_at_every_level(question):
    candidate = {
        "issue_type": "privacy", "channel": "manual_review",
        "resolution": "user_decision", "priority": "high",
        "question": question, "reason": "模型按公开出版标准提出确认。",
        "material_ids": ["M1"], "section_ids": ["S1"], "chapter_ids": ["C1"],
    }
    section = section_digests.normalize_section_digest_payload(
        {"summary": "讲述者回忆亲友生活。", "candidate_issues": [candidate]},
        material_references=ModelReferenceMap("M", ["material-1"]),
    )
    chapter = chapter_digests.normalize_chapter_digest(
        {"summary": "讲述者回忆亲友生活。", "candidate_issues": [candidate]},
        section_references=ModelReferenceMap("S", ["section-1"]),
    )
    context = BookReviewContext(
        project_id="project-1", plan_id="plan-1", book_metadata={"title": "回忆"},
        chapters=(BookReviewChapter(
            chapter_id="chapter-1", title="亲友生活", sort_order=0,
            digest_id="digest-1", digest_signature="digest-signature", digest={},
            draft_version_id="version-1", draft_hash="draft-hash",
            material_ids=frozenset(),
        ),),
        input_signature="private-review",
    )
    book = normalize_book_review(
        {"summary": "讲述者回忆亲友生活。", "candidate_issues": [candidate]},
        context=context,
    )
    assert section["candidate_issues"] == chapter["candidate_issues"] == book["candidate_issues"] == []
    assert completion_issues.book_review_candidate_fingerprints([candidate]) == []


def test_book_review_fingerprints_follow_questions_when_order_changes():
    common = {
        "issue_type": "clarity",
        "priority": "medium",
        "channel": "manual_review",
        "reason": "需要用户判断",
        "chapter_ids": ["chapter-1"],
        "material_ids": [],
    }
    candidates = [
        {**common, "question": question}
        for question in ("问题 A？", "问题 B？")
    ]
    for candidate in candidates:
        candidate["fingerprint"] = completion_issues.stable_book_issue_fingerprint(candidate)

    def fingerprints(items):
        return {
            candidate["question"]: fingerprint
            for fingerprint, candidate in completion_issues.book_review_candidate_fingerprints(items)
        }

    assert fingerprints(candidates) == fingerprints(list(reversed(candidates)))


def test_book_review_anchors_only_verified_distinct_quoted_facts():
    quote = "某位知名企业家曾经是我的经理"
    context = BookReviewContext(
        project_id="project-1",
        plan_id="plan-1",
        book_metadata={"title": "故事"},
        chapters=tuple(
            BookReviewChapter(
                chapter_id=f"chapter-{index}",
                title=f"第{index}章",
                sort_order=index,
                digest_id=f"digest-{index}",
                digest_signature=f"signature-{index}",
                digest={},
                draft_version_id=f"version-{index}",
                draft_hash=f"hash-{index}",
                material_ids=frozenset(),
                draft_content=content,
            )
            for index, content in (
                (1, f"那时候，{quote}。"),
                (2, "这一章只写散步。"),
            )
        ),
        input_signature="book-signature",
    )
    def candidate(question, chapter_id, issue_type="conflict"):
        return {
            "issue_type": issue_type,
            "question": question,
            "reason": "这需要用户决定。",
            "priority": "high",
            "chapter_ids": [chapter_id],
            "evidence_quote": quote,
            "resolution": "user_decision",
        }
    short_quote = candidate("经理一词需要确认吗？", "C1")
    short_quote["evidence_quote"] = "经理"

    review = normalize_book_review(
        {
            "summary": "有待确认内容。",
            "candidate_issues": [
                candidate("是否匿名？", "C1"),
                candidate("是否匿名？", "C2"),
                candidate("这段关系准确吗？", "C1", "clarity"),
                candidate("这段邀请准确吗？", "C1", "clarity"),
                short_quote,
            ],
        },
        context=context,
    )
    issues = review["candidate_issues"]
    assert issues[0]["evidence_quote"] == quote
    assert "evidence_quote" not in issues[1]
    assert "evidence_quote" not in issues[2]
    assert "evidence_quote" not in issues[3]
    assert "evidence_quote" not in issues[4]
    assert issues[2]["fingerprint"] != issues[3]["fingerprint"]


async def test_book_review_reuses_quoted_conflict_issue_across_reworded_reviews(
    client, test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    chapter_id = "chapter-1"
    old = MemoirCompletionIssue(
        project_id=project_id,
        scope="book",
        issue_type="conflict",
        channel="manual_review",
        question="书中‘学校比赛安排在九月’与另一章十月的说法冲突，以哪一个为准？",
        reason="请决定如何表述。",
        priority="high",
        fingerprint="a" * 64,
        related_chapter_ids_json=[chapter_id],
        input_signature="old-review",
    )
    test_db.add(old)
    test_db.add(MemoirCompletionIssue(
        project_id=project_id,
        scope="book",
        issue_type="conflict",
        channel="manual_review",
        question="‘学校比赛安排在九月’这个日期准确吗？",
        reason="旧轮次的问题。",
        priority="high",
        fingerprint="c" * 64,
        related_chapter_ids_json=[chapter_id],
        input_signature="earlier-review",
        status="closed",
        outcome="superseded",
    ))
    await test_db.flush()
    old_id = old.id
    candidate = {
        "issue_type": "conflict",
        "channel": "manual_review",
        "question": "学校比赛到底在九月还是十月？",
        "reason": "时间冲突需要讲述者确认。",
        "priority": "high",
        "chapter_ids": [chapter_id],
        "evidence_quote": "学校比赛安排在九月",
        "resolution": "user_decision",
    }
    candidate["fingerprint"] = completion_issues.stable_book_issue_fingerprint(candidate)

    first = await completion_issues.sync_book_review_issues(
        test_db, project_id=project_id, input_signature="new-review",
        candidates=[candidate],
    )
    assert len(first) == 1
    assert first[0].id == old_id
    assert first[0].status == "open"
    assert first[0].input_signature == "new-review"

    first[0].status = "closed"
    first[0].outcome = "confirmed"
    first[0].outcome_note = "以九月为准。"
    await test_db.flush()
    reworded = {**candidate, "question": "学校比赛的时间以哪一个说法为准？"}
    reworded["fingerprint"] = completion_issues.stable_book_issue_fingerprint(reworded)
    assert reworded["fingerprint"] == candidate["fingerprint"]
    current = await completion_issues.sync_book_review_issues(
        test_db, project_id=project_id, input_signature="later-review",
        candidates=[reworded],
    )
    assert len(current) == 1
    assert current[0].id == old_id
    assert current[0].status == "closed"
    assert current[0].outcome_note == "以九月为准。"
    assert current[0].input_signature == "new-review"
    assert current[0].question == candidate["question"]


@pytest.mark.ci_smoke
async def test_legacy_privacy_does_not_block_private_book_or_repeat_decisions(
    client, test_db,
):
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output

    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    versions = []
    for chapter in chapters:
        _, version = await _create_current_draft(
            test_db, project_id=project_id, chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.DRAFT,
        )
        versions.append(version)
    original_content = versions[0].content
    candidate = {
        "issue_type": "privacy",
        "channel": "manual_review",
        "question": "这段内容是否适合保留？",
        "reason": "请由讲述者决定。",
        "priority": "high",
        "chapter_ids": [chapters[0].id],
        "evidence_quote": original_content,
        "resolution": "user_decision",
    }
    candidate["fingerprint"] = completion_issues.stable_book_issue_fingerprint(candidate)
    issue = MemoirCompletionIssue(
        project_id=project_id, scope="book", issue_type="privacy",
        channel="manual_review", question=candidate["question"],
        reason=candidate["reason"], priority="high",
        related_chapter_ids_json=candidate["chapter_ids"],
        fingerprint=candidate["fingerprint"], input_signature="first-review",
        resolution="user_decision",
    )
    test_db.add(issue)
    await test_db.commit()

    full = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert full.omitted_chapter_ids == []
    assert full.chapter_snapshot_json[0]["content"] == original_content
    await test_db.commit()
    exported = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=txt",
    )
    assert exported.status_code == 200
    assert original_content in exported.text
    shared = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/share")
    assert shared.status_code == 200, shared.text
    assert (await completion_issues.collection_response(
        test_db, project_id=project_id,
    )).issues == []
    history = await completion_issues.collection_response(
        test_db, project_id=project_id, include_closed=True,
    )
    assert history.issues[0].id == issue.id
    assert history.issues[0].blocks_book is False
    assert issue.status == "open"
    pending_review = MemoirBookReview(
        project_id=project_id, engine=book_reviews.BOOK_REVIEW_ENGINE,
        input_signature="first-review", status="ready", review_json={},
    )
    assert (await book_review_answer_state.load_answer_context(
        test_db, review=pending_review,
    )).unresolved_count == 0

    closed = await client.post(
        f"/api/memoir/projects/{project_id}/completion-issues/{issue.id}/close",
        json={"outcome": "declined", "note": "用户确认不处理，保留当前书稿表述。"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert closed.json()["outcome"] == "declined"

    full = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert full.omitted_chapter_ids == []
    assert [chapter["chapter_id"] for chapter in full.chapter_snapshot_json] == [
        chapter.id for chapter in chapters
    ]
    assert full.chapter_snapshot_json[0]["content"] == original_content
    assert (await chapter_generation.latest_chapter_draft_version(
        test_db, project_id=project_id, chapter_id=chapters[0].id,
    )).content == original_content

    reworded = {**candidate, "question": "是否要修改这段表述？"}
    current = await completion_issues.sync_book_review_issues(
        test_db, project_id=project_id, input_signature="second-review",
        candidates=[reworded],
    )
    assert current == []
    await test_db.refresh(issue)
    assert issue.status == "closed"
    assert issue.outcome == "declined"
    assert issue.outcome_note == "用户确认不处理，保留当前书稿表述。"

    review = MemoirBookReview(
        project_id=project_id,
        engine=book_reviews.BOOK_REVIEW_ENGINE,
        input_signature="second-review",
        status="ready",
        review_json={},
    )
    answer_context = await book_review_answer_state.load_answer_context(
        test_db, review=review,
    )
    assert answer_context.answers == ()
    assert answer_context.unresolved_count == 0


async def test_book_review_keeps_unresolved_conflict_issue_when_model_omits_it(
    client, test_db,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter_id = chapters[0].id
    draft = MemoirBookChapterDraft(
        project_id=project_id, chapter_id=chapter_id, status="confirmed",
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(MemoirBookChapterDraftVersion(
        draft_id=draft.id, project_id=project_id, chapter_id=chapter_id,
        version=1, content="书中写到‘某位知名企业家’曾是我的经理。",
    ))
    old = MemoirCompletionIssue(
        project_id=project_id,
        scope="book",
        issue_type="conflict",
        channel="manual_review",
        question="书中‘某位知名企业家’曾是你的经理，这段是否适合保留？",
        reason="请决定如何表述。",
        priority="high",
        fingerprint="b" * 64,
        related_chapter_ids_json=[chapter_id],
        input_signature="old-review",
    )
    test_db.add(old)
    await test_db.flush()
    current = await completion_issues.sync_book_review_issues(
        test_db, project_id=project_id, input_signature="new-review",
        candidates=[],
    )
    assert [issue.id for issue in current] == [old.id]
    assert old.status == "open"
    assert old.input_signature == "new-review"

    test_db.add(MemoirBookChapterDraftVersion(
        draft_id=draft.id, project_id=project_id, chapter_id=chapter_id,
        version=2, content="书中只写一位同事曾是我的经理。",
    ))
    await test_db.flush()
    current = await completion_issues.sync_book_review_issues(
        test_db, project_id=project_id, input_signature="later-review",
        candidates=[],
    )
    assert current == []
    assert old.status == "closed"
    assert old.outcome == "superseded"


def test_book_review_does_not_invent_material_provenance():
    context = BookReviewContext(
        project_id="project-1",
        plan_id="plan-1",
        book_metadata={"title": "故事"},
        chapters=(
            BookReviewChapter(
                chapter_id="chapter-1",
                title="第一章",
                sort_order=1,
                digest_id="digest-1",
                digest_signature="signature-1",
                digest={},
                draft_version_id="version-1",
                draft_hash="hash-1",
                material_ids=frozenset({"unrelated-material", "another-material"}),
            ),
        ),
        input_signature="book-signature",
    )
    review = normalize_book_review(
        {
            "summary": "需要补充后续。",
            "candidate_issues": [
                {
                    "issue_type": "impact",
                    "question": "后来这件事带来了什么变化？",
                    "reason": "现有正文缺少后续。",
                    "priority": "medium",
                    "chapter_ids": ["C1"],
                    "resolution": "new_material",
                }
            ],
        },
        context=context,
    )
    candidate = review["candidate_issues"][0]
    assert candidate["chapter_ids"] == ["chapter-1"]
    assert candidate["material_ids"] == []


async def test_book_review_regeneration_preserves_confirmed_answer(
    client, test_db,
):
    project_id, chapters, sources = await _book(client, test_db)
    chapter_id = chapters[0].id
    source_id = sources[0]["utterance_id"]
    candidate = {
        "issue_type": "conflict",
        "channel": "manual_review",
        "question": "比赛的时间以哪一个说法为准？",
        "reason": "需要用户决定。",
        "priority": "high",
        "chapter_ids": [chapter_id],
        "material_ids": ["arbitrary-old-material"],
        "resolution": "user_decision",
    }
    old_fingerprint = completion_issues.stable_book_issue_fingerprint(candidate)
    issue = MemoirCompletionIssue(
        project_id=project_id,
        scope="book",
        issue_type="conflict",
        channel="manual_review",
        question=candidate["question"],
        reason=candidate["reason"],
        priority="high",
        fingerprint=old_fingerprint,
        related_material_ids_json=[source_id],
        related_chapter_ids_json=[chapter_id],
        resolution="user_decision",
        input_signature="same-review-input",
        status="closed",
        outcome="confirmed",
        outcome_note="以九月为准。",
    )
    test_db.add(issue)
    await test_db.flush()
    issue_id = issue.id
    candidate["material_ids"] = []
    candidate["fingerprint"] = completion_issues.stable_book_issue_fingerprint(candidate)

    current = await completion_issues.sync_book_review_issues(
        test_db,
        project_id=project_id,
        input_signature="new-review-input",
        candidates=[candidate],
    )
    assert len(current) == 1
    assert current[0].id == issue_id
    assert current[0].fingerprint == candidate["fingerprint"]
    assert current[0].outcome_note == "以九月为准。"
    assert current[0].input_signature == "same-review-input"
    assert current[0].related_chapter_ids_json == [chapter_id]
    assert current[0].related_material_ids_json == [source_id]
    assert current[0].resolution == "user_decision"


async def test_book_answer_reads_issue_without_review_candidate_history(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    review = MemoirBookReview(
        project_id=project_id,
        engine=book_reviews.BOOK_REVIEW_ENGINE,
        input_signature="current-review",
        status="ready",
        review_json={},
    )
    issue = MemoirCompletionIssue(
        project_id=project_id,
        scope="book",
        issue_type="privacy",
        channel="manual_review",
        question="是否保留真实姓名？",
        reason="需要用户决定。",
        priority="high",
        fingerprint="a" * 64,
        related_material_ids_json=[],
        related_chapter_ids_json=["chapter-1"],
        resolution="user_decision",
        input_signature="current-review",
        status="closed",
        outcome="confirmed",
        outcome_note="使用化名。",
    )
    test_db.add_all([review, issue])
    await test_db.flush()

    context = await book_review_answer_state.load_answer_context(test_db, review=review)
    assert len(context.answers) == 1
    assert context.answers[0].chapter_ids == ("chapter-1",)
    assert context.answers[0].resolution == "user_decision"


async def test_book_issue_uses_its_chapter_instead_of_unrelated_material(
    client, test_db,
):
    project_id, chapters, sources = await _book(client, test_db, chapter_count=2)
    response = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert response.status_code == 200
    target = await test_db.scalar(
        select(MemoirSection).where(MemoirSection.chapter_id == chapters[1].id)
    )
    assert target is not None
    issue = MemoirCompletionIssue(
        project_id=project_id,
        scope="book",
        issue_type="clarity",
        channel="interview",
        question="后来发生了什么？",
        reason="补充这章的后续",
        priority="medium",
        fingerprint="a" * 64,
        related_material_ids_json=[sources[0]["utterance_id"]],
        related_chapter_ids_json=[chapters[1].id],
        input_signature="book-target-test",
    )
    test_db.add(issue)
    await test_db.flush()
    assert await completion_interviews._target_section_id(test_db, issue=issue) == target.id


def test_section_revision_keeps_whole_chapter_size_limit():
    with pytest.raises(ValueError, match="整章正文不能超过"):
        MemoirBookChapterDraftRevisionRequest(
            expected_version_id="version-1",
            sections=[
                {
                    "section_id": f"section-{index}",
                    "content": "一" * 70_000,
                }
                for index in range(3)
            ],
        )


def test_book_revision_plan_rejects_non_object_metadata_patch():
    with pytest.raises(ValueError):
        MemoirBookRevisionPlan.model_validate(
            {
                "summary": "调整成书信息",
                "book_metadata_patch": [],
            }
        )


def test_book_revision_comparison_counts_title_only_change():
    before = [{
        "chapter_id": "chapter-1",
        "chapter_title": "上学时光",
        "sections": [{
            "section_id": "section-1",
            "title": "比赛后的回想峰",
            "content": "这是已经存在的完整章节正文。",
        }],
    }]
    after = [{
        "chapter_id": "chapter-1",
        "chapter_title": "上学时光",
        "sections": [{
            "section_id": "section-1",
            "title": "赛后回想",
            "content": "这是已经存在的完整章节正文。",
        }],
    }]

    comparison = book_revision_proposals.build_comparison(
        before=before,
        after=after,
    )

    assert comparison["changed_chapter_count"] == 1
    assert comparison["changed_section_count"] == 1
    assert comparison["chapters"][0]["sections"][0] == {
        "section_id": "section-1",
        "title": "赛后回想",
        "before_title": "比赛后的回想峰",
        "after_title": "赛后回想",
        "before_content": "这是已经存在的完整章节正文。",
        "after_content": "这是已经存在的完整章节正文。",
        "before_char_count": 14,
        "after_char_count": 14,
    }


def test_editorial_profile_plan_requires_selected_metadata_fields():
    with pytest.raises(
        book_revision_generation.BookRevisionError,
        match=r"(?=.*前言)(?=.*后记)(?=.*封底文案)",
    ):
        book_revision_generation._validate_metadata_plan(
            plan=MemoirBookRevisionPlan(
                summary="调整成书信息",
                book_metadata_patch={},
            ),
            actions=[
                {
                    "action_type": "revise_book_profile",
                    "metadata_fields": [
                        "preface",
                        "afterword",
                        "back_cover_quote",
                    ],
                }
            ],
            book_information={},
        )


def test_section_revision_job_validates_each_operation_input():
    assert MemoirChapterSectionRevisionJobRequest(
        operation="feedback",
        proposal_id=" proposal-1 ",
        feedback=" 写得更平实一些 ",
    ).model_dump() == {
        "ai_model": None,
        "operation": "feedback",
        "proposal_id": "proposal-1",
        "feedback": "写得更平实一些",
    }
    with pytest.raises(ValueError, match="修改意见"):
        MemoirChapterSectionRevisionJobRequest(
            operation="plan",
            feedback="   ",
        )
    with pytest.raises(ValueError, match="修改方案"):
        MemoirChapterSectionRevisionJobRequest(
            operation="draft",
        )








def _prompt_ids(prompt: str, key: str) -> list[str]:
    prefix = f"{key}: "
    return [line.removeprefix(prefix).strip() for line in prompt.splitlines() if line.startswith(prefix)]


def _prompt_section_materials(prompt: str) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    current_materials: list[str] | None = None
    for line in prompt.splitlines():
        if line.startswith("section_id: "):
            current_materials = []
            result.append((line.removeprefix("section_id: ").strip(), current_materials))
        elif line.startswith("material_id: ") and current_materials is not None:
            current_materials.append(line.removeprefix("material_id: ").strip())
    return result


def _install_ai(
    monkeypatch,
    fake: LayeredBookAI,
    requested_models: list[str | None] | None = None,
) -> None:
    def factory(model=None):
        if requested_models is not None:
            requested_models.append(model)
        return fake

    monkeypatch.setattr(section_digests, "get_ai_service", factory)
    monkeypatch.setattr(chapter_digests, "get_ai_service", factory)
    monkeypatch.setattr(chapter_generation, "get_ai_service", factory)
    monkeypatch.setattr(book_reviews, "get_ai_service", factory)
    monkeypatch.setattr(book_revision_generation, "get_ai_service", factory)


async def _run_chapter_preparation(
    client,
    test_db,
    *,
    project_id: str,
    chapter_id: str,
    operation: str = "prepare",
    ai_model: str | None = None,
):
    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs",
        json={"operation": operation, "ai_model": ai_model},
    )
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(
            test_db,
            queued.json()["job_id"],
        ) is True
    finally:
        clear_editing_job_handlers()
    return await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/workspace",
    )


async def _run_book_review(
    client,
    test_db,
    *,
    project_id: str,
    force_regenerate: bool = False,
    ai_model: str | None = None,
):
    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={
            "force_regenerate": force_regenerate,
            "ai_model": ai_model,
        },
    )
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(
            test_db,
            queued.json()["job_id"],
        ) is True
    finally:
        clear_editing_job_handlers()
    snapshot = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview",
    )
    return Response(
        snapshot.status_code,
        json=snapshot.json()["book_review"],
        request=snapshot.request,
    )


async def _completion_field(client, project_id: str, field: str):
    response = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert response.status_code == 200, response.text
    return response.json()[field]


def test_book_review_answer_output_rejects_structure_changes():
    valid = {
        "section_revisions": [
            {
                "section_id": "S1",
                "content": "修改后的完整小节正文。",
            }
        ],
    }

    assert book_revision_generation._RevisionOutput.model_validate(valid).model_dump() == valid
    with pytest.raises(ValueError):
        book_revision_generation._RevisionOutput.model_validate(
            {
                **valid,
                "summary": "补充 C3 章节并调整 S19 小节。",
            }
        )
    with pytest.raises(ValueError):
        book_revision_generation._RevisionOutput.model_validate(
            {
                **valid,
                "chapter_order": ["C1"],
            }
        )
    with pytest.raises(ValueError):
        book_revision_generation._RevisionOutput.model_validate(
            {
                **valid,
                "section_revisions": [
                    {
                        **valid["section_revisions"][0],
                        "title": "模型不应改名",
                    }
                ],
            }
        )


async def _book(client, test_db, *, chapter_count: int = 1):
    project_id = await ensure_mobile_memoir_project(client)
    sources = [
        await create_test_voice_transcript(
            client,
            test_db,
            f"那年，我参加了第{index + 1}次学校比赛。",
            project_id=project_id,
        )
        for index in range(chapter_count)
    ]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=[f"学校里的比赛{index + 1}" for index in range(chapter_count)],
        source_refs_by_chapter=[[memoir_source_ref(source)] for source in sources],
    )
    return project_id, chapters, sources


async def _create_current_draft(
    test_db,
    *,
    project_id: str,
    chapter_id: str,
    status: MemoirBookChapterDraftStatus,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    draft = await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=status,
        content="这是已经存在的完整章节正文。",
    )
    context = await chapter_generation.load_chapter_generation_context(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    version = await test_db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.draft_id == draft.id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
    )
    assert version is not None
    used_ids = {item.material_id for item in context.materials}
    version.input_signature = context.input_signature
    version.prompt_snapshot = chapter_generation._render_policy_tag(context)
    version.used_material_ids_json = [item.material_id for item in context.materials]
    version.material_snapshot_json = chapter_generation._snapshot(
        context,
        used_material_ids=used_ids,
    )
    version.section_snapshot_json = [
        {
            "section_id": section.section_id,
            "title": section.title,
            "section_type": section.section_type,
            "section_version": section.section_version,
            "sort_order": section.sort_order,
            "input_signature": chapter_generation._section_input_signature(
                context,
                section,
            ),
            "content": version.content,
            "used_material_ids": [material.material_id for material in section.materials],
        }
        for section in context.sections
    ]
    test_db.add(version)
    await test_db.commit()
    return draft, version


@pytest.mark.parametrize("empty_sections_present", [True, False])
async def test_completion_requires_material_before_checking_empty_chapters(
    client, test_db, empty_sections_present,
):
    project_id = await ensure_mobile_memoir_project(client)
    source = await create_test_voice_transcript(
        client, test_db, "这是用于发布回归的虚构讲述。", project_id=project_id,
    )
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["有讲述的章节", "尚未讲述的章节一", "尚未讲述的章节二"],
        source_refs_by_chapter=[[memoir_source_ref(source)], [], []],
    )
    _, version = await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    if not empty_sections_present:
        for section in (await test_db.scalars(
            select(MemoirSection).where(MemoirSection.chapter_id.in_([c.id for c in chapters[1:]]))
        )).all():
            await test_db.delete(section)
    await test_db.commit()

    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["completed_chapters"] == 1
    for item in body["chapters"][1:]:
        assert item["state"] == "needs_input"
        assert item["next_action"]["kind"] == "wait"
        assert item["next_action"]["disabled"] is True
        assert item["active_issue"] is None
    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[1].id}/workspace"
    )
    assert workspace.status_code == 200
    assert workspace.json()["completion_summary"]["state"] == "needs_input"

    old_batch = MemoirBookCompletionJob(
        project_id=project_id,
        status=MemoirBookCompletionJobStatus.FAILED.value,
        target_chapters_json=[{"chapter_id": c.id, "title": c.title} for c in chapters[1:]],
        chapter_job_ids_json={},
        enqueue_errors_json={c.id: "章节没有可用于生成正文的素材" for c in chapters[1:]},
    )
    test_db.add(old_batch)
    await test_db.commit()
    retry = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={"retry_job_id": old_batch.id},
    )
    assert retry.status_code == 409
    assert await test_db.scalar(select(func.count()).select_from(MemoirBookCompletionJob)) == 1
    await test_db.refresh(old_batch)
    assert old_batch.status == MemoirBookCompletionJobStatus.FAILED.value

    # A real unchecked chapter still queues normally; the empty chapters do not.
    version.prompt_snapshot = ""
    await test_db.commit()
    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs", json={},
    )
    assert queued.status_code == 202
    assert queued.json()["failed_chapter_count"] == 0
    assert [item["chapter_id"] for item in queued.json()["chapters"]] == [chapters[0].id]
    assert queued.json()["chapters"][0]["job_id"]
    assert await test_db.scalar(select(func.count()).select_from(MemoirSectionMaterial)) == 1


async def test_existing_current_draft_still_requires_a_real_completion_check(
    client,
    test_db,
):
    project_id, chapters, _ = await _book(client, test_db)
    _, version = await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )

    version.prompt_snapshot = ""
    await test_db.commit()

    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")

    assert overview.status_code == 200
    summary = overview.json()["chapters"][0]
    assert summary["state"] == "not_started"
    assert summary["headline"] == "尚未检查是否需要补充"
    assert summary["next_action"]["action_text"] == "检查本章"


async def test_prepare_keeps_a_current_existing_draft_unchanged(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    _, version = await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT,
    )
    fake = LayeredBookAI(chapter_interview_issue=True)
    _install_ai(monkeypatch, fake)

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )

    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert body["draft_version_id"] == version.id
    assert body["draft_content"] == "这是已经存在的完整章节正文。"
    assert not any("请把下面的原始讲述整理为回忆录章节" in prompt for prompt in fake.prompts)
    version_count = await test_db.scalar(
        select(func.count())
        .select_from(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.chapter_id == chapter.id)
    )
    assert version_count == 1


async def test_prepare_uses_and_remembers_the_default_ai_model(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI()
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)

    first = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_model=DEFAULT_AI_TEXT_MODEL,
    )

    assert first.status_code == 200, first.text
    assert requested_models
    assert set(requested_models) == {DEFAULT_AI_TEXT_MODEL}
    project = await test_db.get(Project, project_id)
    assert project is not None
    assert project.metadata_json["ai_model"] == DEFAULT_AI_TEXT_MODEL

    requested_models.clear()
    second = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )

    assert second.status_code == 200, second.text
    assert requested_models
    assert set(requested_models) == {DEFAULT_AI_TEXT_MODEL}


async def test_chapter_preparation_job_reports_progress_and_default_model(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI()
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter.id}/jobs",
        json={
            "operation": "prepare",
            "ai_model": DEFAULT_AI_TEXT_MODEL,
        },
    )

    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["status"] == "pending"
    assert queued_body["stage"] == "queued"
    assert queued_body["total_section_count"] == 1
    assert queued_body["requested_ai_model"] == DEFAULT_AI_TEXT_MODEL
    assert queued_body["can_cancel"] is True

    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert overview.status_code == 200, overview.text
    assert overview.json()["chapters"][0]["state"] == "preparing"
    assert overview.json()["chapters"][0]["next_action"]["action_text"] == ("查看进度")

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                queued_body["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    current = await client.get(f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter.id}/jobs/current")

    assert current.status_code == 200, current.text
    current_body = current.json()
    assert current_body["status"] == "succeeded"
    assert current_body["stage"] == "completed"
    assert current_body["completed_section_count"] == 1
    assert current_body["outcome"] == "completed"
    assert current_body["requested_ai_model"] == DEFAULT_AI_TEXT_MODEL
    assert current_body["resolved_ai_model"] == fake.model_name
    assert current_body["can_cancel"] is False
    assert set(requested_models) == {DEFAULT_AI_TEXT_MODEL}


async def test_book_completion_job_persists_progress_failure_retry_and_cancel(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)

    idle = await _completion_field(client, project_id, "completion_job")
    assert idle["status"] == "idle"
    assert idle["job_id"] is None

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={
            "ai_model": "deepseek-v4-pro",
        },
    )

    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["status"] == "running"
    assert queued_body["total_chapter_count"] == 2
    assert queued_body["processed_chapter_count"] == 0
    assert queued_body["current_chapter_id"] == chapters[0].id
    assert queued_body["requested_ai_model"] == "deepseek-v4-pro"
    assert queued_body["can_cancel"] is True
    child_job_ids = [item["job_id"] for item in queued_body["chapters"]]
    assert all(child_job_ids)

    duplicate = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={},
    )

    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["job_id"] == queued_body["job_id"]
    assert [item["job_id"] for item in duplicate.json()["chapters"]] == child_job_ids
    batch_count = await test_db.scalar(select(func.count()).select_from(MemoirBookCompletionJob))
    assert batch_count == 1

    resumed = await _completion_field(client, project_id, "completion_job")
    assert resumed["job_id"] == queued_body["job_id"]
    assert [item["job_id"] for item in resumed["chapters"]] == child_job_ids

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, child_job_ids[0]) is True
    finally:
        clear_editing_job_handlers()

    partial = await _completion_field(client, project_id, "completion_job")
    assert partial["status"] == "running"
    assert partial["processed_chapter_count"] == 1
    assert partial["succeeded_chapter_count"] == 1
    assert partial["current_chapter_id"] == chapters[1].id

    failed_child = await test_db.get(MemoirEditingJob, child_job_ids[1])
    assert failed_child is not None
    failed_child.status = MemoirEditingJobStatus.FAILED.value
    failed_child.error_message = "provider unavailable"
    test_db.add(failed_child)
    await test_db.commit()

    failed_body = await _completion_field(client, project_id, "completion_job")
    assert failed_body["status"] == "failed"
    assert failed_body["processed_chapter_count"] == 2
    assert failed_body["succeeded_chapter_count"] == 1
    assert failed_body["failed_chapter_count"] == 1
    assert failed_body["can_cancel"] is False
    assert failed_body["can_retry"] is True
    assert failed_body["error_message"] == "provider unavailable"
    assert failed_body["chapters"][1]["error_message"] == "provider unavailable"
    failed_batch = await test_db.get(
        MemoirBookCompletionJob,
        queued_body["job_id"],
    )
    assert failed_batch is not None
    assert failed_batch.status == MemoirBookCompletionJobStatus.RUNNING.value
    assert failed_batch.active_key is not None
    assert failed_batch.completed_at is None

    settled = await client.delete(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/{queued_body['job_id']}"
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "failed"
    await test_db.refresh(failed_batch)
    assert failed_batch.status == MemoirBookCompletionJobStatus.FAILED.value
    assert failed_batch.active_key is None
    assert failed_batch.error_message == "provider unavailable"
    assert failed_batch.completed_at is not None

    retried = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={
            "retry_job_id": queued_body["job_id"],
        },
    )

    assert retried.status_code == 202, retried.text
    retried_body = retried.json()
    assert retried_body["job_id"] != queued_body["job_id"]
    assert retried_body["status"] == "running"
    assert retried_body["total_chapter_count"] == 1
    assert retried_body["chapters"][0]["chapter_id"] == chapters[1].id
    assert retried_body["chapters"][0]["job_id"] != child_job_ids[1]

    stale_cancel = await client.delete(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/{queued_body['job_id']}"
    )
    assert stale_cancel.status_code == 200, stale_cancel.text
    assert stale_cancel.json()["status"] == "failed"
    assert stale_cancel.json()["processed_chapter_count"] == 2
    assert [item["status"] for item in stale_cancel.json()["chapters"]] == [
        "succeeded",
        "failed",
    ]
    current_retry = await _completion_field(client, project_id, "completion_job")
    assert current_retry["job_id"] == retried_body["job_id"]
    assert current_retry["status"] == "running"

    cancelled = await client.delete(f"/api/memoir/projects/{project_id}/book-completion/jobs/{retried_body['job_id']}")

    assert cancelled.status_code == 200, cancelled.text
    cancelled_body = cancelled.json()
    assert cancelled_body["status"] == "cancelled"
    assert cancelled_body["processed_chapter_count"] == 1
    assert cancelled_body["cancelled_chapter_count"] == 1
    assert cancelled_body["can_retry"] is True
    stored_batch = await test_db.get(
        MemoirBookCompletionJob,
        retried_body["job_id"],
    )
    assert stored_batch is not None
    assert stored_batch.active_key is None


async def test_book_completion_overview_does_not_resume_or_update_job(
    client,
    test_db,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    batch = MemoirBookCompletionJob(
        project_id=project_id,
        active_key=f"book-completion:{project_id}",
        status=MemoirBookCompletionJobStatus.PENDING.value,
        target_chapters_json=[
            {"chapter_id": chapter.id, "title": chapter.title}
            for chapter in chapters
        ],
        chapter_job_ids_json={},
        enqueue_errors_json={},
    )
    test_db.add(batch)
    await test_db.commit()

    snapshot = await _completion_field(client, project_id, "completion_job")

    assert snapshot["job_id"] == batch.id
    assert snapshot["status"] == "pending"
    assert snapshot["processed_chapter_count"] == 0
    assert all(item["job_id"] is None for item in snapshot["chapters"])
    await test_db.refresh(batch)
    assert batch.status == MemoirBookCompletionJobStatus.PENDING.value
    assert batch.chapter_job_ids_json == {}
    assert batch.active_key == f"book-completion:{project_id}"
    child_count = await test_db.scalar(
        select(func.count())
        .select_from(MemoirEditingJob)
        .where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.PREPARE_CHAPTER.value,
        )
    )
    assert child_count == 0


async def test_book_completion_job_does_not_enqueue_after_concurrent_cancel(
    client,
    test_db,
    monkeypatch,
):
    project_id, _, _ = await _book(client, test_db, chapter_count=2)
    original_enqueue = chapter_preparation_jobs.enqueue_chapter_preparation_job
    cancellation_injected = False

    async def enqueue_then_cancel(*args, **kwargs):
        nonlocal cancellation_injected
        child = await original_enqueue(*args, **kwargs)
        if not cancellation_injected:
            cancellation_injected = True
            batch = await test_db.scalar(
                select(MemoirBookCompletionJob).where(
                    MemoirBookCompletionJob.project_id == project_id,
                )
            )
            assert batch is not None
            batch.status = MemoirBookCompletionJobStatus.CANCELLED.value
            batch.active_key = None
            test_db.add(batch)
            await test_db.commit()
        return child

    monkeypatch.setattr(
        book_completion_jobs.chapter_preparation_jobs,
        "enqueue_chapter_preparation_job",
        enqueue_then_cancel,
    )

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={},
    )

    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "cancelled"
    assert queued.json()["cancelled_chapter_count"] == 2
    child_jobs = (
        await test_db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type == MemoirEditingJobType.PREPARE_CHAPTER.value,
            )
        )
    ).all()
    assert len(child_jobs) == 1
    assert child_jobs[0].status == MemoirEditingJobStatus.SKIPPED.value


async def test_chapter_workspace_exposes_section_structure(
    client,
    test_db,
):
    project_id, chapters, sources = await _book(client, test_db)
    chapter = chapters[0]

    response = await client.get(f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server_revision"] >= 1
    section = body["sections"][0]
    assert section["title"] == chapter.title
    assert section["section_type"] == "life_fragment"
    assert section["material_count"] == 1
    assert section["included_material_count"] == 1
    assert section["materials"][0] == {
        "utterance_id": sources[0]["utterance_id"],
        "sort_order": 0,
        "text": "那年，我参加了第1次学校比赛。",
        "include_in_book": True,
    }


async def test_current_completion_issue_overrides_confirmed_draft(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    chapter_id = chapter.id
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    fake = LayeredBookAI(chapter_manual_issue=True)
    _install_ai(monkeypatch, fake)

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )

    assert prepared.status_code == 200, prepared.text
    summary = prepared.json()["completion_summary"]
    assert summary["state"] == "needs_input"
    assert summary["next_action"]["kind"] == "confirm"
    assert summary["active_issue"]["question"] == "这一章比赛年份的两种说法，哪一种准确？"
    rewritten = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter.id}/jobs",
        json={"operation": "rewrite"},
    )
    assert rewritten.status_code == 202
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(
            test_db,
            rewritten.json()["job_id"],
        ) is False
    finally:
        clear_editing_job_handlers()
    failed = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter_id}/jobs/current",
    )
    assert failed.json()["error_message"] == "本章还有一项需要您先确认"


async def test_chapter_review_filters_section_findings_before_rewrite(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    chapter_id = chapter.id
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    supplemental = await create_test_voice_transcript(
        client,
        test_db,
        "这是后来补充的一段背景。",
        project_id=project_id,
    )
    await add_test_section_materials(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        source_refs=[memoir_source_ref(supplemental)],
    )
    chapter.title = "离乡后的成长"
    test_db.add(chapter)
    await test_db.commit()
    test_db.expire_all()
    fake = LayeredBookAI(section_issue=True)
    _install_ai(monkeypatch, fake)

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
    )

    assert prepared.status_code == 200, prepared.text
    workspace = prepared.json()
    assert workspace["completion_summary"]["state"] == "draft_ready"
    assert workspace["draft_needs_rewrite"] is False
    assert workspace["draft_content"].startswith("那年，我参加了学校比赛")
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.chapter_id == chapter_id,
        )
    )
    assert issue is None
    assert any("这件事后来对你有什么影响" in prompt for prompt in fake.prompts if "请通读回忆录章节" in prompt)


async def test_explicit_rewrite_refreshes_all_sections_once(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    chapter_id = chapter.id
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    added_sources = [
        await create_test_voice_transcript(
            client,
            test_db,
            f"这是第 {index + 2} 个小节的讲述。",
            project_id=project_id,
        )
        for index in range(3)
    ]
    for index, source in enumerate(added_sources, start=1):
        section = MemoirSection(
            project_id=project_id,
            chapter_id=chapter_id,
            title=f"新小节 {index + 1}",
            section_type="event",
            sort_order=index,
        )
        test_db.add(section)
        await test_db.flush()
        test_db.add(
            MemoirSectionMaterial(
                project_id=project_id,
                section_id=section.id,
                utterance_id=source["utterance_id"],
                transcript_version_id=source["transcript_version_id"],
                sort_order=0,
                assignment_reason="four_section_rewrite_regression",
            )
        )
    await test_db.commit()
    test_db.expire_all()
    fake = LayeredBookAI()
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)

    rewritten = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        operation="rewrite",
        ai_model=DEFAULT_AI_TEXT_MODEL,
    )

    assert rewritten.status_code == 200, rewritten.text
    body = rewritten.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert len(body["sections"]) == 4
    assert len(body["draft_sections"]) == 4
    assert [item["section_id"] for item in body["draft_sections"]] == [item["id"] for item in body["sections"]]
    assert len(fake.prompts) == 4
    assert all("请把下面的原始讲述整理为回忆录章节" in prompt for prompt in fake.prompts)
    assert all(prompt.count("material_id: ") == 1 for prompt in fake.prompts)
    assert body["draft_sections"][0]["content"] != "这是已经存在的完整章节正文。"
    assert requested_models == [DEFAULT_AI_TEXT_MODEL]


@pytest.mark.parametrize("finalize_action", ["apply", "dismiss", "stale"])
async def test_section_revision_waits_for_apply_and_preserves_other_content(
    client,
    test_db,
    monkeypatch,
    finalize_action,
):
    project_id = await ensure_mobile_memoir_project(client)
    sources = [await create_test_voice_transcript(
        client, test_db, "那年，我参加了学校比赛。", project_id=project_id,
    )]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db, project_id=project_id,
        chapter_titles=["学校里的比赛", "尚未讲述的章节一", "尚未讲述的章节二"],
        source_refs_by_chapter=[[memoir_source_ref(sources[0])], [], []],
    )
    chapter = chapters[0]
    added_source = await create_test_voice_transcript(
        client,
        test_db,
        "比赛结束后，我一个人在操场边想了很久。",
        project_id=project_id,
    )
    target_section = MemoirSection(
        project_id=project_id,
        chapter_id=chapter.id,
        title="比赛后的回想峰",
        section_type="viewpoint_reflection",
        sort_order=1,
    )
    test_db.add(target_section)
    await test_db.flush()
    test_db.add(
        MemoirSectionMaterial(
            project_id=project_id,
            section_id=target_section.id,
            utterance_id=added_source["utterance_id"],
            transcript_version_id=added_source["transcript_version_id"],
            sort_order=0,
            assignment_reason="single_section_regeneration_regression",
        )
    )
    await test_db.commit()
    _, version = await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    context = await chapter_generation.load_chapter_generation_context(
        test_db, project_id=project_id, chapter_id=chapter.id,
    )
    version.prompt_snapshot = chapter_generation._render_policy_tag(context)
    version.content = "\n\n".join(item["content"] for item in version.section_snapshot_json)
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, parent.id)
    finally:
        clear_editing_job_handlers()
    await test_db.refresh(parent)
    assert parent.status == MemoirEditingJobStatus.SUCCEEDED.value
    original_output = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    assert original_output is not None
    original_output_id, original_output_version = original_output.id, original_output.version
    original_snapshot = original_output.chapter_snapshot_json[0]
    assert original_snapshot["draft_version_id"] == version.id

    fake = LayeredBookAI(
        section_revision_title="赛后回想",
        section_revision_content="这是已经存在的完整章节正文。",
    )
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)

    base_path = (
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}"
        f"/draft-sections/{target_section.id}/revision"
    )
    queued = await client.post(
        f"{base_path}/jobs",
        json={
            "operation": "plan",
            "ai_model": DEFAULT_AI_TEXT_MODEL,
            "feedback": "把标题中的错字改正，并把标题缩短为“赛后回想”。",
        },
    )
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, queued.json()["job_id"])
    finally:
        clear_editing_job_handlers()
    proposal_id = queued.json()["job_id"]
    current = await client.get(f"{base_path}/jobs/current")
    assert current.json()["proposal_id"] == proposal_id
    plan = await client.get(f"{base_path}/proposals/{proposal_id}")
    assert plan.status_code == 200, plan.text
    assert plan.json()["phase"] == "plan_ready"
    assert plan.json()["can_apply"] is False
    assert plan.json()["revision_plan"]["book_metadata_patch"] == {}
    assert await test_db.scalar(
        select(func.count(MemoirBookChapterDraftVersion.id)).where(
            MemoirBookChapterDraftVersion.draft_id == version.draft_id
        )
    ) == 1

    feedback = await client.post(
        f"{base_path}/jobs",
        json={
            "operation": "feedback",
            "proposal_id": proposal_id,
            "feedback": "标题就用“赛后回想”。",
        },
    )
    assert feedback.status_code == 202, feedback.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, feedback.json()["job_id"])
    finally:
        clear_editing_job_handlers()
    refined = await client.get(f"{base_path}/proposals/{proposal_id}")
    assert refined.json()["phase"] == "plan_ready"
    assert refined.json()["summary"] == "保留两段经历，统一称谓并适当精简。"

    draft = await client.post(
        f"{base_path}/jobs",
        json={"operation": "draft", "proposal_id": proposal_id},
    )
    assert draft.status_code == 202, draft.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, draft.json()["job_id"])
    finally:
        clear_editing_job_handlers()
    preview = await client.get(f"{base_path}/proposals/{proposal_id}")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["phase"] == "draft_ready"
    assert body["can_apply"] is True
    comparison = body["comparison"]["chapters"][0]["sections"][0]
    assert comparison["before_title"] == "比赛后的回想峰"
    assert comparison["after_title"] == "赛后回想"
    assert comparison["before_content"] == comparison["after_content"]
    assert await test_db.scalar(
        select(func.count(MemoirBookChapterDraftVersion.id)).where(
            MemoirBookChapterDraftVersion.draft_id == version.draft_id
        )
    ) == 1

    if finalize_action == "stale":
        target_section.version += 1
        await test_db.commit()
        stale = await client.get(f"{base_path}/proposals/{proposal_id}")
        assert stale.json()["status"] == "stale"
        assert stale.json()["can_apply"] is False
    action = "dismiss" if finalize_action == "dismiss" else "apply"
    applied = await client.post(f"{base_path}/proposals/{proposal_id}/{action}")
    if finalize_action != "apply":
        assert applied.status_code == (200 if action == "dismiss" else 409), applied.text
        if action == "dismiss":
            assert applied.json()["status"] == "dismissed"
        await test_db.refresh(parent)
        assert parent.status == MemoirEditingJobStatus.SUCCEEDED.value
        assert parent.result_json["output_version_id"] == original_output_id
        assert await test_db.scalar(select(func.count(MemoirEditingJob.id)).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.UPDATE_BOOK_DRAFT.value,
        )) == 1
        assert await test_db.scalar(select(func.count(MemoirBookChapterDraftVersion.id)).where(
            MemoirBookChapterDraftVersion.draft_id == version.draft_id,
        )) == 1
        return
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    assert (await client.get(f"{base_path}/jobs/current")).json()["status"] == "idle"
    latest = await test_db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.draft_id == version.draft_id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
    )
    assert latest is not None
    assert latest.version == 2
    assert latest.section_snapshot_json[0]["content"] == "这是已经存在的完整章节正文。"
    assert latest.section_snapshot_json[1]["title"] == "赛后回想"
    assert latest.section_snapshot_json[1]["content"] == "这是已经存在的完整章节正文。"
    assert latest.prompt_snapshot.endswith(":section_revision_workbench")
    await test_db.refresh(target_section)
    assert target_section.title == "赛后回想"
    assert target_section.version == 2
    draft = await test_db.get(MemoirBookChapterDraft, version.draft_id)
    assert draft is not None
    assert draft.metadata_json["last_requested_ai_model"] == DEFAULT_AI_TEXT_MODEL
    assert draft.metadata_json["last_resolved_ai_model"] == "test-layered-book"
    assert sources[0]["utterance_id"] not in fake.prompts[-1]
    assert added_source["utterance_id"] not in fake.prompts[-1]
    assert requested_models == [
        DEFAULT_AI_TEXT_MODEL,
        DEFAULT_AI_TEXT_MODEL,
        DEFAULT_AI_TEXT_MODEL,
    ]

    await test_db.refresh(parent)
    assert parent.status == MemoirEditingJobStatus.PENDING.value
    assert parent.result_json["output_version_id"] == original_output_id
    assert await test_db.scalar(select(func.count(MemoirEditingJob.id)).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.UPDATE_BOOK_DRAFT.value,
    )) == 1
    latest_id, latest_content = latest.id, latest.content
    assert latest.used_material_ids_json == version.used_material_ids_json
    calls = len(fake.prompts)
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, parent.id)
    finally:
        clear_editing_job_handlers()
    assert len(fake.prompts) == calls
    await test_db.refresh(parent)
    assert parent.result_json["chapters"][chapter.id]["draft_version_id"] == latest_id
    assert all(parent.result_json["chapters"][empty.id]["status"] == "needs_material" for empty in chapters[1:])
    refreshed_output = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    assert refreshed_output is not None
    assert refreshed_output.version == original_output_version + 1
    assert len(refreshed_output.chapter_snapshot_json) == 1
    snapshot = refreshed_output.chapter_snapshot_json[0]
    assert snapshot["draft_version_id"] == latest_id
    assert snapshot["content"] == latest_content
    assert snapshot["sections"][0] == original_snapshot["sections"][0]
    assert snapshot["sections"][1]["title"] == "赛后回想"
    assert snapshot["source_refs"] == original_snapshot["source_refs"]
    assert await test_db.scalar(select(func.count(MemoirBookChapterDraftVersion.id)).where(
        MemoirBookChapterDraftVersion.project_id == project_id,
    )) == 2
    output = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert output.status_code == 200, output.text
    assert output.json()["is_stale"] is False


async def test_book_overview_counts_ready_drafts_as_complete(
    client,
    test_db,
    monkeypatch,
):
    project_id, _, _ = await _book(client, test_db, chapter_count=3)
    states = iter(("not_started", "needs_input", "draft_ready"))

    async def fake_chapter_summary(db, *, project_id, chapter, snapshot):
        state = next(states)
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state=state,
            next_action=MemoirChapterCompletionNextAction(
                kind="prepare",
                title="下一步",
                detail="测试",
            ),
            headline="测试",
            detail="测试",
        )

    monkeypatch.setattr(
        completion_read_model,
        "chapter_summary",
        fake_chapter_summary,
    )

    overview = await completion_read_model.book_overview(
        test_db,
        project_id=project_id,
    )

    assert overview.completed_chapters == 1
    assert overview.headline == "还有 2 章未完成"
    assert overview.detail == "1 章待检查，1 章待确认。每章只显示当前最重要的一步。"


async def test_book_completion_uses_background_chapter_preparation(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI(chapter_interview_issue=True)
    _install_ai(monkeypatch, fake)

    initial = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert initial.status_code == 200
    assert initial.json()["chapters"][0]["state"] == "not_started"

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert body["draft_content"].startswith("那年，我参加了学校比赛")
    assert len(body["draft_sections"]) == 1
    assert body["draft_sections"][0]["content"] == body["draft_content"]
    assert body["draft_sections"][0]["used_material_ids"]
    original_version_id = body["draft_version_id"]
    preparation_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.PREPARE_CHAPTER.value,
        )
    )
    assert preparation_job is not None
    assert preparation_job.status == MemoirEditingJobStatus.SUCCEEDED.value

    revised = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/revise",
        json={
            "expected_version_id": original_version_id,
            "sections": [
                {
                    "section_id": body["draft_sections"][0]["section_id"],
                    "content": ("那年，我参加了学校比赛。这件事至今仍留在记忆里。"),
                }
            ],
        },
    )
    assert revised.status_code == 200, revised.text
    revised_version = await test_db.get(
        MemoirBookChapterDraftVersion,
        revised.json()["latest_version"]["id"],
    )
    assert revised_version is not None
    assert revised.json()["latest_version"]["sections"][0]["content"].endswith("这件事至今仍留在记忆里。")
    assert revised_version.input_signature
    assert revised_version.used_material_ids_json

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(MemoirCompletionIssue)
            .where(
                MemoirCompletionIssue.project_id == project_id,
                MemoirCompletionIssue.status == "open",
            )
        )
        == 1
    )
    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert overview.json()["chapters"][0]["state"] == "confirmed"


async def test_chapter_reorder_preserves_checks_and_confirmed_drafts(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(
        client,
        test_db,
        chapter_count=2,
    )
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)

    for chapter in chapters:
        prepared = await _run_chapter_preparation(
            client,
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
        assert prepared.status_code == 200, prepared.text
        confirmed = await client.post(
            f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
        )
        assert confirmed.status_code == 200, confirmed.text

        version = await test_db.scalar(
            select(MemoirBookChapterDraftVersion)
            .where(
                MemoirBookChapterDraftVersion.project_id == project_id,
                MemoirBookChapterDraftVersion.chapter_id == chapter.id,
            )
            .order_by(MemoirBookChapterDraftVersion.version.desc())
        )
        assert version is not None
        version.input_signature = f"legacy-order-{chapter.sort_order}"
        test_db.add(version)
    await test_db.commit()
    reviewed = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
    )
    assert reviewed.status_code == 200, reviewed.text
    prompt_count = len(fake.prompts)

    moved = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "move_chapter",
            "chapter_id": chapters[1].id,
            "direction": "up",
        },
    )
    assert moved.status_code == 200, moved.text

    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert overview.status_code == 200, overview.text
    assert [item["chapter_id"] for item in overview.json()["chapters"]] == [chapters[1].id, chapters[0].id]
    assert [item["state"] for item in overview.json()["chapters"]] == ["confirmed", "confirmed"]
    for chapter in chapters:
        workspace = await client.get(f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace")
        assert workspace.status_code == 200, workspace.text
        assert workspace.json()["draft_needs_rewrite"] is False
    current_review = await _completion_field(
        client,
        project_id,
        "book_review",
    )
    assert current_review is None
    assert len(fake.prompts) == prompt_count


async def test_section_revision_rejects_changed_section_boundary(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    _install_ai(monkeypatch, LayeredBookAI())
    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()

    revised = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/revise",
        json={
            "expected_version_id": body["draft_version_id"],
            "sections": [
                {
                    "section_id": "another-section",
                    "content": "不应保存的正文",
                }
            ],
        },
    )

    assert revised.status_code == 409
    assert "小节已经变化" in revised.json()["detail"]


async def test_legacy_flat_draft_requires_rewrite_before_confirmation(
    client,
    test_db,
):
    project_id, chapters, _ = await _book(client, test_db)
    draft, version = await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.DRAFT,
    )
    version.section_snapshot_json = []
    test_db.add(version)
    await test_db.commit()

    workspace_response = await client.get(f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/workspace")
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["draft_needs_rewrite"] is True
    assert workspace["can_rewrite_draft"] is True
    assert workspace["draft_sections"] == []

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[0].id}/draft/confirm"
    )
    assert confirmed.status_code == 409
    assert "没有小节结构" in confirmed.json()["detail"]
    assert draft.status == MemoirBookChapterDraftStatus.DRAFT.value


async def test_chapter_manual_issue_is_one_explicit_next_action(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI(chapter_manual_issue=True)
    _install_ai(monkeypatch, fake)

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    assert prepared.status_code == 200
    summary = prepared.json()["completion_summary"]
    assert summary["state"] == "needs_input"
    assert summary["next_action"]["kind"] == "confirm"
    assert summary["active_issue"]["question"] == "这一章比赛年份的两种说法，哪一种准确？"


async def test_book_review_uses_chapter_text_and_creates_book_issue(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    fake = LayeredBookAI(book_issue=True)
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)
    bootstrapped = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert bootstrapped.status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )
    unreviewed = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble",
    )
    assert unreviewed.status_code == 200
    assert len(unreviewed.json()["chapter_snapshot_json"]) == 2

    generated = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
        ai_model=DEFAULT_AI_TEXT_MODEL,
    )
    assert generated.status_code == 200
    review = generated.json()

    assert review["status"] == "ready"
    assert set(requested_models) == {DEFAULT_AI_TEXT_MODEL}
    book_prompt = next(prompt for prompt in fake.prompts if "请通读下面这本回忆录" in prompt)
    assert all(chapter.id not in book_prompt for chapter in chapters)
    assert "chapter_id: C1" in book_prompt
    assert "这是已经存在的完整章节正文。" in book_prompt
    assert review["review"]["summary"] == "全书记录了几段重要成长经历。"
    assert "candidate_issues" not in review["review"]
    assert "generation" not in review["review"]
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.scope == MemoirCompletionIssueScope.BOOK.value,
        )
    )
    assert issue is not None
    issue_id = issue.id
    assert issue.channel == "manual_review"
    assert issue.related_material_ids_json == []
    assert issue.related_chapter_ids_json == [chapter.id for chapter in chapters]
    current = await _completion_field(client, project_id, "book_review")
    assert current["id"] == review["id"]
    still_readable = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble",
    )
    assert still_readable.status_code == 200
    assert len(still_readable.json()["chapter_snapshot_json"]) == 2
    closed = await client.post(
        f"/api/memoir/projects/{project_id}/completion-issues/{issue_id}/close",
        json={"outcome": "confirmed"},
    )
    assert closed.status_code == 200
    assembled = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble",
    )
    assert assembled.status_code == 200
    assert (
        await test_db.scalar(select(MemoirBookReview.id).where(MemoirBookReview.project_id == project_id))
        == review["id"]
    )
    first_version = await chapter_generation.latest_chapter_draft_version(
        test_db, project_id=project_id, chapter_id=chapters[0].id,
    )
    assert first_version is not None
    test_db.add(MemoirBookChapterDraftVersion(
        draft_id=first_version.draft_id,
        project_id=project_id,
        chapter_id=chapters[0].id,
        version=first_version.version + 1,
        content=first_version.content,
    ))
    await test_db.flush()
    assert (await book_reviews.load_book_review_context(
        test_db, project_id=project_id,
    )).input_signature == review["input_signature"]


async def test_book_review_job_persists_across_requests_and_runs_in_worker(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    fake = LayeredBookAI(book_issue=True)
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)
    bootstrapped = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert bootstrapped.status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )

    idle = await _completion_field(client, project_id, "book_review_job")
    assert idle["status"] == "idle"

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={
            "force_regenerate": False,
            "ai_model": DEFAULT_AI_TEXT_MODEL,
        },
    )

    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["status"] == "pending"
    assert queued_body["stage"] == "queued"
    assert queued_body["total_chapter_count"] == 2
    assert queued_body["processed_chapter_count"] == 0
    assert queued_body["can_cancel"] is True
    assert (
        await test_db.scalar(
            select(func.count()).select_from(MemoirBookReview).where(MemoirBookReview.project_id == project_id)
        )
        == 0
    )

    duplicate = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={"force_regenerate": False},
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["job_id"] == queued_body["job_id"]
    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(MemoirEditingJob)
            .where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_BOOK_REVIEW.value,
            )
        )
        == 1
    )

    resumed = await _completion_field(client, project_id, "book_review_job")
    assert resumed["job_id"] == queued_body["job_id"]
    assert resumed["stage"] == "queued"

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                queued_body["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()

    completed_body = await _completion_field(
        client,
        project_id,
        "book_review_job",
    )
    assert completed_body["status"] == "succeeded"
    assert completed_body["stage"] == "completed"
    assert completed_body["processed_chapter_count"] == 2
    assert completed_body["review_id"]
    assert completed_body["can_cancel"] is False
    assert completed_body["requested_ai_model"] == DEFAULT_AI_TEXT_MODEL
    assert completed_body["resolved_ai_model"] == fake.model_name
    assert set(requested_models) == {DEFAULT_AI_TEXT_MODEL}

    snapshot = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert snapshot.status_code == 200
    snapshot_body = snapshot.json()
    assert snapshot_body["book_review"]["id"] == completed_body["review_id"]
    assert snapshot_body["book_review"]["status"] == "ready"
    assert snapshot_body["book_review_job"]["status"] == "succeeded"
    assert snapshot_body["book_review_job"]["review_id"] == completed_body["review_id"]
    assert len(snapshot_body["book_issues"]) == 1
    assert snapshot_body["book_output_available"] is False
    assert snapshot_body["revision_job"]["unresolved_count"] == 1
    assert snapshot_body["revision_job"]["status"] == "idle"

    requeued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={"force_regenerate": True},
    )
    assert requeued.status_code == 202
    assert requeued.json()["job_id"] == queued_body["job_id"]
    assert requeued.json()["status"] == "pending"
    cancelled = await client.delete(f"/api/memoir/projects/{project_id}/book-review/jobs/{queued_body['job_id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["can_retry"] is True


async def test_unrelated_book_revision_can_proceed_while_conflict_chapter_is_omitted(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=3)
    fake = LayeredBookAI(book_actions=True)
    _install_ai(monkeypatch, fake)
    assert (await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")).status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db, project_id=project_id, chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )
    # Review the actual book metadata before testing an unrelated omission.
    assert (await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")).status_code == 200
    assert (await client.patch(f"/api/mobile/memoir/projects/{project_id}/book-output", json={
        'subtitle': '这些往事', 'narrator_name': '口述人', 'prepared_by': '家人',
        'preface': '家人留下的回忆。', 'afterword': '这些故事留给后人。', 'back_cover_quote': '听家人慢慢讲。',
    })).status_code == 200
    generated = await _run_book_review(client, test_db, project_id=project_id)
    assert generated.status_code == 200, generated.text
    review = await test_db.get(MemoirBookReview, generated.json()["id"])
    assert review is not None
    test_db.add(MemoirCompletionIssue(
        project_id=project_id,
        scope="book",
        issue_type="conflict",
        channel="manual_review",
        question="第三章这段可识别的经历是否适合入书？",
        priority="high",
        fingerprint="f" * 64,
        input_signature=review.input_signature,
        related_chapter_ids_json=[chapters[2].id],
    ))
    await test_db.commit()

    assembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert assembled.status_code == 200, assembled.text
    assert assembled.json()["omitted_chapter_ids"] == [chapters[2].id]
    assert [item["chapter_id"] for item in assembled.json()["chapter_snapshot_json"]] == [
        chapter.id for chapter in chapters[:2]
    ]

    action = generated.json()["review"]["editorial_actions"][0]
    assert action["chapter_ids"] == [chapter.id for chapter in chapters[:2]]
    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision/jobs",
        json={"include_answers": False, "action_ids": [action["id"]]},
    )
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, queued.json()["job_id"])
    finally:
        clear_editing_job_handlers()
    completed = await _completion_field(client, project_id, "revision_job")
    assert completed["status"] == "succeeded"
    assert completed["proposal_status"] == "ready"


async def test_book_review_selection_plan_feedback_draft_and_apply(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(
        client,
        test_db,
        chapter_count=3,
    )
    fake = LayeredBookAI(book_issue=True, book_actions=True)
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)
    assert (await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")).status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )

    assert (await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")).status_code == 200
    generated = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
    )
    assert generated.status_code == 200, generated.text
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.scope == MemoirCompletionIssueScope.BOOK.value,
        )
    )
    assert issue is not None
    blocked_revision = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision/jobs",
        json={
            "include_answers": False,
            "action_ids": [
                action["id"]
                for action in generated.json()["review"]["editorial_actions"]
            ],
        },
    )
    assert blocked_revision.status_code == 409
    assert "先处理所选章节需要确认的问题" in blocked_revision.json()["detail"]
    blocked_state = await _completion_field(
        client,
        project_id,
        "revision_job",
    )
    assert blocked_state["answer_count"] == 0
    assert blocked_state["unresolved_count"] == 1
    closed = await client.post(
        f"/api/memoir/projects/{project_id}/completion-issues/{issue.id}/close",
        json={
            "outcome": "confirmed",
            "note": "两段经历发生在不同阶段，我希望都保留。",
        },
    )
    assert closed.status_code == 200

    unapplied = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert unapplied.status_code == 200, unapplied.text

    idle = await _completion_field(client, project_id, "revision_job")
    assert idle["status"] == "idle"
    assert idle["answer_count"] == 1
    action_id = generated.json()["review"]["editorial_actions"][0]["id"]

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision/jobs",
        json={
            "include_answers": True,
            "action_ids": [],
            "ai_model": DEFAULT_AI_TEXT_MODEL,
        },
    )
    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["status"] == "pending"
    assert queued_body["stage"] == "queued"
    assert queued_body["include_answers"] is True
    assert queued_body["answer_count"] == 1
    assert queued_body["action_ids"] == []
    assert queued_body["selected_action_count"] == 0

    cancelled = await client.delete(
        f"/api/memoir/projects/{project_id}/book-review/revision/jobs/{queued_body['job_id']}"
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    retried = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision/jobs",
        json={
            "include_answers": True,
            "action_ids": [action_id],
            "ai_model": DEFAULT_AI_TEXT_MODEL,
        },
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["job_id"] == queued_body["job_id"]
    assert retried.json()["action_ids"] == [action_id]
    assert retried.json()["selected_action_count"] == 1

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                queued_body["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()

    completed_body = await _completion_field(
        client,
        project_id,
        "revision_job",
    )
    assert completed_body["status"] == "succeeded"
    assert completed_body["stage"] == "completed"
    assert completed_body["answer_count"] == 1
    assert completed_body["action_ids"] == [action_id]
    assert completed_body["selected_action_count"] == 1
    assert completed_body["changed_chapter_count"] == 0
    assert completed_body["proposal_id"]
    assert completed_body["proposal_status"] == "ready"
    assert completed_body["proposal_phase"] == "plan_ready"
    assert completed_body["resolved_ai_model"] == fake.model_name
    assert "第1章" in completed_body["summary"]
    assert "第1小节" in completed_body["summary"]
    assert "C1" not in completed_body["summary"]
    assert "S1" not in completed_body["summary"]
    assert set(requested_models) == {None, DEFAULT_AI_TEXT_MODEL}

    versions = list(
        (
            await test_db.scalars(
                select(MemoirBookOutputVersion)
                .where(MemoirBookOutputVersion.project_id == project_id)
                .order_by(MemoirBookOutputVersion.version)
            )
        ).all()
    )
    assert len(versions) == 1
    assert "我决定都保留在书里" not in versions[0].content

    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}?preview=none"
    )
    assert proposal.status_code == 200, proposal.text
    proposal_body = proposal.json()
    assert proposal_body["status"] == "ready"
    assert proposal_body["phase"] == "plan_ready"
    assert proposal_body["can_apply"] is False
    assert proposal_body["can_dismiss"] is True
    assert proposal_body["comparison"]["changed_chapter_count"] == 0
    assert proposal_body["comparison"]["changed_section_count"] == 0
    visible_plan = json.dumps(proposal_body["revision_plan"], ensure_ascii=False)
    assert "第1章" in visible_plan
    assert "第1小节" in visible_plan
    assert "C1" not in visible_plan
    assert "S1" not in visible_plan
    assert proposal_body["before"]["manuscript"] is None
    assert proposal_body["after"]["manuscript"] is None
    before_preview = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=before"
    )
    after_preview = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=after"
    )
    before_content = before_preview.json()["before"]["manuscript"]["chapters"][0]["content"]
    assert before_preview.json()["after"]["manuscript"] is None
    assert after_preview.json()["before"]["manuscript"] is None
    assert after_preview.json()["after"]["manuscript"] is None
    assert "我决定都保留在书里" not in before_content

    feedback_job = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}/feedback/jobs",
        json={
            "feedback": "保留两段经历，但请统一相关人物的称谓。",
            "ai_model": DEFAULT_AI_TEXT_MODEL,
        },
    )
    assert feedback_job.status_code == 202, feedback_job.text
    feedback_job_body = feedback_job.json()
    assert feedback_job_body["status"] == "pending"
    assert feedback_job_body["proposal_id"] == completed_body["proposal_id"]
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        feedback_succeeded = await run_test_editing_job(
            test_db,
            feedback_job_body["job_id"],
        )
        failed_feedback_job = await test_db.get(
            MemoirEditingJob,
            feedback_job_body["job_id"],
        )
        assert feedback_succeeded is True, failed_feedback_job.error_message
    finally:
        clear_editing_job_handlers()
    refined = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}/feedback/jobs/current"
    )
    assert refined.status_code == 200, refined.text
    assert refined.json()["status"] == "succeeded"
    assert refined.json()["changed_chapter_count"] == 0
    assert refined.json()["proposal_phase"] == "plan_ready"
    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=after"
    )
    assert proposal.status_code == 200, proposal.text
    proposal_body = proposal.json()
    assert proposal_body["id"] == completed_body["proposal_id"]
    assert proposal_body["phase"] == "plan_ready"
    assert proposal_body["after"]["manuscript"] is None
    assert "老同学" in (proposal_body["revision_plan"]["items"][0]["approach"])
    versions = list(
        (
            await test_db.scalars(
                select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.project_id == project_id)
            )
        ).all()
    )
    assert len(versions) == 1

    draft_job = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/draft/jobs",
        json={"ai_model": DEFAULT_AI_TEXT_MODEL},
    )
    assert draft_job.status_code == 202, draft_job.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                draft_job.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    drafted = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}/draft/jobs/current"
    )
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["status"] == "succeeded"
    assert drafted.json()["proposal_phase"] == "draft_ready"
    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=after"
    )
    assert proposal.json()["can_apply"] is True
    assert "老同学" in (proposal.json()["after"]["manuscript"]["chapters"][0]["content"])

    second_feedback = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}/feedback/jobs",
        json={"feedback": "再精简一些。"},
    )
    assert second_feedback.status_code == 202, second_feedback.text
    assert second_feedback.json()["job_id"] == feedback_job_body["job_id"]
    assert second_feedback.json()["status"] == "pending"
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                feedback_job_body["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    replanned = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=after"
    )
    assert replanned.json()["phase"] == "plan_ready"
    assert replanned.json()["can_apply"] is False
    assert replanned.json()["after"]["manuscript"] is None

    second_draft = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/draft/jobs",
        json={},
    )
    assert second_draft.status_code == 202, second_draft.text
    assert second_draft.json()["job_id"] == draft_job.json()["job_id"]
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                second_draft.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()

    review_record = await test_db.get(MemoirBookReview, generated.json()["id"])
    assert review_record is not None
    review_record.status = "failed"
    test_db.add(review_record)
    await test_db.commit()
    stale = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}"
    )
    assert stale.status_code == 200
    assert stale.json()["status"] == "stale"
    assert stale.json()["can_apply"] is False
    rejected = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/apply"
    )
    assert rejected.status_code == 409
    review_record.status = "ready"
    test_db.add(review_record)
    await test_db.commit()

    applied = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/apply"
    )
    assert applied.status_code == 200, applied.text
    applied_body = applied.json()
    assert applied_body["status"] == "applied"
    assert applied_body["can_apply"] is False
    assert applied_body["result_output_version_id"]
    applied_state = await _completion_field(client, project_id, "revision_job")
    assert applied_state["already_applied"] is True

    versions = list(
        (
            await test_db.scalars(
                select(MemoirBookOutputVersion)
                .where(MemoirBookOutputVersion.project_id == project_id)
                .order_by(MemoirBookOutputVersion.version)
            )
        ).all()
    )
    assert len(versions) == 2
    assert "老同学" in versions[1].content
    assert versions[1].created_by == "ai"
    assert [item["chapter_id"] for item in versions[1].chapter_snapshot_json] == [chapter.id for chapter in chapters]
    assert versions[1].chapter_snapshot_json[1:] == versions[0].chapter_snapshot_json[1:]
    plan_prompt = next(
        prompt
        for prompt in fake.prompts
        if "请根据用户选中的问题回答和编辑建议" in prompt
    )
    assert "两段经历发生在不同阶段，我希望都保留" in plan_prompt
    assert all(chapter.id not in plan_prompt for chapter in chapters)
    draft_prompt = next(prompt for prompt in fake.prompts if "请按照已确认的修改思路生成书稿修改稿" in prompt)
    assert "统一称谓并精简重复" in draft_prompt
    assert all(chapter.id not in draft_prompt for chapter in chapters)
    assert "不改变章节和小节的标题、数量" in draft_prompt

async def test_selected_editorial_actions_create_a_preview_before_applying(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    fake = LayeredBookAI(
        book_actions=True,
        book_profile_action=True,
    )
    _install_ai(monkeypatch, fake)
    bootstrapped = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert bootstrapped.status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )

    assert (await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")).status_code == 200
    generated = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
    )
    assert generated.status_code == 200, generated.text
    actions = generated.json()["review"]["editorial_actions"]
    assert {action["action_type"] for action in actions} == {
        "remove_repetition",
        "improve_transition",
        "reorder_chapters",
        "revise_book_profile",
    }
    assembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert assembled.status_code == 200, assembled.text
    baseline_version_id = assembled.json()["output_version_id"]

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision/jobs",
        json={
            "include_answers": False,
            "action_ids": [action["id"] for action in actions],
        },
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["selected_action_count"] == 4
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                queued.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()

    completed_body = await _completion_field(
        client,
        project_id,
        "revision_job",
    )
    assert completed_body["status"] == "succeeded"
    assert completed_body["proposal_status"] == "ready"
    assert completed_body["proposal_phase"] == "plan_ready"
    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(MemoirBookOutputVersion)
            .where(MemoirBookOutputVersion.project_id == project_id)
        )
        == 1
    )

    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}"
    )
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["can_apply"] is False
    assert proposal.json()["revision_plan"]["items"]
    assert proposal.json()["revision_plan"]["book_metadata_patch"] == {
        "preface": "写下这些故园往事，是为了留住家人的声音。",
        "afterword": "故事写到这里，故园的风仍在记忆里。",
        "back_cover_quote": "把故园与家人的声音，留在这本书里。",
    }
    draft = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/draft/jobs",
        json={},
    )
    assert draft.status_code == 202, draft.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                draft.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}"
    )
    comparison = proposal.json()["comparison"]
    assert comparison["chapter_order_changed"] is True
    assert [item["chapter_id"] for item in comparison["after_chapter_order"]] == [chapters[1].id, chapters[0].id]
    assert comparison["changed_section_count"] == 1
    assert {
        item["field"]: item["after_value"]
        for item in comparison["metadata_changes"]
    } == {
        "preface": "写下这些故园往事，是为了留住家人的声音。",
        "afterword": "故事写到这里，故园的风仍在记忆里。",
        "back_cover_quote": "把故园与家人的声音，留在这本书里。",
    }
    assert proposal.json()["before"]["version"] == 1
    assert proposal.json()["after"]["version"] == 2

    applied = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/apply"
    )
    assert applied.status_code == 200, applied.text
    applied_version_id = applied.json()["result_output_version_id"]
    versions = list(
        (
            await test_db.scalars(
                select(MemoirBookOutputVersion)
                .where(MemoirBookOutputVersion.project_id == project_id)
                .order_by(MemoirBookOutputVersion.version)
            )
        ).all()
    )
    assert [version.id for version in versions] == [
        baseline_version_id,
        applied_version_id,
    ]
    assert [item["chapter_id"] for item in versions[-1].chapter_snapshot_json] == [chapters[1].id, chapters[0].id]
    assert "老同学" in versions[-1].content
    preface = next(
        section
        for section in versions[-1].manuscript_json["front_matter"]
        if section["kind"] == "preface"
    )
    assert preface["content"] == "写下这些故园往事，是为了留住家人的声音。"
    assert versions[-1].manuscript_json["back_matter"]["afterword"]["content"] == (
        "故事写到这里，故园的风仍在记忆里。"
    )
    assert versions[-1].manuscript_json["render_settings"]["back_cover_quote"] == (
        "把故园与家人的声音，留在这本书里。"
    )
    state = await _completion_field(client, project_id, "revision_job")
    assert state["already_applied"] is False


async def test_book_review_job_preserves_failure_and_can_retry(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )

    async def fail_review(*args, progress_callback=None, **kwargs):
        assert progress_callback is not None
        await progress_callback(
            {
                "stage": "reviewing",
                "total_chapter_count": 1,
                "processed_chapter_count": 1,
                "current_chapter_title": "",
            }
        )
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        book_review_jobs.book_reviews,
        "generate_or_reuse_book_review",
        fail_review,
    )
    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={"force_regenerate": False},
    )
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, job_id) is False
    finally:
        clear_editing_job_handlers()

    failed = await _completion_field(client, project_id, "book_review_job")
    assert failed["status"] == "failed"
    assert failed["stage"] == "reviewing"
    assert "已保存的进度会保留" in failed["error_message"]
    assert failed["can_retry"] is True

    retried = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={"force_regenerate": False},
    )
    assert retried.status_code == 202
    assert retried.json()["job_id"] == job_id
    assert retried.json()["status"] == "pending"
    assert retried.json()["stage"] == "queued"
    assert retried.json()["error_message"] == ""


async def test_book_review_job_accepts_readable_chapters_without_confirmation(
    client,
    test_db,
):
    project_id, chapters, _ = await _book(client, test_db)
    for chapter in chapters:
        await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter.id,
                                    status=MemoirBookChapterDraftStatus.DRAFT)

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={},
    )

    assert queued.status_code == 202


async def test_book_review_ignores_a_stale_output_title(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)
    bootstrapped = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert bootstrapped.status_code == 200
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    assert (await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")).status_code == 200
    reviewed = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
    )
    assert reviewed.status_code == 200, reviewed.text
    assembled = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble",
    )
    assert assembled.status_code == 200, assembled.text
    output = await test_db.scalar(
        select(MemoirBookOutput).where(
            MemoirBookOutput.project_id == project_id,
        )
    )
    assert output is not None
    output.title = "一个人的故园与远方"
    test_db.add(output)
    await test_db.flush()

    context = await book_reviews.load_book_review_context(
        test_db,
        project_id=project_id,
    )

    assert context.book_metadata["title"] == "测试回忆录"
    assert context.input_signature == reviewed.json()["input_signature"]


@pytest.mark.parametrize('field', ['subtitle', 'narrator_name', 'prepared_by', 'preface', 'afterword', 'back_cover_quote'])
@pytest.mark.parametrize('during_generation', [False, True])
async def test_review1470_book_metadata_invalidates_cached_and_inflight_review(client, test_db, monkeypatch, field, during_generation):
    project_id, chapters, _ = await _book(client, test_db)
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)
    await _create_current_draft(test_db, project_id=project_id, chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.CONFIRMED)
    await book_reviews.ensure_chapter_digests(test_db, project_id=project_id, ai_model=None, ai_client=fake)
    assembled = await client.post(f'/api/memoir/projects/{project_id}/book-output/assemble')
    assert assembled.status_code == 200, assembled.text
    old = await book_reviews.generate_or_reuse_book_review(test_db, project_id=project_id, ai_client=fake)
    old_id, old_signature = old.id, old.input_signature
    await test_db.commit()
    value = '用户确认的新成书信息'
    async def edit():
        response = await client.patch(f'/api/mobile/memoir/projects/{project_id}/book-output', json={field: value})
        assert response.status_code == 200, response.text
    if during_generation:
        generate = book_reviews.generate_structured_with_client
        async def generate_while_editing(*args, **kwargs):
            result = await generate(*args, **kwargs)
            await edit()
            return result
        monkeypatch.setattr(book_reviews, 'generate_structured_with_client', generate_while_editing)
        with pytest.raises(HTTPException) as error:
            await book_reviews.generate_or_reuse_book_review(test_db, project_id=project_id, ai_client=fake, force_regenerate=True)
        assert error.value.status_code == 409
    else:
        await edit()
        assert await book_reviews.get_current_book_review(test_db, project_id=project_id) is None
        fresh = await book_reviews.generate_or_reuse_book_review(test_db, project_id=project_id, ai_client=fake)
        assert fresh.id != old_id and fresh.input_signature != old_signature
        assert value in fake.prompts[-1]


@pytest.mark.parametrize("after_commit", [False, True])
async def test_background_batch_recovers_at_child_checkpoint_without_client_reads(client, test_db, monkeypatch, after_commit):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=1)
    project = await test_db.get(Project, project_id)
    chapter_id, user_id = chapters[0].id, project.user_id
    batch = MemoirBookCompletionJob(project_id=project_id, active_key=f"book-completion:{project_id}",
        status="pending", target_chapters_json=[{"chapter_id": chapters[0].id, "title": chapters[0].title}])
    test_db.add(batch)
    await test_db.commit()
    enqueue = chapter_preparation_jobs.enqueue_chapter_preparation_job
    commit = test_db.commit
    dispatched = False
    async def stopped_after_dispatch(*args, **kwargs):
        nonlocal dispatched
        result = await enqueue(*args, **kwargs)
        dispatched = True
        if not after_commit:
            raise RuntimeError("worker stopped before checkpoint")
        return result
    async def stopped_after_checkpoint():
        await commit()
        if dispatched:
            raise RuntimeError("worker stopped after checkpoint")
    monkeypatch.setattr(chapter_preparation_jobs, "enqueue_chapter_preparation_job", stopped_after_dispatch)
    monkeypatch.setattr(test_db, "commit", stopped_after_checkpoint)
    with pytest.raises(RuntimeError, match="worker stopped"):
        await book_completion_jobs.advance_book_completion_jobs(test_db)
    await test_db.rollback()
    await test_db.refresh(batch)
    monkeypatch.setattr(chapter_preparation_jobs, "enqueue_chapter_preparation_job", enqueue)
    monkeypatch.setattr(test_db, "commit", commit)
    if not after_commit:
        assert not batch.chapter_job_ids_json
        assert await test_db.scalar(select(MemoirEditingJob.id).where(
            MemoirEditingJob.chapter_id == chapter_id,
            MemoirEditingJob.job_type == "prepare_chapter",
        )) is None
        await book_completion_jobs.advance_book_completion_jobs(test_db)
        await test_db.refresh(batch)
    child_id = batch.chapter_job_ids_json[chapter_id]
    child = await test_db.get(MemoirEditingJob, child_id)
    assert child.payload_json["user_id"] == user_id
    child.status = "succeeded"
    await test_db.commit()
    await book_completion_jobs.advance_book_completion_jobs(test_db)
    await test_db.refresh(batch)
    assert batch.status == "succeeded"
    assert batch.active_key is None
    assert batch.chapter_job_ids_json == {chapter_id: child_id}


@pytest.mark.parametrize("scope", ["chapter", "book"])
async def test_confirmed_fact_invalidates_chapter_and_blocks_old_book_assembly(client, test_db, scope):
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output

    project_id, chapters, sources = await _book(client, test_db)
    chapter_id = chapters[0].id
    _, version = await _create_current_draft(
        test_db, project_id=project_id, chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.DRAFT,
    )
    issue = MemoirCompletionIssue(
        project_id=project_id, scope=scope,
        chapter_id=chapter_id if scope == "chapter" else None,
        issue_type="conflict", channel="manual_review", question="比赛是哪一年？",
        reason="年份需要核对", priority="high", fingerprint="f" * 64,
        related_material_ids_json=[sources[0]["utterance_id"]] if scope == "chapter" else [],
        related_chapter_ids_json=[chapter_id] if scope == "book" else [],
        input_signature="fact-regression", resolution="user_decision",
    )
    test_db.add(issue)
    await test_db.commit()
    response = await client.post(
        f"/api/memoir/projects/{project_id}/completion-issues/{issue.id}/close",
        json={"outcome": "confirmed", "note": "是1978年，之前记错了。"},
    )
    assert response.status_code == 200, response.text
    context = await chapter_generation.load_chapter_generation_context(
        test_db, project_id=project_id, chapter_id=chapter_id,
    )
    assert chapter_generation.chapter_draft_generation_plan(context, version).target_context.sections
    prompt = chapter_generation.build_chapter_generation_prompt(context, editorial_guidance=[])
    assert "是1978年，之前记错了。" in prompt
    assert "学校比赛" in prompt
    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert assembled.omitted_chapter_ids == [chapter_id]

    edited_text = "1982年，我参加了学校比赛。"
    edited = MemoirTranscriptVersion(
        voice_session_id=sources[0]["session"]["id"],
        source_artifact_id=sources[0]["utterance_id"],
        version_type="user_edited", created_by="user", content=edited_text,
    )
    test_db.add(edited)
    await test_db.flush()
    material = await test_db.scalar(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.utterance_id == sources[0]["utterance_id"],
    ))
    material.transcript_version_id = edited.id
    await test_db.flush()
    updated = await chapter_generation.load_chapter_generation_context(
        test_db, project_id=project_id, chapter_id=chapter_id,
    )
    assert updated.materials[0].text == edited_text
    assert updated.materials[0].edits[-1].before == context.materials[0].text
    assert updated.materials[0].edits[-1].after == edited_text
    assert updated.sections[0].decisions == context.sections[0].decisions
