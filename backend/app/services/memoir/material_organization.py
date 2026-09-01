from __future__ import annotations

from dataclasses import dataclass
import re

from app.domain.memoir.types import MemoirSectionStatus, MemoirSectionType
from app.models.memoir import MemoirBookPlanChapter, MemoirSection
from app.services.memoir.section_roles import requires_user_assignment
from app.services.memoir.transcript_projection import current_utterance_text


ALLOWED_ACTIONS = {"assign_existing", "leave_unassigned"}
ALLOWED_SECTION_TYPES = {item.value for item in MemoirSectionType}


@dataclass(frozen=True)
class MaterialOrganizationDecision:
    action: str
    section_id: str | None
    chapter_id: str | None
    confidence: float | None
    reason: str


@dataclass(frozen=True)
class MaterialOrganizationChapterCandidate:
    id: str
    title: str


@dataclass
class MaterialOrganizationSectionCandidate:
    id: str
    chapter_id: str
    title: str
    section_type: str
    existing_material_examples: list[str]


def _compact(value: object, *, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _confidence(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return min(1.0, max(0.0, float(value)))


def material_organization_excerpt(material_text: str) -> str:
    return _compact(material_text, limit=120)


def material_organization_candidates(
    *,
    chapters: list[MemoirBookPlanChapter],
    sections: list[MemoirSection],
) -> tuple[
    list[MaterialOrganizationChapterCandidate],
    list[MaterialOrganizationSectionCandidate],
]:
    chapter_candidates = [
        MaterialOrganizationChapterCandidate(id=chapter.id, title=chapter.title)
        for chapter in chapters
    ]
    section_candidates = [
        MaterialOrganizationSectionCandidate(
            id=section.id,
            chapter_id=section.chapter_id,
            title=section.title,
            section_type=section.section_type,
            existing_material_examples=[
                text
                for text in (
                    material_organization_excerpt(
                        current_utterance_text(material.utterance)
                    )
                    for material in sorted(
                        section.materials,
                        key=lambda item: (item.sort_order, item.id),
                    )[-2:]
                )
                if text
            ],
        )
        for section in sections
        if (
            section.status == MemoirSectionStatus.ACTIVE.value
            and section.include_in_book
            and not requires_user_assignment(section.metadata_json)
        )
    ]
    return chapter_candidates, section_candidates


def normalize_material_organization_decision(
    payload: object,
    *,
    allowed_section_ids: set[str],
    allowed_chapter_ids: set[str],
) -> MaterialOrganizationDecision:
    data = payload if isinstance(payload, dict) else {}
    action = _compact(data.get("action"), limit=40)
    section_id = _compact(data.get("section_id"), limit=36) or None
    chapter_id = _compact(data.get("chapter_id"), limit=36) or None
    reason = _compact(data.get("reason"), limit=300)

    if action not in ALLOWED_ACTIONS:
        action = "leave_unassigned"
    if action == "assign_existing" and section_id not in allowed_section_ids:
        action = "leave_unassigned"
        section_id = None
    if action == "leave_unassigned" and chapter_id not in allowed_chapter_ids:
        chapter_id = None
    return MaterialOrganizationDecision(
        action=action,
        section_id=section_id,
        chapter_id=chapter_id,
        confidence=_confidence(data.get("confidence")),
        reason=reason,
    )
