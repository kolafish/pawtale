"""Whole-book context for the AI interviewer: who is narrating, chapters, the last chapter and life topics."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.framework_templates import (
    DEFAULT_FRAMEWORK_TEMPLATE_KEY,
    DEFAULT_FRAMEWORK_TEMPLATE_VERSION,
    load_framework_template,
)
from app.domain.memoir.narrative_types import MemoirFrameworkUserResolution
from app.models.memoir import (
    MemoirBookPlanChapter,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_voice import MemoirProjectProgressSnapshotResponse
from app.services.memoir.narrative import frameworks
from app.services.memoir.projects import MEMOIR_PROFILE_METADATA_KEY

_NARRATOR_GENDERS = {"male": "男", "female": "女"}


@dataclass(frozen=True, slots=True)
class LifeTopic:
    title: str
    intent: str
    material_count: int

    @property
    def level(self) -> frameworks.FrameworkTopicLevel:
        return frameworks.topic_level(self.material_count)


@dataclass(frozen=True, slots=True)
class InterviewBookContext:
    chapters: tuple[tuple[str, int], ...]
    last_chapter_title: str
    # Topics the narrator marked as enough / not applicable / do not discuss are left out.
    topics: tuple[LifeTopic, ...]
    anchor_topic: LifeTopic | None
    # Age, gender and hometown the narrator set on the project, e.g. "男，72 岁，家乡山东".
    narrator: str = ""
    narrator_age_known: bool = False


async def load_interview_book_context(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    snapshot: MemoirProjectProgressSnapshotResponse | None,
) -> InterviewBookContext:
    chapters = tuple(
        (chapter.title, chapter.source_count)
        for chapter in (snapshot.chapter_progress_items if snapshot else [])
        if chapter.include_in_book
    )
    topics, anchor_topic = await _life_topics(
        db,
        project_id=voice_session.project_id,
        anchor_item_id=voice_session.anchor_framework_item_id,
    )
    project = await db.get(Project, voice_session.project_id)
    profile = (project.metadata_json or {}).get(MEMOIR_PROFILE_METADATA_KEY) if project else None
    narrator, narrator_age_known = _narrator(profile if isinstance(profile, dict) else {})
    return InterviewBookContext(
        chapters=chapters,
        last_chapter_title=await _last_chapter_title(db, voice_session.project_id),
        topics=topics,
        anchor_topic=anchor_topic,
        narrator=narrator,
        narrator_age_known=narrator_age_known,
    )


def _narrator(profile: dict) -> tuple[str, bool]:
    parts = []
    gender = _NARRATOR_GENDERS.get(str(profile.get("narrator_gender") or ""))
    if gender:
        parts.append(gender)
    age = profile.get("narrator_age")
    age_known = isinstance(age, int) and age > 0
    if age_known:
        parts.append(f"{age} 岁")
    origin = str(profile.get("narrator_origin") or "").strip()
    if origin:
        parts.append(f"家乡{origin}")
    return "，".join(parts), age_known


async def _life_topics(
    db: AsyncSession,
    *,
    project_id: str,
    anchor_item_id: str | None,
) -> tuple[tuple[LifeTopic, ...], LifeTopic | None]:
    framework = await frameworks.framework_for_project(db, project_id=project_id)
    if framework is None:
        template = load_framework_template(
            DEFAULT_FRAMEWORK_TEMPLATE_KEY,
            DEFAULT_FRAMEWORK_TEMPLATE_VERSION,
        )
        return tuple(
            LifeTopic(title=item.title, intent=item.intent, material_count=0)
            for item in sorted(template.items, key=lambda item: item.sort_order)
        ), None
    definitions = {
        item.item_key: item for item in frameworks.template_for_framework(framework).items
    }
    material_counts = await frameworks.primary_material_counts(db, project_framework_id=framework.id)
    topics: list[LifeTopic] = []
    anchor_topic = None
    for item in sorted(framework.items, key=lambda item: item.sort_order):
        definition = definitions[item.item_key]
        topic = LifeTopic(
            title=definition.title,
            intent=definition.intent,
            material_count=material_counts.get(item.id, 0),
        )
        if item.id == anchor_item_id:
            anchor_topic = topic
        if item.user_resolution == MemoirFrameworkUserResolution.NONE.value:
            topics.append(topic)
    return tuple(topics), anchor_topic


async def _last_chapter_title(db: AsyncSession, project_id: str) -> str:
    title = await db.scalar(
        select(MemoirBookPlanChapter.title)
        .join(MemoirSection, MemoirSection.chapter_id == MemoirBookPlanChapter.id)
        .join(MemoirSectionMaterial, MemoirSectionMaterial.section_id == MemoirSection.id)
        .join(MemoirVoiceUtterance, MemoirVoiceUtterance.id == MemoirSectionMaterial.utterance_id)
        .where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirBookPlanChapter.include_in_book.is_(True),
        )
        .order_by(MemoirVoiceUtterance.created_at.desc())
        .limit(1)
    )
    return title or ""
