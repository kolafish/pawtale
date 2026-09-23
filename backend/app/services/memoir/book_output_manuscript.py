from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from fastapi import HTTPException

from app.models.memoir import MemoirBookOutputVersion
from app.schemas.memoir_book import (
    MemoirBookCoverStyle,
    MemoirBookCustomLayout,
    MemoirBookManuscript,
    MemoirBookManuscriptBackMatter,
    MemoirBookManuscriptChapter,
    MemoirBookManuscriptProfile,
    MemoirBookManuscriptQuote,
    MemoirBookManuscriptRenderSettings,
    MemoirBookManuscriptSection,
    MemoirBookManuscriptSourceRef,
    MemoirBookManuscriptTemplate,
    MemoirBookOutputSectionResponse,
    MemoirBookOutputSectionTableOfContentsItemResponse,
    MemoirBookOutputTableOfContentsItemResponse,
)
from app.services.memoir.book_manuscript_ai import GeneratedManuscriptCopy
from app.services.memoir.book_manuscript_cleanup import (
    expanded_afterword,
    fallback_back_cover_quote,
    fallback_chapter_intro,
    fallback_preface,
    formal_book_title as cleanup_formal_book_title,
    sanitize_manuscript_payload,
)


DEFAULT_BOOK_COVER_STYLE = "classic_plain"
BOOK_PROFILE_TEXT_FIELDS = ("subtitle", "narrator_name", "prepared_by")
BOOK_SECTION_TEXT_FIELDS = ("preface", "afterword", "back_cover_quote")
BOOK_INFORMATION_LABELS = {
    "title": "书名",
    "subtitle": "副标题",
    "narrator_name": "口述人",
    "prepared_by": "整理人",
    "preface": "前言",
    "afterword": "后记",
    "back_cover_quote": "封底文案",
}
BOOK_EDITORIAL_COPY_TARGETS = {
    "preface",
    "afterword",
    "back_cover_quote",
    "chapter_intros",
    "pull_quotes",
}
SUPPORTED_BOOK_COVER_STYLES = {
    "classic_plain",
    "warm_gray",
    "dark_album",
    "moonlit_courtyard",
    "navy_gilt",
    "heritage_classic",
    "warm_journal",
    "modern_minimal",
}
TEMPLATE_COVER_STYLES: dict[MemoirBookManuscriptTemplate, MemoirBookCoverStyle] = {
    "heritage_classic": "heritage_classic",
    "warm_journal": "warm_journal",
    "modern_minimal": "modern_minimal",
}
MEMOIR_PROFILE_METADATA_KEY = "memoir_profile"
BOOK_MANUSCRIPT_TEMPLATES = {
    "classic_family",
    "photo_memory",
    "oral_archive",
    "heritage_classic",
    "warm_journal",
    "modern_minimal",
    "custom",
}


def positive_int(value: object) -> int:
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


def safe_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def book_information_text(information: dict[str, str]) -> str:
    return "\n".join(
        f"{label}：{information.get(field, '') or '（未填写）'}"
        for field, label in BOOK_INFORMATION_LABELS.items()
    )


def _section_responses(value: object) -> list[MemoirBookOutputSectionResponse]:
    if not isinstance(value, list):
        return []
    sections: list[MemoirBookOutputSectionResponse] = []
    for fallback_index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        section_id = safe_str(item.get("section_id"))
        title = safe_str(item.get("title"))
        content = safe_str(item.get("content"))
        if not section_id or not title or not content:
            continue
        raw_source_ref_ids = item.get("used_material_ids")
        source_ref_ids = [
            source_ref_id
            for raw_source_ref_id in (
                raw_source_ref_ids if isinstance(raw_source_ref_ids, list) else []
            )
            if (source_ref_id := safe_str(raw_source_ref_id))
        ]
        sections.append(
            MemoirBookOutputSectionResponse(
                section_id=section_id,
                index=positive_int(item.get("section_index")) or fallback_index,
                title=title,
                section_type=safe_str(item.get("section_type")),
                content=content,
                char_count=positive_int(item.get("content_char_count"))
                or len(content),
                source_count=positive_int(item.get("source_count")),
                source_ref_ids=source_ref_ids,
            )
        )
    return sections


def _section_toc_items(
    sections: list[MemoirBookOutputSectionResponse],
) -> list[MemoirBookOutputSectionTableOfContentsItemResponse]:
    return [
        MemoirBookOutputSectionTableOfContentsItemResponse(
            section_id=section.section_id,
            index=section.index,
            title=section.title,
            char_count=section.char_count,
            source_count=section.source_count,
        )
        for section in sections
    ]


def limited_book_text(value: object, *, max_chars: int) -> str:
    text = safe_str(value)
    return text[:max_chars] if len(text) > max_chars else text


def formal_book_title(raw_title: str) -> str:
    return cleanup_formal_book_title(raw_title)


def cover_style(value: str | None) -> str:
    return value if value in SUPPORTED_BOOK_COVER_STYLES else DEFAULT_BOOK_COVER_STYLE


def template_cover_style(template: MemoirBookManuscriptTemplate | None) -> MemoirBookCoverStyle | None:
    return TEMPLATE_COVER_STYLES.get(template) if template is not None else None


def project_narrator_name(project: object) -> str:
    metadata = getattr(project, "metadata_json", None)
    profile = metadata.get(MEMOIR_PROFILE_METADATA_KEY) if isinstance(metadata, dict) else None
    return limited_book_text(profile.get("narrator_name"), max_chars=80) if isinstance(profile, dict) else ""


def _brief_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split())
    return normalized if len(normalized) <= max_chars else normalized[:max_chars].rstrip("，。；、,. ") + "。"


def _first_sentence(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split())
    for index, char in enumerate(normalized):
        if char in "。！？!?":
            return _brief_text(normalized[: index + 1], max_chars=max_chars)
    return _brief_text(normalized, max_chars=max_chars) if normalized else ""


def _strip_title_wrappers(title: str) -> str:
    return title.strip().strip("《》「」“”\"' ")


def _short_topic(title: str) -> str:
    topic = _strip_title_wrappers(title)
    for word in ("与", "和", "里的", "中的"):
        if word in topic and len(topic) > 7:
            topic = topic.split(word)[0]
            break
    return topic[:8]


def fallback_title_suggestions(base_title: str, count: int) -> list[str]:
    formal_title = formal_book_title(base_title)
    subject = formal_title.removesuffix("的回忆录").removesuffix("回忆录").strip("的") or "家人"
    candidates = [
        formal_title,
        f"{subject}的一生",
        f"{subject}慢慢说",
        f"{subject}口述回忆录",
        "我走过的这些年",
        "一生的来路",
        "从前的日子",
        "那些年我记得",
        "家里的回忆录",
    ]
    result: list[str] = []
    for candidate in candidates:
        cleaned = formal_book_title(candidate)
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) >= count:
            break
    return result


def _fallback_cover_subtitle(chapter_titles: list[str]) -> str:
    topics = [_short_topic(title) for title in chapter_titles if _short_topic(title)]
    if len(topics) >= 2:
        return f"关于{'、'.join(topics[:3])}的记忆"
    return "根据口述访谈整理而成"


def _today_year_text() -> str:
    today = datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date()
    return f"{today.year} 年"


def _title_page_content(*, subtitle: str, narrator_name: str, prepared_by: str) -> str:
    lines = [
        subtitle,
        f"口述：{narrator_name}" if narrator_name else "",
        f"整理：{prepared_by}" if prepared_by else "",
        _today_year_text(),
    ]
    return "\n\n".join(line for line in lines if line)


def _front_section_content(manuscript: dict[str, object], kind: str) -> str:
    sections = manuscript.get("front_matter")
    if not isinstance(sections, list):
        return ""
    for section in sections:
        if isinstance(section, dict) and section.get("kind") == kind:
            return safe_str(section.get("content"))
    return ""


def _back_section_content(manuscript: dict[str, object], kind: str) -> str:
    back_matter = manuscript.get("back_matter")
    section = back_matter.get(kind) if isinstance(back_matter, dict) else None
    return safe_str(section.get("content")) if isinstance(section, dict) else ""


def stored_manuscript_metadata(stored: object) -> dict[str, str]:
    if not isinstance(stored, dict):
        return {}
    profile = stored.get("profile")
    settings = stored.get("render_settings")
    return {
        "subtitle": safe_str(profile.get("subtitle")) if isinstance(profile, dict) else "",
        "narrator_name": safe_str(profile.get("narrator_name")) if isinstance(profile, dict) else "",
        "prepared_by": safe_str(profile.get("prepared_by")) if isinstance(profile, dict) else "",
        "preface": _front_section_content(stored, "preface"),
        "afterword": _back_section_content(stored, "afterword"),
        "back_cover_quote": safe_str(settings.get("back_cover_quote")) if isinstance(settings, dict) else "",
    }


def stored_manuscript_design(stored: object) -> tuple[MemoirBookManuscriptTemplate, MemoirBookCustomLayout]:
    manuscript = stored if isinstance(stored, dict) else {}
    profile = manuscript.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    template = profile.get("template")
    if not isinstance(template, str) or template not in BOOK_MANUSCRIPT_TEMPLATES:
        template = "classic_family"
    settings = manuscript.get("render_settings")
    settings = settings if isinstance(settings, dict) else {}
    try:
        custom_layout = MemoirBookCustomLayout.model_validate(settings.get("custom_layout") or {})
    except ValueError:
        custom_layout = MemoirBookCustomLayout()
    return template, custom_layout


def request_book_metadata_patch(req: object) -> dict[str, str]:
    fields_set = getattr(req, "model_fields_set", set())
    patch: dict[str, str] = {}
    for field in (*BOOK_PROFILE_TEXT_FIELDS, *BOOK_SECTION_TEXT_FIELDS):
        if field not in fields_set or getattr(req, field, None) is None:
            continue
        max_chars = 160 if field == "back_cover_quote" else 1200 if field in {"preface", "afterword"} else 180
        patch[field] = limited_book_text(getattr(req, field), max_chars=max_chars)
    return patch


def effective_book_metadata(
    *,
    project: object | None,
    stored: object = None,
    patch: dict[str, str] | None = None,
    include_empty: bool = False,
) -> dict[str, str]:
    metadata = {"prepared_by": "家人整理"}
    narrator_name = project_narrator_name(project) if project is not None else ""
    if narrator_name:
        metadata["narrator_name"] = narrator_name
    metadata.update({key: value for key, value in stored_manuscript_metadata(stored).items() if value})
    metadata.update(patch or {})
    metadata["prepared_by"] = metadata.get("prepared_by") or "家人整理"
    if include_empty:
        for key in (*BOOK_PROFILE_TEXT_FIELDS, *BOOK_SECTION_TEXT_FIELDS):
            metadata.setdefault(key, "")
    return metadata


def _manuscript_source_refs(raw_refs: object) -> list[MemoirBookManuscriptSourceRef]:
    refs: list[MemoirBookManuscriptSourceRef] = []
    for item in raw_refs if isinstance(raw_refs, list) else []:
        if not isinstance(item, dict):
            continue
        utterance_id = safe_str(item.get("utterance_id"))
        if utterance_id:
            refs.append(
                MemoirBookManuscriptSourceRef(
                    utterance_id=utterance_id,
                    transcript_version_id=safe_str(item.get("transcript_version_id")) or None,
                    voice_session_id=safe_str(item.get("voice_session_id")) or None,
                    quote=safe_str(item.get("quote")),
                )
            )
    return refs


def _chapter_pull_quotes(content: str, source_refs: list[MemoirBookManuscriptSourceRef]) -> list[MemoirBookManuscriptQuote]:
    for ref in source_refs:
        quote = _brief_text(ref.quote, max_chars=80)
        if quote:
            return [MemoirBookManuscriptQuote(text=quote, source_ref_ids=[ref.utterance_id], source="original_quote")]
    quote = _first_sentence(content, max_chars=80)
    return [MemoirBookManuscriptQuote(text=quote, source="editorial_generated")] if quote else []


def _generated_chapter_pull_quote(
    *,
    chapter_id: str,
    editorial_copy: GeneratedManuscriptCopy | None,
) -> MemoirBookManuscriptQuote | None:
    if not editorial_copy:
        return None
    for item in editorial_copy.chapter_intros:
        if item.chapter_id != chapter_id or not item.pull_quote:
            continue
        return MemoirBookManuscriptQuote(
            text=item.pull_quote,
            source_ref_ids=[item.pull_quote_source_ref_id] if item.pull_quote_source_ref_id else [],
            source="original_quote" if item.pull_quote_source_ref_id else "editorial_generated",
        )
    return None


def manuscript_from_snapshot(
    *,
    title: str,
    content: str,
    snapshot: list[dict[str, object]],
    editorial_copy: GeneratedManuscriptCopy | None = None,
    cover_style_value: str = DEFAULT_BOOK_COVER_STYLE,
    template: MemoirBookManuscriptTemplate = "classic_family",
    custom_layout: MemoirBookCustomLayout | None = None,
    metadata: dict[str, str] | None = None,
    chapter_illustrations_by_chapter: dict[str, list[dict[str, object]]] | None = None,
    image_appendix: list[dict[str, object]] | None = None,
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
        chapter_id = safe_str(item.get("chapter_id"))
        chapter_title = safe_str(item.get("chapter_title")) or safe_str(item.get("title"))
        chapter_content = safe_str(item.get("content"))
        if not chapter_id or not chapter_title or not chapter_content:
            continue
        source_refs = _manuscript_source_refs(item.get("source_refs"))
        sections = _section_responses(item.get("sections"))
        chapter_index = positive_int(item.get("chapter_index")) or fallback_index
        toc.append(
            MemoirBookOutputTableOfContentsItemResponse(
                chapter_id=chapter_id,
                index=chapter_index,
                title=chapter_title,
                char_count=positive_int(item.get("content_char_count")) or len(chapter_content),
                source_count=positive_int(item.get("source_count")) or len(source_refs),
                sections=_section_toc_items(sections),
            )
        )
        generated_pull_quote = _generated_chapter_pull_quote(chapter_id=chapter_id, editorial_copy=editorial_copy)
        chapters.append(
            MemoirBookManuscriptChapter(
                chapter_id=chapter_id,
                index=chapter_index,
                title=chapter_title,
                intro=intro_by_chapter_id.get(chapter_id) or fallback_chapter_intro(chapter_title, chapter_content),
                pull_quotes=[generated_pull_quote] if generated_pull_quote else _chapter_pull_quotes(chapter_content, source_refs),
                content=chapter_content,
                sections=sections,
                source_refs=source_refs,
                illustrations=(chapter_illustrations_by_chapter or {}).get(chapter_id, []),
            )
        )
    if not chapters:
        return None

    metadata = metadata or {}
    chapter_titles = [item.title for item in toc]
    subtitle = metadata.get("subtitle")
    if subtitle is None:
        subtitle = editorial_copy.cover_subtitle if editorial_copy and editorial_copy.cover_subtitle else _fallback_cover_subtitle(chapter_titles)
    narrator_name = metadata.get("narrator_name", "")
    book_title = title.strip() or "家庭回忆录"
    subtitle = safe_str(subtitle)
    prepared_by = metadata.get("prepared_by", "家人整理") or "家人整理"
    preface = metadata.get("preface")
    if preface is None:
        preface = editorial_copy.preface if editorial_copy and editorial_copy.preface else fallback_preface(chapter_titles)
    afterword = metadata.get("afterword")
    if afterword is None:
        afterword = editorial_copy.afterword if editorial_copy and editorial_copy.afterword else expanded_afterword()
    back_cover_quote = metadata.get("back_cover_quote")
    if back_cover_quote is None:
        back_cover_quote = editorial_copy.back_cover_quote if editorial_copy and editorial_copy.back_cover_quote else fallback_back_cover_quote(
            [chapter.model_dump(mode="json") for chapter in chapters]
        )
    source_count = sum(item.source_count for item in toc)
    return MemoirBookManuscript(
        profile=MemoirBookManuscriptProfile(
            template=template,
            title=book_title,
            subtitle=subtitle,
            narrator_name=narrator_name,
            prepared_by=prepared_by,
        ),
        front_matter=[
            MemoirBookManuscriptSection(kind="cover", title=book_title, content=f"{book_title}\n{subtitle}"),
            MemoirBookManuscriptSection(
                kind="title_page",
                title="扉页",
                content=_title_page_content(subtitle=subtitle, narrator_name=narrator_name, prepared_by=prepared_by),
            ),
            MemoirBookManuscriptSection(
                kind="generation_note",
                title="整理说明",
                content=(
                    "这本书由家人的口述材料整理而成，供家人保存与阅读。整理过程中保留原始讲述的事实边界，"
                    "对语句、段落和章节顺序做了必要的梳理，便于日后阅读和继续修订。\n\n"
                    "书中仍可能有时间、地点或姓名需要家人补充确认。若发现遗漏或不准确之处，"
                    "可以在后续版本中继续修改。"
                ),
            ),
            MemoirBookManuscriptSection(kind="preface", title="写在前面", content=preface),
        ],
        table_of_contents=toc,
        chapters=chapters,
        image_appendix=image_appendix or [],
        back_matter=MemoirBookManuscriptBackMatter(
            afterword=MemoirBookManuscriptSection(kind="afterword", title="后记", content=afterword),
            interview_records=MemoirBookManuscriptSection(
                kind="interview_records",
                title="口述片段",
                content=f"本书当前收录 {len(chapters)} 章，共整理自 {source_count} 条口述片段。",
            ),
            version_note=MemoirBookManuscriptSection(
                kind="version_note",
                title="版本与修订",
                content="本书正文采用适合阅读的整理版表达。原始口述材料已保留，便于家人日后继续补充、核对和修订。",
            ),
            unconfirmed_facts=MemoirBookManuscriptSection(
                kind="unconfirmed_facts",
                title="未确认信息",
                content="当前没有单独标记的未确认信息。后续可在章节编辑中补充时间、地点或姓名待确认项。",
            ),
            source_appendix=MemoirBookManuscriptSection(
                kind="source_appendix",
                title="口述片段",
                content="\n".join(f"第{item.index}章「{item.title}」：{item.source_count} 条口述片段" for item in toc),
            ),
        ),
        render_settings=MemoirBookManuscriptRenderSettings(
            cover_style=cover_style(cover_style_value),
            back_cover_quote=back_cover_quote,
            editorial_copy_model=editorial_copy.model if editorial_copy else None,
            custom_layout=custom_layout or MemoirBookCustomLayout(),
        ),
    )


def manuscript_response(
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
            try:
                return MemoirBookManuscript.model_validate(
                    sanitize_manuscript_payload(title=title, manuscript=stored)
                )
            except ValueError:
                pass
    return manuscript_from_snapshot(title=title, content=content, snapshot=snapshot)


def editable_manuscript(
    *,
    title: str,
    content: str,
    snapshot: list[dict[str, object]],
    stored: object,
) -> MemoirBookManuscript | None:
    if isinstance(stored, dict) and stored:
        try:
            manuscript = MemoirBookManuscript.model_validate(
                sanitize_manuscript_payload(title=title, manuscript=stored)
            )
            manuscript.schema_version = 3
            return manuscript
        except ValueError:
            pass
    return manuscript_from_snapshot(title=title, content=content, snapshot=snapshot)


def updated_manuscript_payload(
    *,
    title: str,
    stored: object,
    content: str,
    snapshot: list[dict[str, object]],
    cover_style_value: str | None,
    metadata_patch: dict[str, str] | None = None,
    manuscript_template: MemoirBookManuscriptTemplate | None = None,
    custom_layout: MemoirBookCustomLayout | None = None,
) -> dict[str, object]:
    manuscript = editable_manuscript(
        title=title,
        content=content,
        snapshot=snapshot,
        stored=stored,
    )
    if not manuscript:
        return {}
    metadata = effective_book_metadata(project=None, stored=stored, patch=metadata_patch, include_empty=True)
    manuscript.profile.title = title
    if manuscript_template is not None:
        manuscript.profile.template = manuscript_template
    if custom_layout is not None:
        manuscript.render_settings.custom_layout = custom_layout
    manuscript.profile.subtitle = metadata["subtitle"]
    manuscript.profile.narrator_name = metadata["narrator_name"]
    manuscript.profile.prepared_by = metadata["prepared_by"]
    if cover_style_value:
        manuscript.render_settings.cover_style = cover_style(cover_style_value)
    manuscript.render_settings.back_cover_quote = metadata["back_cover_quote"]
    for section in manuscript.front_matter:
        if section.kind == "cover":
            section.title = title
            section.content = f"{title}\n{metadata['subtitle']}"
        elif section.kind == "title_page":
            section.content = _title_page_content(
                subtitle=metadata["subtitle"],
                narrator_name=metadata["narrator_name"],
                prepared_by=metadata["prepared_by"],
            )
        elif section.kind == "preface":
            section.content = metadata["preface"]
    manuscript.back_matter.afterword.content = metadata["afterword"]
    return manuscript.model_dump(mode="json")


def target_set(targets: list[str]) -> set[str]:
    requested = {target for target in targets if target in BOOK_EDITORIAL_COPY_TARGETS}
    return requested or set(BOOK_EDITORIAL_COPY_TARGETS)


def chapter_id_set(chapter_ids: list[str]) -> set[str]:
    return {chapter_id for chapter_id in chapter_ids if chapter_id}


def _set_preface(manuscript: MemoirBookManuscript, value: str) -> None:
    for section in manuscript.front_matter:
        if section.kind == "preface":
            section.content = value
            return
    manuscript.front_matter.append(MemoirBookManuscriptSection(kind="preface", title="写在前面", content=value))


def apply_editorial_copy(
    manuscript: MemoirBookManuscript,
    editorial_copy: GeneratedManuscriptCopy,
    *,
    targets: set[str],
    chapter_ids: set[str],
) -> None:
    if "preface" in targets and editorial_copy.preface:
        _set_preface(manuscript, editorial_copy.preface)
    if "afterword" in targets and editorial_copy.afterword:
        manuscript.back_matter.afterword.content = editorial_copy.afterword
    if "back_cover_quote" in targets and editorial_copy.back_cover_quote:
        manuscript.render_settings.back_cover_quote = editorial_copy.back_cover_quote
    if editorial_copy.model:
        manuscript.render_settings.editorial_copy_model = editorial_copy.model
    generated_by_chapter = {item.chapter_id: item for item in editorial_copy.chapter_intros if item.chapter_id}
    for chapter in manuscript.chapters:
        if chapter_ids and chapter.chapter_id not in chapter_ids:
            continue
        generated = generated_by_chapter.get(chapter.chapter_id)
        if not generated:
            continue
        if "chapter_intros" in targets and generated.intro:
            chapter.intro = generated.intro
        if "pull_quotes" in targets and generated.pull_quote:
            chapter.pull_quotes = [
                MemoirBookManuscriptQuote(
                    text=generated.pull_quote,
                    source_ref_ids=[generated.pull_quote_source_ref_id] if generated.pull_quote_source_ref_id else [],
                    source="original_quote" if generated.pull_quote_source_ref_id else "editorial_generated",
                )
            ]


def find_manuscript_chapter(manuscript: MemoirBookManuscript, chapter_id: str) -> MemoirBookManuscriptChapter:
    for chapter in manuscript.chapters:
        if chapter.chapter_id == chapter_id:
            return chapter
    raise HTTPException(status_code=404, detail="章节不存在")


def manual_pull_quote_source_ids(chapter: MemoirBookManuscriptChapter, source_ref_id: str | None) -> list[str]:
    if source_ref_id:
        return [source_ref_id]
    for quote in chapter.pull_quotes:
        if quote.source_ref_ids:
            return quote.source_ref_ids
    return []


def update_chapter_editorial_copy(
    manuscript: MemoirBookManuscript,
    chapter_id: str,
    *,
    fields_set: set[str],
    intro: str | None,
    pull_quote: str | None,
    pull_quote_source_ref_id: str | None,
) -> None:
    chapter = find_manuscript_chapter(manuscript, chapter_id)
    if "intro" in fields_set:
        chapter.intro = intro or ""
    if "pull_quote" in fields_set:
        source_ref_id = pull_quote_source_ref_id if "pull_quote_source_ref_id" in fields_set else None
        if source_ref_id and not any(ref.utterance_id == source_ref_id for ref in chapter.source_refs):
            raise HTTPException(status_code=400, detail="章首引语来源不属于当前章节")
        quote_text = pull_quote or ""
        chapter.pull_quotes = [
            MemoirBookManuscriptQuote(
                text=quote_text,
                source_ref_ids=manual_pull_quote_source_ids(chapter, source_ref_id),
                source="manual",
            )
        ] if quote_text else []
    elif "pull_quote_source_ref_id" in fields_set and pull_quote_source_ref_id:
        if not any(ref.utterance_id == pull_quote_source_ref_id for ref in chapter.source_refs):
            raise HTTPException(status_code=400, detail="章首引语来源不属于当前章节")
        if chapter.pull_quotes:
            chapter.pull_quotes[0].source_ref_ids = [pull_quote_source_ref_id]
            chapter.pull_quotes[0].source = "manual"


def stored_cover_style(version: MemoirBookOutputVersion | None) -> str:
    if not version or not isinstance(version.manuscript_json, dict):
        return DEFAULT_BOOK_COVER_STYLE
    settings = version.manuscript_json.get("render_settings")
    if not isinstance(settings, dict):
        return DEFAULT_BOOK_COVER_STYLE
    return cover_style(settings.get("cover_style") if isinstance(settings.get("cover_style"), str) else None)
