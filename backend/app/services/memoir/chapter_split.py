from __future__ import annotations

from dataclasses import dataclass, replace
import json

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirSectionStatus
from app.models.memoir import MemoirBookPlanChapter, MemoirSection
from app.schemas.memoir_book import MemoirBookPlanStructureCommandRequest
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.book_plan_write_workflow import apply_book_plan_structure_command
from app.services.memoir.material_organization import material_organization_excerpt
from app.services.memoir.transcript_projection import current_utterance_text


SPLIT_MIN_SECTIONS = 5
SYSTEM_PROMPT = "你是一位有整体感的中文回忆录结构编辑，只调整章节划分，不改写任何内容。"


class _SplitGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    section_ids: list[str] = Field(default_factory=list)


class _SplitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[_SplitGroup] = Field(default_factory=list)


@dataclass(frozen=True)
class ChapterSplitGroup:
    title: str
    section_ids: list[str]


def splittable_sections(sections: list[MemoirSection]) -> list[MemoirSection]:
    return sorted(
        (
            section
            for section in sections
            if section.status == MemoirSectionStatus.ACTIVE.value
            and section.include_in_book
            and any(material.include_in_book for material in section.materials)
        ),
        key=lambda item: (item.sort_order, item.id),
    )


def build_chapter_split_prompt(
    *,
    chapter: MemoirBookPlanChapter,
    sections: list[MemoirSection],
    other_chapter_titles: list[str],
) -> tuple[str, ModelReferenceMap]:
    section_refs = ModelReferenceMap("S", (section.id for section in sections))
    section_payload = [
        {
            "section_id": section_refs.encode(section.id),
            "section_title": section.title,
            "materials": [
                material_organization_excerpt(current_utterance_text(material.utterance))
                for material in sorted(section.materials, key=lambda item: (item.sort_order, item.id))
                if material.include_in_book
            ][:4],
        }
        for section in sections
    ]
    prompt = (
        f"下面是回忆录中“{chapter.title}”这一章的全部小节。请判断这一章是否装了几个不同的人生阶段或长期主题，"
        "需要拆成几章。\n"
        "只在一章明显跨越不同人生阶段（例如童年、求学、工作、成家、晚年）或几个互不相干的长期主题时才拆；"
        "同一段经历讲得再细，也保持一章。一般拆成 2 到 4 章，每章至少两个小节。\n"
        "第一组保留在原章节，沿用原标题，放和原标题最贴近的小节；其余每组成为新章节，按人生先后排列。"
        "新章节标题用 2 到 8 个字概括这一阶段或主题（例如“求学与教书”“成家以后”），"
        "不要照抄小节标题，不和其他已有章节重名。每个小节只能出现一次。\n"
        "不需要拆分时只返回一组，包含全部小节。\n\n"
        f"其他已有章节：{json.dumps(other_chapter_titles, ensure_ascii=False)}\n\n"
        f"本章小节：\n{json.dumps(section_payload, ensure_ascii=False)}\n\n"
        '只返回 JSON：{"groups":[{"title":"","section_ids":["S1"]}]}'
    )
    return prompt, section_refs


def normalize_split_groups(
    output: _SplitOutput,
    *,
    chapter: MemoirBookPlanChapter,
    sections: list[MemoirSection],
    section_refs: ModelReferenceMap,
) -> list[ChapterSplitGroup]:
    order = {section.id: index for index, section in enumerate(sections)}
    used: set[str] = set()
    groups: list[ChapterSplitGroup] = []
    for raw in output.groups:
        ids = []
        for ref in raw.section_ids:
            try:
                section_id = section_refs.decode(ref.strip())
            except ValueError:
                continue
            if section_id in order and section_id not in used:
                used.add(section_id)
                ids.append(section_id)
        if ids:
            groups.append(ChapterSplitGroup(title=" ".join(raw.title.split())[:80], section_ids=ids))
    if not groups:
        return []
    missing = [section.id for section in sections if section.id not in used]
    section_titles = {section.title for section in sections}
    kept = [ChapterSplitGroup(title=chapter.title, section_ids=[*groups[0].section_ids, *missing])]
    for group in groups[1:]:
        if len(group.section_ids) >= 2 and group.title and group.title not in section_titles | {chapter.title}:
            kept.append(group)
        else:
            # Too small or badly named to stand alone: it stays with the chapter before it.
            kept[-1] = replace(kept[-1], section_ids=[*kept[-1].section_ids, *group.section_ids])
    kept = [replace(group, section_ids=sorted(group.section_ids, key=order.__getitem__)) for group in kept]
    return kept if len(kept) > 1 else []


async def decide_chapter_split(
    *,
    chapter: MemoirBookPlanChapter,
    sections: list[MemoirSection],
    other_chapter_titles: list[str],
    ai_model: str | None,
) -> list[ChapterSplitGroup]:
    sections = splittable_sections(sections)
    if len(sections) < SPLIT_MIN_SECTIONS:
        return []
    prompt, section_refs = build_chapter_split_prompt(
        chapter=chapter,
        sections=sections,
        other_chapter_titles=other_chapter_titles,
    )
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    generated = await generate_structured_with_client(
        get_ai_service(ai_model),
        SYSTEM_PROMPT,
        prompt,
        output_model=_SplitOutput,
        requested_model=ai_model,
        temperature=0.15,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    return normalize_split_groups(generated.value, chapter=chapter, sections=sections, section_refs=section_refs)


async def apply_chapter_split(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    groups: list[ChapterSplitGroup],
) -> None:
    # Each split inserts its new chapter right after the source, so going
    # backwards leaves the new chapters in the model's order.
    for group in reversed(groups[1:]):
        await apply_book_plan_structure_command(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookPlanStructureCommandRequest(
                type="split_chapter",
                chapter_id=chapter_id,
                title=group.title,
                section_ids=group.section_ids,
            ),
            commit=False,
        )
