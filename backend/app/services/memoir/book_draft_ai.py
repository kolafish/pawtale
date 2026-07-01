from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException

from app.models.memoir import MemoirBookPlanChapter
from app.schemas.memoir_book import MemoirBookChapterSourceRef
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.book_plan_sources import text_or_empty


SYSTEM_PROMPT = "你是一位谨慎的中文回忆录整理写作者，只能把给定口述材料整理成章节正文。"
REVISION_PLAN_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录修订编辑，只判断口述材料变化应该如何修订已有章节。"
EVENT_CLUSTER_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录结构编辑，只根据来源讲述记录判断哪些记录讲的是同一件事。"
AI_BOOK_DRAFT_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"


@dataclass(frozen=True)
class ChapterSourceCardContext:
    source_card_id: str
    summary: str = ""
    people: list[str] | None = None
    places: list[str] | None = None
    time_hints: list[str] | None = None
    event_tags: list[str] | None = None
    key_facts: list[str] | None = None
    open_questions: list[str] | None = None


@dataclass(frozen=True)
class ChapterCardPromptContext:
    chapter_card_id: str
    summary: str = ""
    covered: dict[str, object] | None = None
    gaps: list[str] | None = None
    next_questions: list[str] | None = None


@dataclass(frozen=True)
class RemovedChapterSourceText:
    utterance_id: str
    quote: str = ""
    text: str = ""


@dataclass(frozen=True)
class ChapterSourceText:
    ref: MemoirBookChapterSourceRef
    text: str
    change_type: Literal["existing", "new", "changed"] = "existing"
    source_card: ChapterSourceCardContext | None = None


@dataclass(frozen=True)
class ChapterRevisionPlan:
    mode: Literal["supplement", "regenerate", "needs_review"]
    reason: str
    pending_source_ids: list[str]
    removed_source_ids: list[str]
    related_existing_source_ids: list[str]
    coverage_requirements: list[str]


@dataclass(frozen=True)
class ChapterEventCluster:
    title: str
    source_ids: list[str]
    summary: str


@dataclass(frozen=True)
class GeneratedChapterDraft:
    content: str
    prompt_snapshot: str


def _strip_generated_text(text: str) -> str:
    content = text.strip()
    if len(content) >= 2 and content[0] in {"“", "「", '"', "'"} and content[-1] in {"”", "」", '"', "'"}:
        content = content[1:-1].strip()
    return content


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _change_type_label(source: ChapterSourceText, pending_ids: set[str]) -> str:
    if source.ref.utterance_id not in pending_ids:
        return "既有来源"
    if source.change_type == "changed":
        return "文本已变化来源"
    return "新增来源"


def _source_card_text(card: ChapterSourceCardContext | None) -> str:
    if not card:
        return "source_card: 无"
    return "\n".join(
        [
            f"source_card_id: {card.source_card_id}",
            f"source_card_summary: {card.summary or '无'}",
            f"source_card_people: {card.people or []}",
            f"source_card_places: {card.places or []}",
            f"source_card_time_hints: {card.time_hints or []}",
            f"source_card_event_tags: {card.event_tags or []}",
            f"source_card_key_facts: {card.key_facts or []}",
            f"source_card_open_questions: {card.open_questions or []}",
        ]
    )


def _source_block(sources: list[ChapterSourceText], pending_ids: set[str]) -> str:
    return "\n\n".join(
        (
            f"source {index}\n"
            f"source_type: {_change_type_label(source, pending_ids)}\n"
            f"utterance_id: {source.ref.utterance_id}\n"
            f"{_source_card_text(source.source_card)}\n"
            f"quote: {source.ref.quote}\n"
            f"transcript: {source.text}"
        )
        for index, source in enumerate(sources, start=1)
    )


def _removed_source_block(removed_sources: list[RemovedChapterSourceText]) -> str:
    if not removed_sources:
        return "无"
    return "\n\n".join(
        (
            f"removed_source {index}\n"
            f"utterance_id: {source.utterance_id}\n"
            f"quote: {source.quote or '无'}\n"
            f"transcript: {source.text or '无'}"
        )
        for index, source in enumerate(removed_sources, start=1)
    )


def _chapter_card_prompt_text(chapter_card: ChapterCardPromptContext | None) -> str:
    if not chapter_card:
        return ""
    gaps = "\n".join(f"- {item}" for item in (chapter_card.gaps or []))
    questions = "\n".join(f"- {item}" for item in (chapter_card.next_questions or []))
    return (
        "\n当前章节卡片：\n"
        f"chapter_card_id: {chapter_card.chapter_card_id}\n"
        f"summary: {chapter_card.summary or '无'}\n"
        f"covered: {chapter_card.covered or {}}\n"
        f"gaps:\n{gaps or '- 无'}\n"
        f"next_questions:\n{questions or '- 无'}\n"
    )


def _build_revision_plan_prompt(
    *,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    previous_content: str,
    pending_source_utterance_ids: set[str],
    removed_sources: list[RemovedChapterSourceText],
    chapter_card: ChapterCardPromptContext | None = None,
) -> str:
    removed_ids = {source.utterance_id for source in removed_sources}
    return f"""请判断本章来源变化应该如何修订现有章节正文。

章节标题参考：{chapter.title}
{_chapter_card_prompt_text(chapter_card)}

现有章节正文：
{previous_content.strip()}

当前仍保留的来源讲述记录：
{_source_block(sources, pending_source_utterance_ids)}

已删除来源：
{_removed_source_block(removed_sources)}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "mode": "supplement",
  "reason": "判断理由",
  "pending_source_ids": ["新增或文本已变化来源 utterance_id"],
  "removed_source_ids": ["已删除来源 utterance_id"],
  "related_existing_source_ids": ["相关既有来源 utterance_id"],
  "coverage_requirements": ["正文生成时必须覆盖的要点"]
}}

判断规则：
- mode=supplement：新增或文本已变化来源属于本章，但与现有正文关系较弱，适合作为新段落或补充细节自然加入，且没有删除来源需要处理。
- mode=regenerate：新增/变化来源与现有正文讲的是同一件事、同一人物/地点/时间段，会修正现有叙述，或存在已删除来源需要从正文中移除，需要重新组织整章。
- mode=needs_review：新增来源明显不属于本章，或缺少足够关系，应该先由用户调整章节归属。
- 不要保留仅由已删除来源支撑的内容；判断必须基于当前来源讲述记录、已删除来源和现有正文。
- removed_source_ids 只能来自已删除来源列表；当前列表为：{", ".join(sorted(removed_ids)) or "无"}。
"""


def _revision_plan_from_content(
    content: str,
    pending_source_utterance_ids: set[str],
    removed_source_utterance_ids: set[str],
) -> ChapterRevisionPlan:
    payload = extract_json_payload(content)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节修订规划")
    mode = payload.get("mode")
    if mode not in {"supplement", "regenerate", "needs_review"}:
        raise HTTPException(status_code=502, detail="AI 未返回有效章节修订模式")
    pending_ids = _string_list(payload.get("pending_source_ids")) or sorted(pending_source_utterance_ids)
    return ChapterRevisionPlan(
        mode=mode,
        reason=text_or_empty(payload.get("reason") if isinstance(payload.get("reason"), str) else ""),
        pending_source_ids=pending_ids,
        removed_source_ids=_string_list(payload.get("removed_source_ids")) or sorted(removed_source_utterance_ids),
        related_existing_source_ids=_string_list(payload.get("related_existing_source_ids")),
        coverage_requirements=_string_list(payload.get("coverage_requirements")),
    )


def _revision_plan_prompt_text(revision_plan: ChapterRevisionPlan | None) -> str:
    if not revision_plan:
        return ""
    requirements = "\n".join(f"- {item}" for item in revision_plan.coverage_requirements)
    related_ids = ", ".join(revision_plan.related_existing_source_ids) or "无"
    pending_ids = ", ".join(revision_plan.pending_source_ids) or "无"
    removed_ids = ", ".join(revision_plan.removed_source_ids) or "无"
    return (
        "\n修订规划：\n"
        f"mode: {revision_plan.mode}\n"
        f"reason: {revision_plan.reason or '无'}\n"
        f"pending_source_ids: {pending_ids}\n"
        f"removed_source_ids: {removed_ids}\n"
        f"related_existing_source_ids: {related_ids}\n"
        f"coverage_requirements:\n{requirements or '- 覆盖所有来源中的具体事实'}\n"
    )


def _build_event_cluster_prompt(
    *,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
) -> str:
    return f"""请把本章来源讲述记录中讲同一件事、同一人物关系、同一地点或同一时间段的记录聚合到一起。

章节标题参考：{chapter.title}

来源讲述记录：
{_source_block(sources, set())}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "clusters": [
    {{
      "title": "事件或主题名",
      "source_ids": ["utterance_id"],
      "summary": "这些来源共同讲了什么"
    }}
  ]
}}

要求：
- source_ids 必须来自上面给定的 utterance_id。
- 每条来源都必须出现在一个 cluster 中，不能遗漏。
- 如果某条来源没有相关记录，可以单独成为一个 cluster。
- 只根据来源讲述记录聚合，不新增事实。
"""


def _event_clusters_from_content(content: str, sources: list[ChapterSourceText]) -> list[ChapterEventCluster]:
    payload = extract_json_payload(content)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节事件聚合")
    raw_clusters = payload.get("clusters")
    if not isinstance(raw_clusters, list):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节事件列表")

    valid_source_ids = {source.ref.utterance_id for source in sources}
    used_source_ids: set[str] = set()
    clusters: list[ChapterEventCluster] = []
    for item in raw_clusters:
        if not isinstance(item, dict):
            continue
        source_ids = [
            source_id
            for source_id in _string_list(item.get("source_ids"))
            if source_id in valid_source_ids and source_id not in used_source_ids
        ]
        if not source_ids:
            continue
        used_source_ids.update(source_ids)
        title = text_or_empty(item.get("title") if isinstance(item.get("title"), str) else "") or "相关回忆"
        summary = text_or_empty(item.get("summary") if isinstance(item.get("summary"), str) else "")
        clusters.append(ChapterEventCluster(title=title, source_ids=source_ids, summary=summary))

    for source in sources:
        if source.ref.utterance_id not in used_source_ids:
            clusters.append(
                ChapterEventCluster(
                    title="未归组来源",
                    source_ids=[source.ref.utterance_id],
                    summary=source.ref.quote or source.text[:120],
                )
            )
    return clusters


def _event_clusters_prompt_text(event_clusters: list[ChapterEventCluster]) -> str:
    if not event_clusters:
        return ""
    cluster_text = "\n".join(
        (
            f"- {cluster.title}\n"
            f"  source_ids: {', '.join(cluster.source_ids)}\n"
            f"  summary: {cluster.summary or '无'}"
        )
        for cluster in event_clusters
    )
    return (
        "\n事件聚合规划：\n"
        f"{cluster_text}\n"
        "组织要求：同一组 source_ids 应尽量写在相邻段落或同一段中；不同组之间再按时间、地点或情绪自然过渡。\n"
    )


def _build_draft_prompt(
    *,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    feedback: str | None,
    previous_content: str | None = None,
    pending_source_utterance_ids: set[str] | None = None,
    removed_sources: list[RemovedChapterSourceText] | None = None,
    revision_plan: ChapterRevisionPlan | None = None,
    event_clusters: list[ChapterEventCluster] | None = None,
    chapter_card: ChapterCardPromptContext | None = None,
) -> str:
    pending_ids = pending_source_utterance_ids or set()
    removed_source_items = removed_sources or []
    removed_ids = {source.utterance_id for source in removed_source_items}
    source_text = _source_block(sources, pending_ids)
    feedback_text = f"\n用户反馈：{feedback.strip()}\n" if feedback else ""
    previous_text = (
        f"\n原章节正文（作为修订基础，保留仍由当前来源支撑的表达和段落）：\n{previous_content.strip()}\n"
        if previous_content
        else ""
    )
    chapter_card_text = _chapter_card_prompt_text(chapter_card)
    revision_text = _revision_plan_prompt_text(revision_plan)
    event_cluster_text = _event_clusters_prompt_text(event_clusters or [])
    removed_source_text = (
        f"\n已删除来源（这些 utterance_id 对应内容不得继续作为本章事实依据）：\n"
        f"{_removed_source_block(removed_source_items)}\n"
        if removed_source_items
        else ""
    )
    if removed_ids and pending_ids and previous_content:
        task_text = (
            "\n本次任务：基于当前仍保留的来源修订原章节正文，移除已删除来源独有支撑的内容；"
            "同时只把 source_type 为“新增来源”或“文本已变化来源”的讲述记录里用户确实讲过的内容自然补入或修正原章节正文。"
            "不要为了填补被删除内容而新增未讲过的事实，可以保留当前来源仍能支撑的原有表达。\n"
        )
    elif removed_ids and previous_content:
        task_text = (
            "\n本次任务：基于当前仍保留的来源修订原章节正文，移除已删除来源独有支撑的内容；"
            "不要为了填补被删除内容而新增未讲过的事实。可以保留当前来源仍能支撑的原有表达。\n"
        )
    elif pending_ids and previous_content:
        task_text = (
            "\n本次任务：只把 source_type 为“新增来源”或“文本已变化来源”的讲述记录里用户确实讲过的内容自然补入或修正原章节正文；"
            "source_type 为“文本已变化来源”的记录应按最新 transcript/素材卡片修正原文；"
            "没有新增或变化来源支撑的内容不要扩写，既有段落不要无故重写。\n"
        )
    else:
        task_text = ""
    if (pending_ids or removed_ids) and previous_content:
        coverage_text = (
            f"\n来源覆盖要求：本章当前共有 {len(sources)} 条保留来源；"
            "本次必须覆盖所有 source_type 为“新增来源”或“文本已变化来源”的内容，"
            "既有来源用于衔接、核对和保留原文。"
            f"不得继续写入 removed_source_ids={sorted(removed_ids) or []} 独有支撑的内容。\n"
        )
    else:
        coverage_text = (
            f"\n来源覆盖要求：本章共有 {len(sources)} 条来源；正文必须覆盖每一条 source 中叙述者实际讲过的具体信息，"
            "不能只写第一条，也不能只写最贴合标题的一条。若章节标题与来源讲述记录不完全一致，以来源讲述记录为准，"
            "可以分段或用自然过渡串联不同主题。\n"
        )
    return f"""请把下面口述材料整理成一个回忆录章节正文。

章节标题参考：{chapter.title}
{feedback_text}
{previous_text}
{chapter_card_text}
{revision_text}
{event_cluster_text}
{removed_source_text}
{task_text}
{coverage_text}
写作要求：
- 必须以叙述者已讲述的内容为主，使用第一人称或贴近原话的叙述。
- 不新增事实、人物、时间、地点、事件、对话或情绪，不替用户补故事。
- 可以去掉明显口头填充词，调整顺序和标点，让文字更像书稿。
- 素材少就写短，不要为了篇幅扩写。
- 每条要求覆盖的来源至少要有一处可对应的具体内容进入正文。
- 不要输出标题、列表、解释或 Markdown，只输出正文。

来源讲述记录：
{source_text}
"""


async def generate_chapter_draft_text(
    client: AITextClient,
    *,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    feedback: str | None,
    previous_content: str | None,
    pending_source_utterance_ids: set[str],
    removed_sources: list[RemovedChapterSourceText] | None = None,
    chapter_card: ChapterCardPromptContext | None = None,
    force_regenerate: bool,
) -> GeneratedChapterDraft:
    revision_plan: ChapterRevisionPlan | None = None
    removed_source_items = removed_sources or []
    removed_source_ids = {source.utterance_id for source in removed_source_items}
    should_plan_pending_sources = (
        bool(pending_source_utterance_ids)
        and not force_regenerate
        and previous_content is not None
    )
    if should_plan_pending_sources:
        plan_prompt = _build_revision_plan_prompt(
            chapter=chapter,
            sources=sources,
            previous_content=previous_content or "",
            pending_source_utterance_ids=pending_source_utterance_ids,
            removed_sources=removed_source_items,
            chapter_card=chapter_card,
        )
        plan_content = await client._call_llm(
            REVISION_PLAN_SYSTEM_PROMPT,
            plan_prompt,
            temperature=0.1,
            max_tokens=700,
            timeout=60.0,
        )
        revision_plan = _revision_plan_from_content(
            str(plan_content),
            pending_source_utterance_ids,
            removed_source_ids,
        )
        if revision_plan.mode == "needs_review":
            raise HTTPException(status_code=409, detail="新增来源与本章关系不明确，请先调整章节归属")
    elif removed_source_ids and not force_regenerate and previous_content is not None:
        revision_plan = ChapterRevisionPlan(
            mode="regenerate",
            reason="本章有来源被删除，需要基于保留来源移除过期内容。",
            pending_source_ids=[],
            removed_source_ids=sorted(removed_source_ids),
            related_existing_source_ids=[],
            coverage_requirements=["移除已删除来源独有支撑的内容，保留当前来源仍能支撑的事实"],
        )

    should_supplement_pending_sources = (
        revision_plan is not None
        and revision_plan.mode == "supplement"
        and not removed_source_ids
    )
    event_clusters: list[ChapterEventCluster] = []
    if not should_supplement_pending_sources and len(sources) > 1:
        cluster_prompt = _build_event_cluster_prompt(chapter=chapter, sources=sources)
        cluster_content = await client._call_llm(
            EVENT_CLUSTER_SYSTEM_PROMPT,
            cluster_prompt,
            temperature=0.1,
            max_tokens=900,
            timeout=60.0,
        )
        event_clusters = _event_clusters_from_content(str(cluster_content), sources)

    should_label_pending_sources = (
        bool(pending_source_utterance_ids)
        and bool(removed_source_ids)
        and not force_regenerate
        and previous_content is not None
    )
    draft_previous_content = previous_content if (should_supplement_pending_sources or removed_source_ids) else None
    draft_pending_ids = (
        pending_source_utterance_ids
        if should_supplement_pending_sources or should_label_pending_sources
        else set()
    )
    prompt = _build_draft_prompt(
        chapter=chapter,
        sources=sources,
        feedback=feedback,
        previous_content=draft_previous_content,
        pending_source_utterance_ids=draft_pending_ids,
        removed_sources=removed_source_items,
        revision_plan=revision_plan,
        event_clusters=event_clusters,
        chapter_card=chapter_card,
    )
    content = await client._call_llm(
        SYSTEM_PROMPT,
        prompt,
        temperature=0.25,
        max_tokens=2600,
        timeout=120.0,
    )
    draft_text = _strip_generated_text(str(content))
    if not draft_text:
        raise HTTPException(status_code=502, detail="AI 未返回可用章节稿")
    return GeneratedChapterDraft(content=draft_text, prompt_snapshot=prompt)
