from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.schemas.memoir_book import (
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookManuscript,
    MemoirBookManuscriptBackMatter,
    MemoirBookManuscriptChapter,
    MemoirBookManuscriptProfile,
    MemoirBookManuscriptQuote,
    MemoirBookManuscriptRenderSettings,
    MemoirBookManuscriptSection,
    MemoirBookManuscriptSourceRef,
    MemoirBookOutputGenerateRequest,
    MemoirBookOutputResponse,
    MemoirBookOutputStructuredChapterResponse,
    MemoirBookOutputStructuredContentResponse,
    MemoirBookOutputTableOfContentsItemResponse,
    MemoirBookOutputVersionResponse,
)
from app.services.file_exports import ExportFile
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio import (
    audio_cache_payload,
    audio_response,
    cached_audio_response,
    discard_staged_audio_files,
    effective_tts_speed_ratio,
    source_text_hash,
    synthesize_text_audio,
)
from app.services.memoir.book_manuscript_ai import (
    GeneratedManuscriptCopy,
    generate_manuscript_editorial_copy,
)
from app.services.memoir.book_output_export import (
    MemoirBookOutputExportFormat,
    MemoirBookOutputExportLayout,
    render_book_output_export,
)
from app.services.memoir.voice_tts import TTSClient


BOOK_TTS_CACHE_KEY = "book_tts"
AI_MODEL_METADATA_KEY = "ai_model"
SNAPSHOT_COMPARE_KEYS = (
    "chapter_id",
    "chapter_title",
    "chapter_sort_order",
    "draft_id",
    "draft_version_id",
    "draft_version",
    "content_char_count",
)


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        try:
            return max(int(value), 0)
        except ValueError:
            return 0
    return 0


def _optional_positive_int(value: object) -> int | None:
    result = _positive_int(value)
    return result or None


def _safe_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _structured_content_response(
    *,
    title: str,
    content: str,
    snapshot: list[dict[str, object]],
) -> MemoirBookOutputStructuredContentResponse | None:
    chapters: list[MemoirBookOutputStructuredChapterResponse] = []
    for fallback_index, item in enumerate(snapshot, start=1):
        chapter_id = _safe_str(item.get("chapter_id"))
        chapter_title = _safe_str(item.get("chapter_title")) or _safe_str(item.get("title"))
        chapter_content = _safe_str(item.get("content"))
        if not chapter_id or not chapter_title or not chapter_content:
            continue
        chapters.append(
            MemoirBookOutputStructuredChapterResponse(
                chapter_id=chapter_id,
                index=_positive_int(item.get("chapter_index")) or fallback_index,
                title=chapter_title,
                content=chapter_content,
                char_count=_positive_int(item.get("content_char_count")) or len(chapter_content),
                source_count=_positive_int(item.get("source_count")),
                draft_version_id=_safe_str(item.get("draft_version_id")) or None,
                draft_version=_optional_positive_int(item.get("draft_version")),
            )
        )
    if not chapters:
        return None
    table_of_contents = [
        MemoirBookOutputTableOfContentsItemResponse(
            chapter_id=chapter.chapter_id,
            index=chapter.index,
            title=chapter.title,
            char_count=chapter.char_count,
            source_count=chapter.source_count,
        )
        for chapter in chapters
    ]
    return MemoirBookOutputStructuredContentResponse(
        title=title,
        chapter_count=len(chapters),
        char_count=len(content.strip()),
        table_of_contents=table_of_contents,
        chapters=chapters,
    )


def _brief_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip("，。；、,. ") + "。"


def _first_sentence(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split())
    if not normalized:
        return ""
    for index, char in enumerate(normalized):
        if char in "。！？!?":
            return _brief_text(normalized[:index + 1], max_chars=max_chars)
    return _brief_text(normalized, max_chars=max_chars)


def _strip_title_wrappers(title: str) -> str:
    return title.strip().strip("《》「」“”\"' ")


def _formal_book_title(raw_title: str) -> str:
    title = _strip_title_wrappers(raw_title.replace("章节规划", "")).strip(" -_·：:")
    if not title:
        return "家庭回忆录"
    title = title.replace("主题回忆录", "回忆录").replace("主题", "").strip(" -_·：:")
    if title in {"回忆录", "我的回忆录"}:
        return "家庭回忆录" if title == "回忆录" else title
    if title.endswith("的回忆录"):
        return title
    if title.endswith("回忆录"):
        subject = title[: -len("回忆录")].strip(" -_·：:的")
        return f"{subject}的回忆录" if subject else "家庭回忆录"
    return title


def _short_topic(title: str) -> str:
    topic = _strip_title_wrappers(title)
    for word in ("与", "和", "里的", "中的"):
        if word in topic and len(topic) > 7:
            topic = topic.split(word)[0]
            break
    return topic[:8]


def _fallback_cover_subtitle(chapter_titles: list[str]) -> str:
    topics = [_short_topic(title) for title in chapter_titles if _short_topic(title)]
    if len(topics) >= 2:
        return f"关于{'、'.join(topics[:3])}的记忆"
    return "根据口述访谈整理而成"


def _fallback_preface(chapter_titles: list[str]) -> str:
    topics = [_short_topic(title) for title in chapter_titles if _short_topic(title)]
    if len(topics) >= 2:
        return (
            f"这些讲述从{topics[0]}开始，也写到{topics[1]}"
            f"{'、' + topics[2] if len(topics) > 2 else ''}。"
            "它们不是宏大的传记，而是一个人愿意说给家人听的生活细节。"
            "整理成书，是为了让这些声音、场景和名字，在日后还能被慢慢翻起。"
        )
    return (
        "这是一段由口述慢慢展开的人生记忆。整理时尽量保留讲述里的事实、语气和细节，"
        "只对语句和段落做必要梳理，方便家人日后阅读、补充和修订。"
    )


def _fallback_back_cover_quote(chapters: list[MemoirBookManuscriptChapter]) -> str:
    for chapter in chapters:
        for quote in chapter.pull_quotes:
            text = _brief_text(quote.text, max_chars=42)
            if text:
                return text
    return "把亲口说出的生活，尽量完整地留下来。"


def _manuscript_source_refs(raw_refs: object) -> list[MemoirBookManuscriptSourceRef]:
    if not isinstance(raw_refs, list):
        return []
    refs: list[MemoirBookManuscriptSourceRef] = []
    for item in raw_refs:
        if not isinstance(item, dict):
            continue
        utterance_id = _safe_str(item.get("utterance_id"))
        if not utterance_id:
            continue
        refs.append(
            MemoirBookManuscriptSourceRef(
                utterance_id=utterance_id,
                transcript_version_id=_safe_str(item.get("transcript_version_id")) or None,
                voice_session_id=_safe_str(item.get("voice_session_id")) or None,
                quote=_safe_str(item.get("quote")),
            )
        )
    return refs


def _chapter_intro(title: str, content: str, editorial_intro: str | None = None) -> str:
    if editorial_intro:
        return editorial_intro
    excerpt = _first_sentence(content, max_chars=70)
    if not excerpt:
        return f"记忆在「{title}」里慢慢展开，留下讲述者对这段经历的片段和语气。"
    return f"关于「{title}」，记忆先从一个清楚的画面开始：{excerpt}"


def _chapter_pull_quotes(content: str, source_refs: list[MemoirBookManuscriptSourceRef]) -> list[MemoirBookManuscriptQuote]:
    for ref in source_refs:
        quote = _brief_text(ref.quote, max_chars=80)
        if quote:
            return [MemoirBookManuscriptQuote(text=quote, source_ref_ids=[ref.utterance_id])]
    quote = _first_sentence(content, max_chars=80)
    return [MemoirBookManuscriptQuote(text=quote)] if quote else []


def _manuscript_from_snapshot(
    *,
    title: str,
    content: str,
    snapshot: list[dict[str, object]],
    editorial_copy: GeneratedManuscriptCopy | None = None,
) -> MemoirBookManuscript | None:
    if not snapshot:
        return None
    toc: list[MemoirBookOutputTableOfContentsItemResponse] = []
    chapters: list[MemoirBookManuscriptChapter] = []
    intro_by_chapter_id = {
        item.chapter_id: item.intro
        for item in (editorial_copy.chapter_intros if editorial_copy else [])
        if item.chapter_id and item.intro
    }
    for fallback_index, item in enumerate(snapshot, start=1):
        chapter_id = _safe_str(item.get("chapter_id"))
        chapter_title = _safe_str(item.get("chapter_title")) or _safe_str(item.get("title"))
        chapter_content = _safe_str(item.get("content"))
        if not chapter_id or not chapter_title or not chapter_content:
            continue
        chapter_index = _positive_int(item.get("chapter_index")) or fallback_index
        source_refs = _manuscript_source_refs(item.get("source_refs"))
        char_count = _positive_int(item.get("content_char_count")) or len(chapter_content)
        source_count = _positive_int(item.get("source_count")) or len(source_refs)
        toc.append(
            MemoirBookOutputTableOfContentsItemResponse(
                chapter_id=chapter_id,
                index=chapter_index,
                title=chapter_title,
                char_count=char_count,
                source_count=source_count,
            )
        )
        chapters.append(
            MemoirBookManuscriptChapter(
                chapter_id=chapter_id,
                index=chapter_index,
                title=chapter_title,
                intro=_chapter_intro(chapter_title, chapter_content, intro_by_chapter_id.get(chapter_id)),
                pull_quotes=_chapter_pull_quotes(chapter_content, source_refs),
                content=chapter_content,
                source_refs=source_refs,
            )
        )
    if not chapters:
        return None
    chapter_count = len(chapters)
    formal_title = _formal_book_title(editorial_copy.book_title if editorial_copy and editorial_copy.book_title else title)
    chapter_titles = [item.title for item in toc]
    subtitle = (
        editorial_copy.cover_subtitle
        if editorial_copy and editorial_copy.cover_subtitle
        else _fallback_cover_subtitle(chapter_titles)
    )
    preface = (
        editorial_copy.preface
        if editorial_copy and editorial_copy.preface
        else _fallback_preface(chapter_titles)
    )
    back_cover_quote = (
        editorial_copy.back_cover_quote
        if editorial_copy and editorial_copy.back_cover_quote
        else _fallback_back_cover_quote(chapters)
    )
    front_matter = [
        MemoirBookManuscriptSection(
            kind="cover",
            title=formal_title,
            content=f"{formal_title}\n{subtitle}",
        ),
        MemoirBookManuscriptSection(
            kind="title_page",
            title="扉页",
            content=f"{subtitle}\n\n整理：家人整理\n仅供家人保存与阅读",
        ),
        MemoirBookManuscriptSection(
            kind="generation_note",
            title="整理说明",
            content=(
                "这本书由家人的口述材料整理而成。整理过程中保留原始讲述的事实边界，"
                "对语句、段落和章节顺序做了必要的梳理，便于日后阅读和继续修订。\n\n"
                "书中仍可能有时间、地点或姓名需要家人补充确认。若发现遗漏或不准确之处，"
                "可以在后续版本中继续修改。"
            ),
        ),
        MemoirBookManuscriptSection(
            kind="preface",
            title="写在前面",
            content=preface,
        ),
    ]
    source_count = sum(item.source_count for item in toc)
    back_matter = MemoirBookManuscriptBackMatter(
        afterword=MemoirBookManuscriptSection(
            kind="afterword",
            title="后记",
            content="这些讲述有些清楚，有些还需要家人继续补充。能先留下来，已经是这本书最重要的意义。",
        ),
        interview_records=MemoirBookManuscriptSection(
            kind="interview_records",
            title="讲述资料",
            content=f"本版共整理 {chapter_count} 章正文，关联 {source_count} 条口述记录。后续可以继续补充访谈时间、地点和参与整理的人。",
        ),
        version_note=MemoirBookManuscriptSection(
            kind="version_note",
            title="版本与修订",
            content=(
                "本书正文采用适合阅读的整理版表达。原始口述材料已保留，"
                "便于家人日后继续补充、核对和修订。"
            ),
        ),
        unconfirmed_facts=MemoirBookManuscriptSection(
            kind="unconfirmed_facts",
            title="未确认信息",
            content="当前没有单独标记的未确认信息。后续可在章节编辑中补充时间、地点或姓名待确认项。",
        ),
        source_appendix=MemoirBookManuscriptSection(
            kind="source_appendix",
            title="口述来源索引",
            content="\n".join(f"第{item.index}章「{item.title}」：{item.source_count} 条口述记录" for item in toc),
        ),
    )
    return MemoirBookManuscript(
        profile=MemoirBookManuscriptProfile(
            title=formal_title,
            subtitle=subtitle,
            prepared_by="家人整理",
        ),
        front_matter=front_matter,
        table_of_contents=toc,
        chapters=chapters,
        back_matter=back_matter,
        render_settings=MemoirBookManuscriptRenderSettings(
            back_cover_quote=back_cover_quote,
            editorial_copy_model=editorial_copy.model if editorial_copy else None,
        ),
    )


def _manuscript_response(
    *,
    title: str,
    content: str,
    snapshot: list[dict[str, object]],
    stored: object,
) -> MemoirBookManuscript | None:
    if isinstance(stored, dict) and stored:
        try:
            return MemoirBookManuscript.model_validate(stored)
        except ValueError:
            pass
    return _manuscript_from_snapshot(title=title, content=content, snapshot=snapshot)


def _project_ai_model(project: object) -> str | None:
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(AI_MODEL_METADATA_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _remember_project_ai_model(project: object, ai_model: str | None) -> None:
    model = (ai_model or "").strip()
    if not model:
        return
    metadata = getattr(project, "metadata_json", None)
    setattr(
        project,
        "metadata_json",
        {
            **(metadata if isinstance(metadata, dict) else {}),
            AI_MODEL_METADATA_KEY: model,
        },
    )


def _latest_chapter_version(draft: MemoirBookChapterDraft | None) -> MemoirBookChapterDraftVersion | None:
    if not draft or not draft.versions:
        return None
    return draft.versions[-1]


def _output_version_response(version: MemoirBookOutputVersion, *, title: str) -> MemoirBookOutputVersionResponse:
    snapshot = [
        item for item in (version.chapter_snapshot_json or [])
        if isinstance(item, dict)
    ]
    manuscript = _manuscript_response(
        title=title,
        content=version.content,
        snapshot=snapshot,
        stored=version.manuscript_json,
    )
    return MemoirBookOutputVersionResponse(
        id=version.id,
        output_id=version.output_id,
        project_id=version.project_id,
        plan_id=version.plan_id,
        version=version.version,
        content=version.content,
        chapter_snapshot_json=snapshot,
        structured_content=_structured_content_response(
            title=title,
            content=version.content,
            snapshot=snapshot,
        ),
        manuscript=manuscript,
        created_by=MemoirCreatedBy(version.created_by),
        created_at=version.created_at,
    )


def _output_response(
    output: MemoirBookOutput,
    *,
    stale_chapter_titles: list[str] | None = None,
) -> MemoirBookOutputResponse:
    versions = [_output_version_response(version, title=output.title) for version in output.versions]
    stale_titles = stale_chapter_titles or []
    return MemoirBookOutputResponse(
        id=output.id,
        project_id=output.project_id,
        plan_id=output.plan_id,
        title=output.title,
        status=MemoirOutputStatus(output.status),
        metadata_json=output.metadata_json or {},
        is_stale=bool(stale_titles),
        stale_chapter_titles=stale_titles,
        latest_version=versions[-1] if versions else None,
        versions=versions,
        created_at=output.created_at,
        updated_at=output.updated_at,
    )


async def _load_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookPlan:
    await load_memoir_project(db, project_id, user_id)
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
        .execution_options(populate_existing=True)
    )
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return plan


def _require_confirmed_chapters(plan: MemoirBookPlan) -> list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]]:
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")
    chapters = sorted(plan.chapters, key=lambda item: item.sort_order)
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")

    missing: list[str] = []
    confirmed: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]] = []
    for chapter in chapters:
        version = _latest_chapter_version(chapter.draft)
        if not chapter.draft or chapter.draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value or not version:
            missing.append(chapter.title)
            continue
        confirmed.append((chapter, version))
    if missing:
        preview = "、".join(missing[:3])
        suffix = "等章节" if len(missing) > 3 else ""
        raise HTTPException(status_code=400, detail=f"请先确认所有章节：{preview}{suffix}")
    return confirmed


def _compose_book_content(
    *,
    title: str,
    chapters: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]],
) -> tuple[str, list[dict[str, object]]]:
    toc = "\n".join(f"{index}. {chapter.title}" for index, (chapter, _) in enumerate(chapters, start=1))
    body_parts: list[str] = []
    snapshot: list[dict[str, object]] = []
    for index, (chapter, version) in enumerate(chapters, start=1):
        content = version.content.strip()
        body_parts.append(f"第{index}章 {chapter.title}\n\n{content}")
        snapshot.append(_chapter_snapshot(chapter, version, chapter_index=index))
    content = f"{title}\n\n目录\n{toc}\n\n" + "\n\n".join(body_parts)
    return content.strip(), snapshot


def _chapter_snapshot(
    chapter: MemoirBookPlanChapter,
    version: MemoirBookChapterDraftVersion,
    *,
    chapter_index: int,
) -> dict[str, object]:
    content = version.content.strip()
    source_refs = chapter.source_refs_json if isinstance(chapter.source_refs_json, list) else []
    source_ref_snapshot = [
        {
            "utterance_id": _safe_str(item.get("utterance_id")),
            "transcript_version_id": _safe_str(item.get("transcript_version_id")) or None,
            "voice_session_id": _safe_str(item.get("voice_session_id")) or None,
            "quote": _safe_str(item.get("quote")),
        }
        for item in source_refs
        if isinstance(item, dict) and _safe_str(item.get("utterance_id"))
    ]
    return {
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "chapter_index": chapter_index,
        "chapter_sort_order": chapter.sort_order,
        "draft_id": chapter.draft.id if chapter.draft else "",
        "draft_version_id": version.id,
        "draft_version": version.version,
        "content_char_count": len(content),
        "source_count": len(source_ref_snapshot),
        "source_refs": source_ref_snapshot,
        "content": content,
    }


def _current_book_snapshot(plan: MemoirBookPlan) -> tuple[list[dict[str, object]], list[str]]:
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        return [], ["章节规划"]
    chapters = sorted(plan.chapters, key=lambda item: item.sort_order)
    snapshot: list[dict[str, object]] = []
    unavailable_titles: list[str] = []
    for index, chapter in enumerate(chapters, start=1):
        version = _latest_chapter_version(chapter.draft)
        if not chapter.draft or chapter.draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value or not version:
            unavailable_titles.append(chapter.title)
            continue
        snapshot.append(_chapter_snapshot(chapter, version, chapter_index=index))
    return snapshot, unavailable_titles


def _snapshot_stale_chapter_titles(
    latest_version: MemoirBookOutputVersion | None,
    current_snapshot: list[dict[str, object]],
    unavailable_titles: list[str],
) -> list[str]:
    if unavailable_titles:
        return unavailable_titles
    if not latest_version:
        return []
    stored_snapshot = [
        item for item in (latest_version.chapter_snapshot_json or [])
        if isinstance(item, dict)
    ]
    stored_by_chapter_id = {
        item.get("chapter_id"): item
        for item in stored_snapshot
        if isinstance(item.get("chapter_id"), str)
    }
    stale_titles: list[str] = []
    for current in current_snapshot:
        chapter_id = current.get("chapter_id")
        stored = stored_by_chapter_id.get(chapter_id)
        if not stored or any(stored.get(key) != current.get(key) for key in SNAPSHOT_COMPARE_KEYS):
            stale_titles.append(str(current.get("chapter_title") or "未命名章节"))
    if len(stored_snapshot) != len(current_snapshot) and not stale_titles:
        stale_titles.append("章节结构")
    return stale_titles


def _output_stale_chapter_titles(output: MemoirBookOutput, plan: MemoirBookPlan) -> list[str]:
    current_snapshot, unavailable_titles = _current_book_snapshot(plan)
    latest_version = output.versions[-1] if output.versions else None
    return _snapshot_stale_chapter_titles(latest_version, current_snapshot, unavailable_titles)


async def _latest_output_version_number(db: AsyncSession, output_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirBookOutputVersion.version)).where(
            MemoirBookOutputVersion.output_id == output_id
        )
    )
    return int(value or 0)


async def _load_output(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookOutput | None:
    return await db.scalar(
        select(MemoirBookOutput)
        .where(MemoirBookOutput.project_id == project_id)
        .options(selectinload(MemoirBookOutput.versions))
        .execution_options(populate_existing=True)
    )


async def get_book_output(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookOutputResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    plan = await _load_plan(db, user_id=user_id, project_id=project_id)
    return _output_response(output, stale_chapter_titles=_output_stale_chapter_titles(output, plan))


async def generate_book_output(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookOutputGenerateRequest,
) -> MemoirBookOutputResponse:
    project = await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, user_id=user_id, project_id=project_id)
    chapters = _require_confirmed_chapters(plan)
    title = _formal_book_title(plan.title)
    content, snapshot = _compose_book_content(title=title, chapters=chapters)
    requested_ai_model = (req.ai_model or "").strip()
    ai_model = requested_ai_model or _project_ai_model(project)
    _remember_project_ai_model(project, req.ai_model)
    editorial_copy = (
        await generate_manuscript_editorial_copy(
            title=title,
            snapshot=snapshot,
            ai_model=ai_model,
        )
        if ai_model
        else None
    )
    if editorial_copy and editorial_copy.book_title:
        generated_title = _formal_book_title(editorial_copy.book_title)
        if generated_title and generated_title != title:
            title = generated_title
            content, snapshot = _compose_book_content(title=title, chapters=chapters)

    output = await _load_output(db, project_id=project_id)
    stale_titles = (
        _snapshot_stale_chapter_titles(output.versions[-1], snapshot, [])
        if output and output.versions
        else []
    )
    if output and output.versions and not req.force_regenerate and not stale_titles:
        return _output_response(output)

    if not output:
        output = MemoirBookOutput(
            project_id=project_id,
            plan_id=plan.id,
            title=title,
            status=MemoirOutputStatus.DRAFT.value,
            metadata_json={},
        )
        db.add(output)
        await db.flush()
    else:
        output.plan_id = plan.id
        output.title = title
        output.status = MemoirOutputStatus.DRAFT.value
        output.metadata_json = {
            **(output.metadata_json or {}),
            BOOK_TTS_CACHE_KEY: None,
        }

    manuscript = _manuscript_from_snapshot(
        title=title,
        content=content,
        snapshot=snapshot,
        editorial_copy=editorial_copy,
    )
    version_number = await _latest_output_version_number(db, output.id) + 1
    db.add(
        MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=plan.id,
            version=version_number,
            content=content,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript.model_dump(mode="json") if manuscript else {},
            created_by=MemoirCreatedBy.SYSTEM.value,
        )
    )
    await db.commit()
    refreshed = await _load_output(db, project_id=project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="全书稿保存失败")
    return _output_response(refreshed)


def _select_output_version(
    output: MemoirBookOutput,
    *,
    version_id: str | None = None,
) -> MemoirBookOutputVersion | None:
    versions = sorted(output.versions, key=lambda item: item.version)
    if not versions:
        return None
    if version_id:
        return next((version for version in versions if version.id == version_id), None)
    return versions[-1]


async def export_book_output_file(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    export_format: MemoirBookOutputExportFormat,
    layout: MemoirBookOutputExportLayout = MemoirBookOutputExportLayout.STANDARD,
    version_id: str | None = None,
    show_source_appendix: bool | None = None,
) -> ExportFile:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    version = _select_output_version(output, version_id=version_id)
    if not version:
        raise HTTPException(status_code=404, detail="全书版本不存在")
    content = version.content.strip()
    return render_book_output_export(
        title=output.title,
        version=version.version,
        content=content,
        manuscript=version.manuscript_json or {},
        show_source_appendix=show_source_appendix,
        export_format=export_format,
        layout=layout,
    )


async def synthesize_book_output_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookAudioRequest,
    tts_client: TTSClient | None = None,
) -> MemoirBookAudioResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await _load_output(db, project_id=project_id)
    if not output or not output.versions:
        raise HTTPException(status_code=404, detail="全书稿不存在")
    latest = output.versions[-1]
    text = latest.content.strip()
    text_hash = source_text_hash(text)
    speed_ratio = effective_tts_speed_ratio(req.speed_ratio)
    cached = cached_audio_response(
        output.metadata_json or {},
        cache_key=BOOK_TTS_CACHE_KEY,
        text_hash=text_hash,
        voice=req.voice,
        speed_ratio=speed_ratio,
        force_regenerate=req.force_regenerate,
    )
    if cached:
        return cached

    synthesis = await synthesize_text_audio(
        db,
        user_id=user_id,
        text=text,
        voice=req.voice,
        speed_ratio=req.speed_ratio,
        tts_client=tts_client,
    )
    try:
        output.metadata_json = {
            **(output.metadata_json or {}),
            BOOK_TTS_CACHE_KEY: audio_cache_payload(synthesis, voice=req.voice, speed_ratio=speed_ratio),
        }
        db.add(output)
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio_files(synthesis.staged_audio)
        raise
    return audio_response(synthesis)
