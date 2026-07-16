from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.framework_templates import (
    DEFAULT_FRAMEWORK_TEMPLATE_KEY,
    DEFAULT_FRAMEWORK_TEMPLATE_VERSION,
    FrameworkTemplateDefinition,
    canonical_template_snapshot,
    framework_template_hash,
    load_framework_template,
)
from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirFrameworkEvidenceState,
    MemoirFrameworkMatchSource,
    MemoirFrameworkUserResolution,
)
from app.domain.projects.types import ProjectType
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirFrameworkItem,
    MemoirFrameworkItemClaim,
    MemoirProjectFramework,
)
from app.models.projects import Project
from app.services.memoir.narrative.claim_usage_policy import (
    known_for_conversation_filters,
)


@dataclass(frozen=True, slots=True)
class FrameworkClaimMatch:
    claim_id: str
    item_key: str
    evidence_role: MemoirClaimEvidenceRole
    confidence: float


@dataclass(frozen=True, slots=True)
class FrameworkReconcileResult:
    created: int
    updated: int
    removed: int
    affected_item_ids: tuple[str, ...]


def _validate_stored_framework(
    framework: MemoirProjectFramework,
) -> None:
    snapshot = framework.template_snapshot_json
    if (
        not isinstance(snapshot, dict)
        or framework_template_hash(snapshot) != framework.template_hash
    ):
        raise ValueError("stored topic map template snapshot is invalid")
    template = FrameworkTemplateDefinition.model_validate(snapshot)
    if (
        template.template_key != framework.template_key
        or template.version != framework.template_version
    ):
        raise ValueError("stored topic map template identity is invalid")
    expected_items = {
        item.item_key: (item.module_key, item.sort_order)
        for item in template.items
    }
    actual_items = {
        item.item_key: (item.module_key, item.sort_order)
        for item in framework.items
    }
    if expected_items != actual_items:
        raise ValueError("stored topic map items do not match its snapshot")


async def initialize_project_framework(
    db: AsyncSession,
    *,
    project_id: str,
    template: FrameworkTemplateDefinition | None = None,
) -> MemoirProjectFramework:
    existing = await db.scalar(
        select(MemoirProjectFramework)
        .where(MemoirProjectFramework.project_id == project_id)
        .options(selectinload(MemoirProjectFramework.items))
    )
    if existing is not None:
        _validate_stored_framework(existing)
        return existing
    project = await db.get(Project, project_id)
    if project is None or project.type != ProjectType.MEMOIR.value:
        raise ValueError("topic map requires a memoir project")
    selected = template or load_framework_template(
        DEFAULT_FRAMEWORK_TEMPLATE_KEY,
        DEFAULT_FRAMEWORK_TEMPLATE_VERSION,
    )
    snapshot = canonical_template_snapshot(selected)
    framework = MemoirProjectFramework(
        project_id=project_id,
        template_key=selected.template_key,
        template_version=selected.version,
        template_snapshot_json=snapshot,
        template_hash=framework_template_hash(snapshot),
    )
    db.add(framework)
    await db.flush()
    for item in selected.items:
        db.add(
            MemoirFrameworkItem(
                project_framework_id=framework.id,
                item_key=item.item_key,
                module_key=item.module_key,
                sort_order=item.sort_order,
                evidence_state=MemoirFrameworkEvidenceState.NONE.value,
                user_resolution=MemoirFrameworkUserResolution.NONE.value,
            )
        )
    await db.flush()
    loaded = await db.scalar(
        select(MemoirProjectFramework)
        .where(MemoirProjectFramework.id == framework.id)
        .options(selectinload(MemoirProjectFramework.items))
    )
    if loaded is None:
        raise RuntimeError("topic map initialization did not persist its framework")
    return loaded


async def framework_for_project(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirProjectFramework | None:
    framework = await db.scalar(
        select(MemoirProjectFramework)
        .where(MemoirProjectFramework.project_id == project_id)
        .options(selectinload(MemoirProjectFramework.items))
    )
    if framework is not None:
        _validate_stored_framework(framework)
    return framework


async def recompute_framework_items(
    db: AsyncSession,
    *,
    item_ids: set[str],
    assessment_version: str,
) -> None:
    if not item_ids:
        return
    items = list(
        (
            await db.scalars(
                select(MemoirFrameworkItem).where(
                    MemoirFrameworkItem.id.in_(item_ids)
                )
            )
        ).all()
    )
    for item in items:
        material_count = int(
            await db.scalar(
                select(func.count(MemoirFrameworkItemClaim.claim_id))
                .join(
                    MemoirClaim,
                    MemoirClaim.id == MemoirFrameworkItemClaim.claim_id,
                )
                .where(
                    MemoirFrameworkItemClaim.framework_item_id == item.id,
                    *known_for_conversation_filters(MemoirClaim),
                )
            )
            or 0
        )
        item.evidence_state = (
            MemoirFrameworkEvidenceState.HAS_MATERIAL.value
            if material_count
            else MemoirFrameworkEvidenceState.NONE.value
        )
        item.assessment_summary = (
            f"已关联 {material_count} 条可用于访谈防重复的素材。"
            if material_count
            else ""
        )
        item.assessment_version = assessment_version
        db.add(item)
    await db.flush()


async def recompute_framework_items_for_claim(
    db: AsyncSession,
    *,
    claim_id: str,
    assessment_version: str,
) -> None:
    item_ids = set(
        (
            await db.scalars(
                select(MemoirFrameworkItemClaim.framework_item_id).where(
                    MemoirFrameworkItemClaim.claim_id == claim_id
                )
            )
        ).all()
    )
    await recompute_framework_items(
        db,
        item_ids=item_ids,
        assessment_version=assessment_version,
    )


async def reconcile_automatic_claim_matches(
    db: AsyncSession,
    *,
    project_framework: MemoirProjectFramework,
    claims: list[MemoirClaim],
    matches: tuple[FrameworkClaimMatch, ...],
    match_source: MemoirFrameworkMatchSource,
    matcher_version: str,
) -> FrameworkReconcileResult:
    if match_source == MemoirFrameworkMatchSource.MANUAL:
        raise ValueError("automatic reconcile cannot write manual matches")
    claim_ids = {claim.id for claim in claims}
    if any(claim.project_id != project_framework.project_id for claim in claims):
        raise ValueError("topic map claims must belong to the framework project")
    items_by_key = {item.item_key: item for item in project_framework.items}
    desired = {
        (match.claim_id, match.item_key): match
        for match in matches
    }
    if any(
        match.claim_id not in claim_ids
        or match.item_key not in items_by_key
        or not 0 <= match.confidence <= 1
        for match in matches
    ):
        raise ValueError("topic map match is outside the reconcile candidates")
    existing = list(
        (
            await db.scalars(
                select(MemoirFrameworkItemClaim)
                .join(
                    MemoirFrameworkItem,
                    MemoirFrameworkItem.id
                    == MemoirFrameworkItemClaim.framework_item_id,
                )
                .where(
                    MemoirFrameworkItem.project_framework_id
                    == project_framework.id,
                    MemoirFrameworkItemClaim.claim_id.in_(claim_ids),
                )
            )
        ).all()
    )
    existing_by_key = {
        (
            relation.claim_id,
            next(
                item.item_key
                for item in project_framework.items
                if item.id == relation.framework_item_id
            ),
        ): relation
        for relation in existing
    }
    affected_item_ids = {
        relation.framework_item_id for relation in existing
    }
    created = 0
    updated = 0
    removed = 0
    for key, relation in existing_by_key.items():
        match = desired.get(key)
        if relation.match_source == MemoirFrameworkMatchSource.MANUAL.value:
            continue
        if match is None:
            await db.delete(relation)
            removed += 1
            continue
        relation.evidence_role = match.evidence_role.value
        relation.match_source = match_source.value
        relation.matcher_version = matcher_version
        relation.confidence = match.confidence
        db.add(relation)
        updated += 1
    for key, match in desired.items():
        if key in existing_by_key:
            continue
        item = items_by_key[match.item_key]
        relation = MemoirFrameworkItemClaim(
            framework_item_id=item.id,
            claim_id=match.claim_id,
            evidence_role=match.evidence_role.value,
            match_source=match_source.value,
            matcher_version=matcher_version,
            confidence=match.confidence,
        )
        db.add(relation)
        affected_item_ids.add(item.id)
        created += 1
    await db.flush()
    await recompute_framework_items(
        db,
        item_ids=affected_item_ids,
        assessment_version=matcher_version,
    )
    return FrameworkReconcileResult(
        created=created,
        updated=updated,
        removed=removed,
        affected_item_ids=tuple(sorted(affected_item_ids)),
    )
