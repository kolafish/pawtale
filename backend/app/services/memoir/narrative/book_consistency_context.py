from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirNarrativeEntity,
)
from app.services.memoir.narrative.gap_rules import (
    ChapterAuditContext,
    load_chapter_audit_contexts,
)


BOOK_CONSISTENCY_ENGINE_VERSION = "3"


@dataclass(frozen=True)
class BookConsistencyContext:
    plan: MemoirBookPlan | None
    chapters: tuple[ChapterAuditContext, ...]
    output: MemoirBookOutput | None
    output_version: MemoirBookOutputVersion | None
    claims: dict[str, MemoirClaim]
    evidence_counts: dict[str, int]
    entities: dict[str, MemoirNarrativeEntity]

    @property
    def project_id(self) -> str:
        if self.plan is not None:
            return self.plan.project_id
        if self.output is not None:
            return self.output.project_id
        return ""


def _latest_output_version(
    output: MemoirBookOutput | None,
) -> MemoirBookOutputVersion | None:
    if output is None or not output.versions:
        return None
    return max(
        output.versions,
        key=lambda item: (item.version, item.created_at, item.id),
    )


async def load_book_consistency_context(
    db: AsyncSession,
    *,
    project_id: str,
) -> BookConsistencyContext:
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(selectinload(MemoirBookPlan.chapters))
    )
    chapter_models = (
        [
            chapter
            for chapter in sorted(
                plan.chapters,
                key=lambda item: (item.sort_order, item.id),
            )
            if chapter.include_in_book
        ]
        if plan is not None
        else []
    )
    contexts_by_id = await load_chapter_audit_contexts(
        db,
        project_id=project_id,
        chapters=chapter_models,
    )
    chapter_contexts = tuple(
        contexts_by_id[chapter.id] for chapter in chapter_models
    )
    claims = {
        claim.id: claim
        for chapter in chapter_contexts
        for claim in chapter.claims.values()
    }
    evidence_counts = {
        claim_id: max(
            chapter.evidence_counts.get(claim_id, 0)
            for chapter in chapter_contexts
            if claim_id in chapter.claims
        )
        for claim_id in claims
    }
    entity_ids = {
        entity_id
        for claim in claims.values()
        for entity_id in (
            claim.subject_entity_id,
            claim.object_entity_id,
            claim.place_entity_id,
        )
        if entity_id
    }
    entities = (
        {
            entity.id: entity
            for entity in (
                await db.scalars(
                    select(MemoirNarrativeEntity).where(
                        MemoirNarrativeEntity.project_id == project_id,
                        MemoirNarrativeEntity.id.in_(entity_ids),
                    )
                )
            ).all()
        }
        if entity_ids
        else {}
    )
    output = await db.scalar(
        select(MemoirBookOutput)
        .where(MemoirBookOutput.project_id == project_id)
        .options(selectinload(MemoirBookOutput.versions))
    )
    return BookConsistencyContext(
        plan=plan,
        chapters=chapter_contexts,
        output=output,
        output_version=_latest_output_version(output),
        claims=claims,
        evidence_counts=evidence_counts,
        entities=entities,
    )


def book_consistency_signature(context: BookConsistencyContext) -> str:
    payload = {
        "engine_version": BOOK_CONSISTENCY_ENGINE_VERSION,
        "plan": (
            [context.plan.id, context.plan.status, context.plan.updated_at.isoformat()]
            if context.plan
            else None
        ),
        "chapters": [
            {
                "chapter": [
                    chapter.chapter.id,
                    chapter.chapter.sort_order,
                    chapter.chapter.updated_at.isoformat(),
                ],
                "stories": [
                    [story.id, story.version, story.updated_at.isoformat()]
                    for story in sorted(
                        chapter.stories.values(),
                        key=lambda item: item.id,
                    )
                ],
                "draft": (
                    [
                        chapter.draft.id,
                        chapter.draft.version_id,
                        chapter.draft.version,
                        chapter.draft.status,
                        chapter.draft.source_changes_pending,
                        sha256(
                            chapter.draft.content.encode("utf-8")
                        ).hexdigest(),
                    ]
                    if chapter.draft
                    else None
                ),
            }
            for chapter in context.chapters
        ],
        "claims": [
            [
                claim.id,
                claim.verification_status,
                claim.publication_status,
                claim.superseded_by_claim_id,
                claim.conflict_group_id,
                context.evidence_counts.get(claim.id, 0),
                claim.updated_at.isoformat(),
            ]
            for claim in sorted(
                context.claims.values(),
                key=lambda item: item.id,
            )
        ],
        "entities": [
            [
                entity.id,
                entity.entity_type,
                entity.canonical_name,
                entity.verification_status,
                entity.merged_into_entity_id,
                entity.updated_at.isoformat(),
            ]
            for entity in sorted(
                context.entities.values(),
                key=lambda item: item.id,
            )
        ],
        "output": (
            {
                "id": context.output.id,
                "version_id": context.output_version.id,
                "version": context.output_version.version,
                "content_hash": sha256(
                    context.output_version.content.encode("utf-8")
                ).hexdigest(),
                "chapter_snapshot": context.output_version.chapter_snapshot_json,
                "manuscript_hash": sha256(
                    json.dumps(
                        context.output_version.manuscript_json or {},
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            }
            if context.output and context.output_version
            else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
