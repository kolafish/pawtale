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
REVISION_PLAN_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录修订编辑，只判断新增口述材料应该如何并入已有章节。"
EVENT_CLUSTER_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录结构编辑，只根据来源讲述记录判断哪些记录讲的是同一件事。"
AI_BOOK_DRAFT_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"


@dataclass(frozen=True)
class ChapterSourceText:
    ref: MemoirBookChapterSourceRef
    text: str


@dataclass(frozen=True)
class ChapterRevisionPlan:
    mode: Literal["supplement", "regenerate", "needs_review"]
    reason: str
    pending_source_ids: list[str]
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


def _source_block(sources: list[ChapterSourceText], pending_ids: set[str]) -> str:
    return "\n\n".join(
        (
            f"source {index}\n"
            f"source_type: {'新增来源' if source.ref.utterance_id in pending_ids else '既有来源'}\n"
            f"utterance_id: {source.ref.utterance_id}\n"
            f"quote: {source.ref.quote}\n"
            f"transcript: {source.text}"
        )
        for index, source in enumerate(sources, start=1)
    )


def _build_revision_plan_prompt(
    *,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    previous_content: str,
    pending_source_utterance_ids: set[str],
) -> str:
    return f"""请判断本章新增来源应该如何并入现有章节正文。

章节标题参考：{chapter.title}

现有章节正文：
{previous_content.strip()}

来源讲述记录：
{_source_block(sources, pending_source_utterance_ids)}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "mode": "supplement",
  "reason": "判断理由",
  "pending_source_ids": ["新增来源 utterance_id"],
  "related_existing_source_ids": ["相关既有来源 utterance_id"],
  "coverage_requirements": ["正文生成时必须覆盖的要点"]
}}

判断规则：
- mode=supplement：新增来源属于本章，但与现有正文关系较弱，适合作为新段落或补充细节自然加入。
- mode=regenerate：新增来源与现有正文讲的是同一件事、同一人物/地点/时间段，或会修正现有叙述，需要重新组织整章。
- mode=needs_review：新增来源明显不属于本章，或缺少足够关系，应该先由用户调整章节归属。
- 不要新增来源没有讲过的事实；判断必须基于来源讲述记录和现有正文。
"""


def _revision_plan_from_content(content: str, pending_source_utterance_ids: set[str]) -> ChapterRevisionPlan:
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
        related_existing_source_ids=_string_list(payload.get("related_existing_source_ids")),
        coverage_requirements=_string_list(payload.get("coverage_requirements")),
    )


def _revision_plan_prompt_text(revision_plan: ChapterRevisionPlan | None) -> str:
    if not revision_plan:
        return ""
    requirements = "\n".join(f"- {item}" for item in revision_plan.coverage_requirements)
    related_ids = ", ".join(revision_plan.related_existing_source_ids) or "无"
    pending_ids = ", ".join(revision_plan.pending_source_ids) or "无"
    return (
        "\n修订规划：\n"
        f"mode: {revision_plan.mode}\n"
        f"reason: {revision_plan.reason or '无'}\n"
        f"pending_source_ids: {pending_ids}\n"
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
    revision_plan: ChapterRevisionPlan | None = None,
    event_clusters: list[ChapterEventCluster] | None = None,
) -> str:
    pending_ids = pending_source_utterance_ids or set()
    source_text = _source_block(sources, pending_ids)
    feedback_text = f"\n用户反馈：{feedback.strip()}\n" if feedback else ""
    previous_text = (
        f"\n原章节正文（作为补写基础，尽量保留原有表达和段落）：\n{previous_content.strip()}\n"
        if previous_content
        else ""
    )
    revision_text = _revision_plan_prompt_text(revision_plan)
    event_cluster_text = _event_clusters_prompt_text(event_clusters or [])
    supplement_text = (
        "\n本次任务：只把 source_type 为“新增来源”的讲述记录里用户确实讲过的内容自然补入原章节正文；"
        "没有新增来源支撑的内容不要扩写，既有段落不要无故重写。\n"
        if pending_ids and previous_content
        else ""
    )
    if pending_ids and previous_content:
        coverage_text = (
            f"\n来源覆盖要求：本章共有 {len(sources)} 条来源；本次必须覆盖所有 source_type 为“新增来源”的内容，"
            "既有来源只用于衔接和核对。即使章节标题更像旧内容，也不能忽略新增来源。\n"
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
{revision_text}
{event_cluster_text}
{supplement_text}
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
    force_regenerate: bool,
) -> GeneratedChapterDraft:
    revision_plan: ChapterRevisionPlan | None = None
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
        )
        plan_content = await client._call_llm(
            REVISION_PLAN_SYSTEM_PROMPT,
            plan_prompt,
            temperature=0.1,
            max_tokens=700,
            timeout=60.0,
        )
        revision_plan = _revision_plan_from_content(str(plan_content), pending_source_utterance_ids)
        if revision_plan.mode == "needs_review":
            raise HTTPException(status_code=409, detail="新增来源与本章关系不明确，请先调整章节归属")

    should_supplement_pending_sources = revision_plan is not None and revision_plan.mode == "supplement"
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

    draft_previous_content = previous_content if should_supplement_pending_sources else None
    draft_pending_ids = pending_source_utterance_ids if should_supplement_pending_sources else set()
    prompt = _build_draft_prompt(
        chapter=chapter,
        sources=sources,
        feedback=feedback,
        previous_content=draft_previous_content,
        pending_source_utterance_ids=draft_pending_ids,
        revision_plan=revision_plan,
        event_clusters=event_clusters,
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
