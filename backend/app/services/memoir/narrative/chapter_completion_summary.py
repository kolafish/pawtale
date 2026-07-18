from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirCompletionItemStatus,
    MemoirNarrativeGapRecommendedAction,
)
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlanChapter,
    MemoirEditingJob,
    MemoirVoiceSession,
)
from app.models.memoir_narrative import MemoirCompletionItem
from app.schemas.memoir_narrative import (
    MemoirChapterCompletionIssue,
    MemoirChapterCompletionNextAction,
    MemoirChapterCompletionSummary,
)
from app.services.memoir.narrative import completion_refresh
from app.services.memoir.narrative.gap_rules import ChapterAuditContext


_ACTIVE_JOB_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}
_FAILED_JOB_STATUSES = {
    MemoirEditingJobStatus.FAILED.value,
    MemoirEditingJobStatus.SKIPPED.value,
}
_ACTIVE_ITEM_STATUSES = {
    MemoirCompletionItemStatus.OPEN.value,
    MemoirCompletionItemStatus.IN_PROGRESS.value,
    MemoirCompletionItemStatus.DEFERRED.value,
}


def _next_action(
    kind: str,
    title: str,
    detail: str,
    *,
    action_text: str = "",
    disabled: bool = False,
    issue_id: str | None = None,
) -> MemoirChapterCompletionNextAction:
    return MemoirChapterCompletionNextAction(
        kind=kind,
        title=title,
        detail=detail,
        action_text=action_text,
        disabled=disabled,
        issue_id=issue_id,
    )


def _active_issue_response(
    item: MemoirCompletionItem | None,
    *,
    active_session_id: str | None,
) -> MemoirChapterCompletionIssue | None:
    if item is None:
        return None
    public_status = (
        "pending"
        if item.status == MemoirCompletionItemStatus.OPEN.value
        else item.status
    )
    return MemoirChapterCompletionIssue(
        id=item.id,
        question=item.question or item.title,
        status=public_status,
        active_session_id=active_session_id,
    )


def _summary(
    *,
    chapter: MemoirBookPlanChapter,
    context: ChapterAuditContext,
    active_item: MemoirCompletionItem | None,
    active_session_id: str | None,
    refresh_job: MemoirEditingJob | None,
    draft_job: MemoirEditingJob | None,
    chapter_draft_status: str | None,
) -> MemoirChapterCompletionSummary:
    draft_current = (
        context.draft is not None
        and not context.draft.source_changes_pending
    )
    refresh_result = (
        refresh_job.result_json
        if refresh_job is not None and isinstance(refresh_job.result_json, dict)
        else {}
    )
    refresh_found_no_gaps = (
        refresh_job is not None
        and refresh_job.status == MemoirEditingJobStatus.SUCCEEDED.value
        and refresh_result.get("deterministic_gap_count") == 0
        and refresh_result.get("semantic_gap_count") == 0
    )

    if refresh_job is not None and refresh_job.status in _ACTIVE_JOB_STATUSES:
        state = "preparing"
        headline = "正在检查现有讲述"
        detail = "系统正在判断现有素材是否足够写成正文。"
        action = _next_action("wait", headline, detail, disabled=True)
    elif refresh_job is not None and refresh_job.status in _FAILED_JOB_STATUSES:
        state = "failed"
        headline = "上次没有整理完成"
        detail = refresh_job.error_message or "已有录音和文字没有丢失，可以重新整理。"
        action = _next_action(
            "prepare",
            "重新整理这一章",
            detail,
            action_text="重新整理",
        )
    elif active_item is not None:
        state = "needs_input"
        if (
            active_item.action
            == MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW.value
        ):
            headline = "还需要确认一件事"
            detail = active_item.description or "确认后系统会继续整理正文。"
            action = _next_action(
                "confirm",
                active_item.title,
                detail,
                action_text="确认",
                issue_id=active_item.id,
            )
        else:
            headline = "还需要讲一件事"
            detail = "回答会直接保存为本章素材，之后自动继续整理。"
            action = _next_action(
                "start_interview",
                active_item.question or active_item.title,
                detail,
                action_text=(
                    "继续讲"
                    if active_item.status
                    == MemoirCompletionItemStatus.IN_PROGRESS.value
                    else "开始讲"
                ),
                issue_id=active_item.id,
            )
    elif draft_job is not None and draft_job.status in _ACTIVE_JOB_STATUSES:
        state = "preparing"
        headline = "正在整理正文"
        detail = "系统正在把已有讲述写成连贯正文。"
        action = _next_action("wait", headline, detail, disabled=True)
    elif draft_current:
        if chapter_draft_status == MemoirBookChapterDraftStatus.CONFIRMED.value:
            state = "confirmed"
            if refresh_found_no_gaps:
                headline = "本次检查未发现待补内容"
                detail = "已按当前素材重新检查，正文仍保持确认状态。"
            else:
                headline = "本章已确认"
                detail = "正文已经收入书稿，但仍可按当前素材重新检查。"
            action = _next_action("complete", headline, detail, disabled=True)
        else:
            state = "draft_ready"
            headline = "正文已经整理好"
            detail = "请通读整章，需要时修改，然后确认收入书稿。"
            action = _next_action(
                "review_draft",
                "通读本章正文",
                detail,
                action_text="查看正文",
            )
    elif draft_job is not None and draft_job.status in _FAILED_JOB_STATUSES:
        state = "failed"
        headline = "正文没有整理完成"
        detail = draft_job.error_message or "已有录音和文字没有丢失，可以重新整理。"
        action = _next_action(
            "prepare",
            "重新整理这一章",
            detail,
            action_text="重新整理",
        )
    else:
        state = "not_started"
        headline = "可以继续整理正文"
        detail = "再次整理后，系统会按当前素材生成正文。"
        action = _next_action(
            "prepare",
            "继续整理这一章",
            detail,
            action_text="继续整理",
        )

    return MemoirChapterCompletionSummary(
        chapter_id=chapter.id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        state=state,
        next_action=action,
        active_issue=_active_issue_response(
            active_item,
            active_session_id=active_session_id,
        ),
        headline=headline,
        detail=detail,
    )


async def get_chapter_completion_summaries(
    db: AsyncSession,
    *,
    project_id: str,
    chapters: list[MemoirBookPlanChapter],
    contexts: dict[str, ChapterAuditContext],
) -> dict[str, MemoirChapterCompletionSummary]:
    chapter_ids = [chapter.id for chapter in chapters]
    if not chapter_ids:
        return {}
    if set(chapter_ids) != set(contexts):
        raise ValueError("completion contexts do not match chapters")

    jobs = list(
        (
            await db.scalars(
                select(MemoirEditingJob)
                .where(
                    MemoirEditingJob.project_id == project_id,
                    MemoirEditingJob.chapter_id.in_(chapter_ids),
                    MemoirEditingJob.job_type.in_(
                        {
                            MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value,
                            MemoirEditingJobType.GENERATE_CHAPTER_DRAFT.value,
                        }
                    ),
                )
                .order_by(
                    MemoirEditingJob.updated_at.desc(),
                    MemoirEditingJob.created_at.desc(),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    jobs_by_chapter: dict[str, list[MemoirEditingJob]] = {
        chapter_id: [] for chapter_id in chapter_ids
    }
    for job in jobs:
        if job.chapter_id in jobs_by_chapter:
            jobs_by_chapter[job.chapter_id].append(job)

    items = list(
        (
            await db.scalars(
                select(MemoirCompletionItem)
                .where(
                    MemoirCompletionItem.project_id == project_id,
                    MemoirCompletionItem.chapter_id.in_(chapter_ids),
                    MemoirCompletionItem.status.in_(_ACTIVE_ITEM_STATUSES),
                )
                .order_by(
                    (
                        MemoirCompletionItem.status
                        == MemoirCompletionItemStatus.IN_PROGRESS.value
                    ).desc(),
                    MemoirCompletionItem.priority.desc(),
                    MemoirCompletionItem.created_at,
                )
            )
        ).all()
    )
    items_by_chapter: dict[str, list[MemoirCompletionItem]] = {
        chapter_id: [] for chapter_id in chapter_ids
    }
    for item in items:
        if item.chapter_id in items_by_chapter:
            items_by_chapter[item.chapter_id].append(item)

    active_session_ids = dict(
        (
            await db.execute(
                select(
                    MemoirVoiceSession.current_completion_item_id,
                    MemoirVoiceSession.id,
                ).where(
                    MemoirVoiceSession.project_id == project_id,
                    MemoirVoiceSession.current_completion_item_id.in_(
                        [item.id for item in items]
                    ),
                )
            )
        ).all()
    )

    draft_statuses = dict(
        (
            await db.execute(
                select(
                    MemoirBookChapterDraft.chapter_id,
                    MemoirBookChapterDraft.status,
                ).where(
                    MemoirBookChapterDraft.project_id == project_id,
                    MemoirBookChapterDraft.chapter_id.in_(chapter_ids),
                )
            )
        ).all()
    )

    summaries: dict[str, MemoirChapterCompletionSummary] = {}
    for chapter in chapters:
        context = contexts[chapter.id]
        content_signature = completion_refresh.chapter_completion_content_signature(
            context
        )
        chapter_jobs = jobs_by_chapter[chapter.id]
        refresh_job = next(
            (
                job
                for job in chapter_jobs
                if job.job_type
                == MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value
                and isinstance(job.payload_json, dict)
                and job.payload_json.get("content_signature") == content_signature
            ),
            None,
        )
        draft_job = next(
            (
                job
                for job in chapter_jobs
                if job.job_type == MemoirEditingJobType.GENERATE_CHAPTER_DRAFT.value
            ),
            None,
        )
        active_item = next(iter(items_by_chapter[chapter.id]), None)
        summaries[chapter.id] = _summary(
            chapter=chapter,
            context=context,
            active_item=active_item,
            active_session_id=(
                active_session_ids.get(active_item.id) if active_item else None
            ),
            refresh_job=refresh_job,
            draft_job=draft_job,
            chapter_draft_status=draft_statuses.get(chapter.id),
        )
    return summaries


async def get_chapter_completion_summary(
    db: AsyncSession,
    *,
    project_id: str,
    chapter: MemoirBookPlanChapter,
    context: ChapterAuditContext,
) -> MemoirChapterCompletionSummary:
    return (
        await get_chapter_completion_summaries(
            db,
            project_id=project_id,
            chapters=[chapter],
            contexts={chapter.id: context},
        )
    )[chapter.id]
