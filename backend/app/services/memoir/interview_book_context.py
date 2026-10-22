"""Whole-book context for the AI interviewer: who is narrating, people, chapters, the last chapter and life topics."""

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
from app.services.memoir.narrative import frameworks, interview_topics
from app.services.memoir import completion_interviews, narration_people, narrator_profile
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
    # Name, gender, age and hometown from the project profile, e.g. "王秀英，女，72 岁，家乡山东".
    narrator: str = ""
    # The profile item to ask, then the one to ask if the narrator already gave it; only in a new book's first
    # two narrations.
    profile_questions: tuple[str, ...] = ()
    # Earlier profile exchanges remain available while asynchronous reading catches up.
    profile_answers: tuple[narrator_profile.ProfileAnswer, ...] = ()
    # People the narrator has talked about, most recently mentioned first.
    people: tuple[str, ...] = ()
    # One gap about a person 小余 may ask once a story is told; never while profile items are left.
    person_question: narration_people.Question | None = None
    completion_offer: completion_interviews.OpeningCompletionOffer | None = None
    starts_new_story: bool = False


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
        anchor_item_id=await interview_topics.current_framework_item_id(db, voice_session),
    )
    project = await db.get(Project, voice_session.project_id)
    profile = (project.metadata_json or {}).get(MEMOIR_PROFILE_METADATA_KEY) if project else None
    profile = profile if isinstance(profile, dict) else {}
    profile_context = await narrator_profile.profile_question_context_for(db, voice_session, project)
    profile_questions = profile_context.questions
    starts_new_story = anchor_topic is None and interview_topics.is_new_story_entry(voice_session)
    user_messages = [
        message
        for message in voice_session.messages
        if message.role == "user" and message.content_text.strip()
    ]
    latest_user_message = max(
        user_messages,
        key=lambda message: (message.turn_index, message.created_at, message.id),
        default=None,
    )
    await completion_interviews.record_supplement_decline(
        db,
        voice_session=voice_session,
        latest_user_message=latest_user_message,
    )
    opening_completion_offer_allowed = (
        interview_topics.explicitly_requests_supplement(
            latest_user_message.content_text if latest_user_message else ""
        )
        or (
            not interview_topics.is_home_voice_entry(voice_session)
            and latest_user_message is None
        )
    )
    return InterviewBookContext(
        chapters=chapters,
        last_chapter_title=await _last_chapter_title(db, voice_session.project_id),
        topics=topics,
        anchor_topic=anchor_topic,
        narrator=_narrator(profile),
        profile_questions=profile_questions,
        profile_answers=profile_context.previous_answers,
        people=await narration_people.recent_people_lines(db, voice_session.project_id),
        person_question=None if profile_questions else await narration_people.question_for(db, voice_session),
        completion_offer=(await completion_interviews.offer_for_opening(db, voice_session=voice_session))
        if chapters and not profile_questions and anchor_topic is None
        and voice_session.session_goal == "open_narration"
        and voice_session.interview_type == "ai_interview"
        and opening_completion_offer_allowed
        else None,
        starts_new_story=starts_new_story,
    )


def _narrator(profile: dict) -> str:
    parts = []
    name = str(profile.get("narrator_name") or "").strip()
    if name:
        parts.append(name)
    gender = _NARRATOR_GENDERS.get(str(profile.get("narrator_gender") or ""))
    if gender:
        parts.append(gender)
    age = profile.get("narrator_age")
    if isinstance(age, int) and age > 0:
        parts.append(f"{age} 岁")
    origin = str(profile.get("narrator_origin") or "").strip()
    if origin:
        parts.append(f"家乡{origin}")
    return "，".join(parts)


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
