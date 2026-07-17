from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import MemoirClaimEvidenceRole
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChapterSourceLinkStatus,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirEditingJob,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import MemoirClaimEvidence, MemoirCompletionItem
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir import automation_event_resolution
from app.services.memoir.book_plan_sources import (
    DRAFT_STATUS_REASON_KEY,
    REMOVED_SOURCE_UTTERANCE_IDS_KEY,
    metadata_without_keys,
    source_change_metadata_keys,
)
from app.services.memoir.book_source_links import mark_chapter_sources_merged
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative.book_readiness import (
    invalidate_formal_book_output,
)
from app.services.memoir.narrative.chapter_draft_ai import (
    ChapterDraftClaimInput,
    ChapterDraftGenerationInput,
    ChapterDraftStoryInput,
    ChapterDraftSupplementInput,
    generate_chapter_draft_text,
)
from app.services.memoir.narrative.claim_usage_policy import (
    claim_usable_for_publication,
)
from app.services.memoir.narrative.gap_rules import (
    ChapterAuditContext,
    chapter_completion_content_signature,
    load_chapter_audit_context,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _signature(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        )
    )[:12]


async def enqueue_chapter_draft_generation(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    content_signature: str,
    ai_model: str | None = None,
    editorial_guidance: list[str] | None = None,
    source: str = "chapter_workspace",
) -> MemoirEditingJob:
    guidance = _string_list(editorial_guidance or [])
    signature = _signature(
        {
            "content_signature": content_signature,
            "ai_model": ai_model or "default",
            "editorial_guidance": guidance,
        }
    )
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.GENERATE_CHAPTER_DRAFT,
        payload_json={
            "chapter_id": chapter_id,
            "content_signature": content_signature,
            "ai_model": ai_model,
            "editorial_guidance": guidance,
            "input_signature": signature,
            "source": source,
        },
        dedupe_key=f"generate-chapter-draft:{project_id}:{chapter_id}:{signature}",
        replace_completed=True,
        retry_failed=True,
    )


async def _generation_input(
    db: AsyncSession,
    *,
    context: ChapterAuditContext,
    editorial_guidance: list[str],
) -> ChapterDraftGenerationInput:
    usable_claim_ids = {
        claim.id
        for claim in context.claims.values()
        if claim_usable_for_publication(claim)
        and context.evidence_counts.get(claim.id, 0) > 0
    }
    if not usable_claim_ids:
        raise ValueError("chapter has no publishable claims with source evidence")
    quotes_by_claim: dict[str, list[str]] = {}
    claim_ids_by_utterance: dict[str, list[str]] = {}
    evidence = (
        await db.scalars(
            select(MemoirClaimEvidence)
            .join(
                MemoirChapterSourceLink,
                (
                    MemoirChapterSourceLink.project_id
                    == MemoirClaimEvidence.project_id
                )
                & (
                    MemoirChapterSourceLink.utterance_id
                    == MemoirClaimEvidence.utterance_id
                )
                & (
                    MemoirChapterSourceLink.transcript_version_id.is_(None)
                    | (
                        MemoirChapterSourceLink.transcript_version_id
                        == MemoirClaimEvidence.transcript_version_id
                    )
                ),
            )
            .where(
                MemoirClaimEvidence.project_id == context.chapter.project_id,
                MemoirClaimEvidence.claim_id.in_(usable_claim_ids),
                MemoirClaimEvidence.evidence_role
                == MemoirClaimEvidenceRole.SUPPORTS.value,
                MemoirChapterSourceLink.chapter_id == context.chapter.id,
                MemoirChapterSourceLink.status
                == MemoirChapterSourceLinkStatus.ACTIVE.value,
            )
            .order_by(
                MemoirClaimEvidence.claim_id,
                MemoirClaimEvidence.created_at,
                MemoirClaimEvidence.id,
            )
        )
    ).all()
    for item in evidence:
        text = (item.quote or item.evidence_note or "").strip()
        if text:
            quotes_by_claim.setdefault(item.claim_id, []).append(text[:800])
        if item.utterance_id:
            claim_ids_by_utterance.setdefault(item.utterance_id, []).append(
                item.claim_id
            )

    stories: list[ChapterDraftStoryInput] = []
    for story in sorted(
        context.stories.values(),
        key=lambda item: (
            item.time_start is None,
            item.time_start or datetime.max,
            item.created_at,
            item.id,
        ),
    ):
        claims = []
        for link in context.story_claims.get(story.id, []):
            claim = context.claims.get(link.claim_id)
            if claim is None or claim.id not in usable_claim_ids:
                continue
            claims.append(
                ChapterDraftClaimInput(
                    claim_id=claim.id,
                    statement=claim.statement,
                    certainty=claim.certainty,
                    narrative_role=link.narrative_role,
                    is_core=link.is_core,
                    evidence_quotes=quotes_by_claim.get(claim.id, [])[:4],
                )
            )
        if claims:
            stories.append(
                ChapterDraftStoryInput(
                    story_id=story.id,
                    title=story.title,
                    summary=story.summary,
                    story_type=story.story_type,
                    time_start=story.time_start.isoformat() if story.time_start else None,
                    time_end=story.time_end.isoformat() if story.time_end else None,
                    claims=claims,
                )
            )
    if not stories:
        raise ValueError("chapter has no story with publishable source evidence")
    supplement_blocks: list[ChapterDraftSupplementInput] = []
    if claim_ids_by_utterance:
        supplement_rows = (
            await db.execute(
                select(MemoirVoiceUtterance, MemoirCompletionItem)
                .join(
                    MemoirCompletionItem,
                    MemoirCompletionItem.id
                    == MemoirVoiceUtterance.completion_item_id,
                )
                .where(
                    MemoirCompletionItem.project_id
                    == context.chapter.project_id,
                    MemoirVoiceUtterance.id.in_(list(claim_ids_by_utterance)),
                )
                .order_by(
                    MemoirVoiceUtterance.created_at,
                    MemoirVoiceUtterance.id,
                )
            )
        ).all()
        for utterance, completion_item in supplement_rows:
            text = (
                (utterance.transcript_edited or "").strip()
                or (utterance.transcript_raw or "").strip()
            )
            claim_ids = list(
                dict.fromkeys(claim_ids_by_utterance.get(utterance.id, []))
            )
            if text and claim_ids:
                supplement_blocks.append(
                    ChapterDraftSupplementInput(
                        utterance_id=utterance.id,
                        text=text,
                        question=completion_item.question,
                        purpose=completion_item.description,
                        dimension=completion_item.dimension,
                        claim_ids=claim_ids,
                    )
                )
    return ChapterDraftGenerationInput(
        chapter_id=context.chapter.id,
        chapter_title=context.chapter.title,
        stories=stories,
        supplement_blocks=supplement_blocks,
        existing_draft=context.draft.content if context.draft else "",
        editorial_guidance=editorial_guidance,
    )


async def _next_version(db: AsyncSession, *, draft_id: str) -> int:
    latest = await db.scalar(
        select(func.max(MemoirBookChapterDraftVersion.version)).where(
            MemoirBookChapterDraftVersion.draft_id == draft_id
        )
    )
    return int(latest or 0) + 1


async def _acknowledge_sources(
    db: AsyncSession,
    *,
    chapter: MemoirBookPlanChapter,
    draft: MemoirBookChapterDraft,
    version_id: str,
) -> None:
    draft.metadata_json = metadata_without_keys(
        draft.metadata_json,
        {DRAFT_STATUS_REASON_KEY, REMOVED_SOURCE_UTTERANCE_IDS_KEY},
    )
    chapter.metadata_json = metadata_without_keys(
        chapter.metadata_json,
        source_change_metadata_keys(),
    )
    db.add_all([draft, chapter])
    await mark_chapter_sources_merged(
        db,
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        draft_version_id=version_id,
        include_removed=True,
    )
    await automation_event_resolution.resolve_assembled_chapter_audit_events(
        db,
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        result_version_id=version_id,
    )


async def _stale_generation_result(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    payload: dict,
    skip_reason: str,
) -> EditingJobResult:
    from app.services.memoir.narrative import completion_refresh

    replacement = await completion_refresh.enqueue_chapter_completion_refresh(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        include_semantic_audit=True,
        ai_model=(str(payload["ai_model"]) if payload.get("ai_model") else None),
        source=skip_reason,
    )
    return EditingJobResult(
        status=MemoirEditingJobStatus.SKIPPED,
        result_json={
            "chapter_id": chapter_id,
            "published": False,
            "skip_reason": skip_reason,
            "replacement_job_id": replacement.id,
        },
        error_message=skip_reason.replace("_", " "),
    )


async def run_chapter_draft_generation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    chapter_id = str(payload.get("chapter_id") or job.chapter_id or "").strip()
    project_id = job.project_id
    context = await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    input_signature = chapter_completion_content_signature(context)
    expected_signature = str(payload.get("content_signature") or "").strip()
    if expected_signature and expected_signature != input_signature:
        return await _stale_generation_result(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            payload=payload,
            skip_reason="chapter_content_changed_before_draft_generation",
        )
    generation_input = await _generation_input(
        db,
        context=context,
        editorial_guidance=_string_list(payload.get("editorial_guidance")),
    )
    client = ai_client or get_ai_service(
        str(payload.get("ai_model") or "").strip() or None
    )
    generated = await generate_chapter_draft_text(
        client,
        context=generation_input,
    )
    await db.rollback()
    db.expire_all()
    await db.refresh(job)
    current_context = await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if chapter_completion_content_signature(current_context) != input_signature:
        return await _stale_generation_result(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            payload=payload,
            skip_reason="chapter_content_changed_during_draft_generation",
        )
    context = current_context
    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
    )
    if draft is None:
        draft = MemoirBookChapterDraft(
            project_id=project_id,
            chapter_id=chapter_id,
            status=MemoirBookChapterDraftStatus.DRAFT.value,
            metadata_json={"source": "chapter_generation"},
        )
        db.add(draft)
        await db.flush()
    latest = await db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.draft_id == draft.id)
        .order_by(
            MemoirBookChapterDraftVersion.version.desc(),
            MemoirBookChapterDraftVersion.created_at.desc(),
            MemoirBookChapterDraftVersion.id.desc(),
        )
        .limit(1)
    )
    if latest is not None and latest.content.strip() == generated.content.strip():
        version = latest
    else:
        version = MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter_id,
            version=await _next_version(db, draft_id=draft.id),
            content=generated.content,
            created_by=MemoirCreatedBy.AI.value,
            model=getattr(client, "model_name", None),
            prompt_snapshot=generated.prompt_snapshot,
            feedback=None,
        )
        db.add(version)
        await db.flush()
    draft.status = MemoirBookChapterDraftStatus.DRAFT.value
    draft.updated_at = _utcnow()
    draft.metadata_json = {
        **(draft.metadata_json if isinstance(draft.metadata_json, dict) else {}),
        "source": "chapter_generation",
        "used_claim_ids": list(generated.used_claim_ids),
    }
    output = await db.scalar(
        select(MemoirBookOutput).where(
            MemoirBookOutput.project_id == project_id
        )
    )
    if output is not None:
        invalidate_formal_book_output(output)
        db.add(output)
    await _acknowledge_sources(
        db,
        chapter=context.chapter,
        draft=draft,
        version_id=version.id,
    )
    return EditingJobResult(
        result_json={
            "chapter_id": chapter_id,
            "draft_id": draft.id,
            "draft_version_id": version.id,
            "version": version.version,
            "used_claim_count": len(generated.used_claim_ids),
            "model": getattr(client, "model_name", None),
        }
    )
