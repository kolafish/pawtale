"""Deterministic writing corrections across one memoir (for example 于大姐 → 余大姐).

The narrator confirms an exact phrase pair. Every stored place that shows the phrase is
changed by plain substring replacement, never by a model rewrite. The original audio and
recognition text stay in history; the corrected writing becomes a user version and the
project glossary, so later narration is transcribed the same way.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from copy import deepcopy
import re

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirCreatedBy
from app.models.memoir import (
    MemoirBookChapterDraft, MemoirBookChapterDraftVersion, MemoirBookOutput, MemoirBookPlanChapter,
    MemoirChatMessage, MemoirSection, MemoirVoiceSession, MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookPlanStructureCommandRequest
from app.schemas.memoir_projects import MemoirProjectUpdate
from app.schemas.memoir_sections import MemoirSectionUpdateRequest
from app.services.memoir import chapter_generation, dialect_glossary, narration_people
from app.services.memoir.semantic_state import semantic_json_signature, semantic_text_hash
from app.services.memoir.transcript_projection import current_utterance_text

MAX_TITLE_CHARS = 80
SCOPE_LABELS = {
    "project_title": "回忆录名称",
    "narrator_name": "讲述人姓名",
    "chapter_title": "章节标题",
    "section_title": "小节标题",
    "transcript": "讲述文字",
    "chapter_body": "章节正文",
    "book_copy": "书稿文案",
}
SCOPE_UNITS = {
    "project_title": "处", "narrator_name": "处", "chapter_title": "处", "section_title": "处",
    "transcript": "条", "chapter_body": "章", "book_copy": "处",
}
_PEOPLE_FIELDS = ("call", "name", "relation", "note", "unclear")
# A span records where a target text was written: [start, target, source] in the new text.
Span = tuple[int, str, str]


class WritingConflict(Exception):
    """The stored text no longer matches what a correction wrote."""


@dataclass(frozen=True)
class WritingCorrection:
    source: str
    target: str


def validate_corrections(corrections: list[WritingCorrection]) -> None:
    sources = [item.source for item in corrections]
    if len(set(sources)) != len(sources):
        raise HTTPException(422, "同一个写法只能更正一次，请重新说明")
    for item in corrections:
        if len(item.source) < 2 or any(char.isspace() for char in item.source + item.target):
            raise HTTPException(422, "用字更正需要不含空格的完整词，原词至少两个字")
        if any(other.source != item.source and other.source in item.source for other in corrections):
            raise HTTPException(422, "几组更正的原词互相包含，请分开确认")
        if any(other.source in item.target for other in corrections):
            raise HTTPException(422, "新写法里不能包含要替换的原词，请重新说明")


def replace_text(text: str, corrections: list[WritingCorrection]) -> tuple[str, list[Span]]:
    targets = {item.source: item.target for item in corrections}
    pattern = re.compile("|".join(re.escape(source) for source in sorted(targets, key=len, reverse=True)))
    parts: list[str] = []
    spans: list[Span] = []
    cursor = 0
    length = 0
    for match in pattern.finditer(text):
        before = text[cursor:match.start()]
        parts.append(before)
        length += len(before)
        target = targets[match.group()]
        spans.append((length, target, match.group()))
        parts.append(target)
        length += len(target)
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts), spans


def revert_text(text: str, spans: list[Span]) -> str:
    result = text
    for start, target, source in sorted(spans, key=lambda span: span[0], reverse=True):
        if result[start:start + len(target)] != target:
            raise WritingConflict()
        result = result[:start] + source + result[start + len(target):]
    return result


def _contains(text: str | None, corrections: list[WritingCorrection]) -> bool:
    return bool(text) and any(item.source in text for item in corrections)


# --- Book copy fields: text the narrator may read in the book, outside chapter bodies.


def _copy_paths(manuscript: dict) -> Iterator[tuple]:
    profile = manuscript.get("profile")
    if isinstance(profile, dict):
        for key in ("subtitle", "narrator_name", "prepared_by"):
            yield ("profile", key)
    for index, section in enumerate(manuscript.get("front_matter") or []):
        if isinstance(section, dict):
            yield ("front_matter", index, "title")
            yield ("front_matter", index, "content")
    back_matter = manuscript.get("back_matter")
    if isinstance(back_matter, dict):
        for name, section in back_matter.items():
            if isinstance(section, dict):
                yield ("back_matter", name, "title")
                yield ("back_matter", name, "content")
    if isinstance(manuscript.get("render_settings"), dict):
        yield ("render_settings", "back_cover_quote")
    for index, chapter in enumerate(manuscript.get("chapters") or []):
        if not isinstance(chapter, dict):
            continue
        yield ("chapters", index, "intro")
        for quote_index, quote in enumerate(chapter.get("pull_quotes") or []):
            if isinstance(quote, dict):
                yield ("chapters", index, "pull_quotes", quote_index, "text")


def _get_path(value: object, path: tuple) -> object:
    for key in path:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and isinstance(key, int) and 0 <= key < len(value):
            value = value[key]
        else:
            return None
    return value


def _set_path(value: dict, path: tuple, text: str) -> None:
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = text


# --- Scan


@dataclass
class _Target:
    scope: str
    ref: str
    key: str
    text: str


@dataclass
class TermScan:
    targets: list[_Target] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, set[str]] = {}
        for target in self.targets:
            if target.scope in SCOPE_LABELS:
                result.setdefault(target.scope, set()).add(target.ref)
        return {scope: len(refs) for scope, refs in result.items()}

    def signature(self, corrections: list[WritingCorrection]) -> str:
        return semantic_json_signature({
            "corrections": [(item.source, item.target) for item in corrections],
            "targets": [(t.scope, t.ref, t.key, t.text) for t in self.targets],
        })


@dataclass
class _Book:
    project: Project
    chapters: list[MemoirBookPlanChapter]
    sections: list[MemoirSection]
    utterances: list[MemoirVoiceUtterance]
    latest_drafts: dict[str, MemoirBookChapterDraftVersion]
    output: MemoirBookOutput | None
    output_version: object | None


async def _load_book(db: AsyncSession, project: Project) -> _Book:
    from app.services.memoir.book_output_versions import load_latest_book_output_version

    chapters = (await db.scalars(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.project_id == project.id)
        .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
        .execution_options(populate_existing=True)
    )).all()
    sections = (await db.scalars(
        select(MemoirSection).where(MemoirSection.project_id == project.id)
        .order_by(MemoirSection.chapter_id, MemoirSection.sort_order, MemoirSection.id)
        .execution_options(populate_existing=True)
    )).all()
    utterances = (await db.scalars(
        select(MemoirVoiceUtterance).join(MemoirVoiceSession)
        .where(MemoirVoiceSession.project_id == project.id)
        .options(selectinload(MemoirVoiceUtterance.chat_message), selectinload(MemoirVoiceUtterance.voice_session))
        .order_by(MemoirVoiceUtterance.created_at, MemoirVoiceUtterance.id)
        .execution_options(populate_existing=True)
    )).all()
    drafts = (await db.scalars(
        select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.project_id == project.id)
        .distinct(MemoirBookChapterDraftVersion.chapter_id)
        .order_by(MemoirBookChapterDraftVersion.chapter_id, MemoirBookChapterDraftVersion.version.desc())
        .execution_options(populate_existing=True)
    )).all()
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project.id))
    output_version = await load_latest_book_output_version(db, output_id=output.id) if output else None
    return _Book(
        project=project, chapters=list(chapters), sections=list(sections), utterances=list(utterances),
        latest_drafts={draft.chapter_id: draft for draft in drafts}, output=output, output_version=output_version,
    )


def _narrator_name(project: Project) -> str:
    profile = (project.metadata_json or {}).get("memoir_profile")
    return str(profile.get("narrator_name") or "") if isinstance(profile, dict) else ""


def _scan_book(book: _Book, corrections: list[WritingCorrection]) -> TermScan:
    scan = TermScan()

    def add(scope: str, ref: str, key: str, text: str | None) -> None:
        if _contains(text, corrections):
            scan.targets.append(_Target(scope, ref, key, text or ""))

    add("project_title", book.project.id, "", book.project.title)
    add("narrator_name", book.project.id, "", _narrator_name(book.project))
    for chapter in book.chapters:
        add("chapter_title", chapter.id, "", chapter.title)
    for section in book.sections:
        add("section_title", section.id, "", section.title)
    for utterance in book.utterances:
        add("transcript", utterance.id, "", current_utterance_text(utterance))
        message = utterance.chat_message
        reading = (message.metadata_json or {}).get(narration_people.READING_KEY) if message else None
        if isinstance(reading, dict):
            for index, person in enumerate(reading.get("people") or []):
                if isinstance(person, dict):
                    for name in _PEOPLE_FIELDS:
                        add("people", message.id, f"{index}.{name}", person.get(name) if isinstance(person.get(name), str) else None)
    for chapter_id, version in book.latest_drafts.items():
        add("chapter_body", chapter_id, "content", version.content)
        for snapshot in version.section_snapshot_json or []:
            if isinstance(snapshot, dict):
                add("chapter_body", chapter_id, str(snapshot.get("section_id") or ""), str(snapshot.get("content") or ""))
    manuscript = getattr(book.output_version, "manuscript_json", None)
    if isinstance(manuscript, dict):
        for path in _copy_paths(manuscript):
            value = _get_path(manuscript, path)
            add("book_copy", _path_key(path), "", value if isinstance(value, str) else None)
    return scan


async def scan_writing(db: AsyncSession, project: Project, corrections: list[WritingCorrection]) -> TermScan:
    return _scan_book(await _load_book(db, project), corrections)


def scan_preview(scan: TermScan, corrections: list[WritingCorrection]) -> str:
    """User-facing summary: where the writing appears and a few before/after excerpts."""
    counts = scan.counts
    places = "、".join(
        f"{label} {counts[scope]} {SCOPE_UNITS[scope]}" for scope, label in SCOPE_LABELS.items() if counts.get(scope)
    )
    samples: list[str] = []
    for target in scan.targets:
        if target.scope not in {"transcript", "chapter_body"}:
            continue
        source = next((item.source for item in corrections if item.source in target.text), "")
        start = target.text.find(source)
        excerpt = target.text[max(0, start - 10):start + len(source) + 10]
        corrected, _spans = replace_text(excerpt, corrections)
        line = f"「…{excerpt}…」→「…{corrected}…」"
        if line not in samples:
            samples.append(line)
        if len(samples) >= 2:
            break
    lines = [f"将修改：{places}。" if places else ""]
    lines.extend(f"例：{sample}" for sample in samples)
    lines.append("原始录音和识别原文都保留；以后的新讲述也会按新写法记录。")
    return "\n".join(line for line in lines if line)


# --- Undo record


class TextChange(BaseModel):
    ref: str
    key: str = ""
    after_hash: str
    spans: list[tuple[int, str, str]] = Field(default_factory=list)
    version_id: str | None = None


class TitleChange(BaseModel):
    ref: str
    before: str
    after: str


class WritingUndo(BaseModel):
    corrections: list[tuple[str, str]]
    project_title: TitleChange | None = None
    narrator_name: TitleChange | None = None
    chapter_titles: list[TitleChange] = Field(default_factory=list)
    section_titles: list[TitleChange] = Field(default_factory=list)
    transcripts: list[TextChange] = Field(default_factory=list)
    people: list[TextChange] = Field(default_factory=list)
    chapter_bodies: list[TextChange] = Field(default_factory=list)
    book_copy: list[TextChange] = Field(default_factory=list)
    glossary_added: list[tuple[str, str]] = Field(default_factory=list)


# --- Apply


async def _chapter_contexts(db: AsyncSession, project_id: str, chapter_ids: list[str]) -> dict[str, chapter_generation.ChapterGenerationContext | None]:
    contexts: dict[str, chapter_generation.ChapterGenerationContext | None] = {}
    for chapter_id in chapter_ids:
        try:
            contexts[chapter_id] = await chapter_generation.load_chapter_generation_context(
                db, project_id=project_id, chapter_id=chapter_id,
            )
        except (chapter_generation.ChapterGenerationNeedsMaterial, HTTPException):
            contexts[chapter_id] = None
    return contexts


async def _rebase_chapters(
    db: AsyncSession, *, project_id: str, latest: dict[str, MemoirBookChapterDraftVersion],
    before: dict[str, chapter_generation.ChapterGenerationContext | None],
    patch_for: Callable[[str], Callable[[str, str], str]],
) -> dict[str, MemoirBookChapterDraftVersion]:
    after = await _chapter_contexts(db, project_id, list(latest))
    created: dict[str, MemoirBookChapterDraftVersion] = {}
    for chapter_id, version in latest.items():
        rebased = chapter_generation.rebased_chapter_draft_version(
            before=before.get(chapter_id), after=after.get(chapter_id), latest=version,
            patch_text=patch_for(chapter_id),
        )
        if (
            rebased.content == version.content
            and rebased.section_snapshot_json == list(version.section_snapshot_json or [])
            and rebased.material_snapshot_json == list(version.material_snapshot_json or [])
            and rebased.input_signature == version.input_signature
        ):
            continue
        db.add(rebased)
        await db.flush()
        draft = await db.get(MemoirBookChapterDraft, version.draft_id)
        if draft is not None:
            metadata = dict(draft.metadata_json or {})
            if metadata.get("confirmed_version_id") == version.id:
                # Same confirmed text with the corrected writing; confirmation carries over.
                metadata["confirmed_version_id"] = rebased.id
                draft.metadata_json = metadata
            draft.updated_at = utcnow_naive()
            db.add(draft)
        created[chapter_id] = rebased
    return created


async def _rename_chapter(db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str, title: str) -> None:
    from app.services.memoir.book_plan_write_workflow import apply_book_plan_structure_command

    await apply_book_plan_structure_command(
        db, user_id=user_id, project_id=project_id,
        req=MemoirBookPlanStructureCommandRequest(type="rename_chapter", chapter_id=chapter_id, title=title),
        commit=False,
    )


async def _rename_section(db: AsyncSession, *, user_id: str, project_id: str, section_id: str, title: str) -> None:
    from app.services.memoir.sections import update_section

    section = await db.get(MemoirSection, section_id)
    await update_section(
        db, user_id=user_id, project_id=project_id, section_id=section_id,
        req=MemoirSectionUpdateRequest(title=title, expected_version=section.version), commit=False,
    )


def _patch_people(message: MemoirChatMessage, *, expected_version: str | None, new_version: str | None,
                  patch: Callable[[str, str], str]) -> bool:
    metadata = dict(message.metadata_json or {})
    reading = metadata.get(narration_people.READING_KEY)
    if not isinstance(reading, dict) or reading.get("transcript_version_id") != (expected_version or ""):
        return False
    reading = deepcopy(reading)
    for index, person in enumerate(reading.get("people") or []):
        if isinstance(person, dict):
            for name in _PEOPLE_FIELDS:
                if isinstance(person.get(name), str):
                    person[name] = patch(f"{index}.{name}", person[name])
    if new_version is not None:
        reading["transcript_version_id"] = new_version
    if reading != metadata[narration_people.READING_KEY]:
        message.metadata_json = {**metadata, narration_people.READING_KEY: reading}
    return True


async def apply_writing_corrections(
    db: AsyncSession, *, user_id: str, project: Project, corrections: list[WritingCorrection],
    expected_signatures: dict[str, str],
) -> WritingUndo:
    """Apply confirmed corrections in the caller's transaction; the caller commits.

    `expected_signatures` maps each source phrase to `scan_signature` from the preview.
    """
    from app.services.memoir.book_output_state import invalidate_book_output
    from app.services.memoir.book_output_versions import append_book_output_version
    from app.services.memoir.completion_issues import lock_project_chapters
    from app.services.memoir.projects import apply_memoir_project_update
    from app.services.memoir.voice_transcripts import apply_corrected_writing_text

    validate_corrections(corrections)
    await lock_project_chapters(db, project_id=project.id)
    book = await _load_book(db, project)
    if any(_scan_book(book, [item]).signature([item]) != expected_signatures.get(item.source) for item in corrections):
        raise HTTPException(409, "要修改的内容已经变化，请重新查看修改结果；不要重复执行旧操作")
    for chapter in book.chapters:
        if _contains(chapter.title, corrections) and len(replace_text(chapter.title, corrections)[0]) > MAX_TITLE_CHARS:
            raise HTTPException(422, "更正后的章节标题过长，请换一个写法")
    undo = WritingUndo(corrections=[(item.source, item.target) for item in corrections])
    before = await _chapter_contexts(db, project.id, list(book.latest_drafts))
    # Loading contexts refreshes shared rows; reload the book so its relationships are usable.
    book = await _load_book(db, project)

    def replaced(text: str) -> tuple[str, list[Span]]:
        return replace_text(text, corrections)

    # Narration first: later title commands may refresh these rows again.
    for utterance in book.utterances:
        text = current_utterance_text(utterance)
        message = utterance.chat_message
        old_version = message.transcript_version_id if message else None
        new_version: str | None = None
        if _contains(text, corrections):
            corrected, spans = replaced(text)
            _old, new_version = await apply_corrected_writing_text(db, utterance, corrected, project_id=project.id)
            undo.transcripts.append(TextChange(
                ref=utterance.id, after_hash=semantic_text_hash(corrected), spans=spans, version_id=new_version,
            ))
        if message is not None:
            people_spans: dict[str, list[Span]] = {}

            def patch_person(key: str, value: str) -> str:
                corrected_value, spans = replaced(value)
                if spans:
                    people_spans[key] = spans
                return corrected_value

            if _patch_people(message, expected_version=old_version, new_version=new_version, patch=patch_person) and (people_spans or new_version):
                undo.people.extend(
                    TextChange(ref=message.id, key=key, after_hash="", spans=spans, version_id=new_version)
                    for key, spans in people_spans.items()
                )
                if new_version and not people_spans:
                    undo.people.append(TextChange(ref=message.id, key="", after_hash="", version_id=new_version))
    profile_update: dict[str, str] = {}
    if _contains(project.title, corrections):
        title = replaced(project.title)[0]
        undo.project_title = TitleChange(ref=project.id, before=project.title, after=title)
        profile_update["title"] = title
    name = _narrator_name(project)
    if _contains(name, corrections):
        corrected_name = replaced(name)[0]
        undo.narrator_name = TitleChange(ref=project.id, before=name, after=corrected_name)
        profile_update["elder_name"] = corrected_name
    if profile_update:
        await apply_memoir_project_update(db, project, MemoirProjectUpdate(**profile_update))
    for chapter in book.chapters:
        if _contains(chapter.title, corrections):
            title = replaced(chapter.title)[0]
            undo.chapter_titles.append(TitleChange(ref=chapter.id, before=chapter.title, after=title))
            await _rename_chapter(db, user_id=user_id, project_id=project.id, chapter_id=chapter.id, title=title)
    for section in book.sections:
        if _contains(section.title, corrections):
            title = replaced(section.title)[0]
            undo.section_titles.append(TitleChange(ref=section.id, before=section.title, after=title))
            await _rename_section(db, user_id=user_id, project_id=project.id, section_id=section.id, title=title)
    await db.flush()

    body_spans: dict[str, dict[str, list[Span]]] = {}

    def patch_for(chapter_id: str) -> Callable[[str, str], str]:
        def patch(key: str, text: str) -> str:
            corrected, spans = replaced(text)
            if spans:
                body_spans.setdefault(chapter_id, {})[key] = spans
            return corrected
        return patch

    created = await _rebase_chapters(db, project_id=project.id, latest=book.latest_drafts, before=before, patch_for=patch_for)
    for chapter_id, version in created.items():
        for key, spans in body_spans.get(chapter_id, {"content": []}).items():
            text = version.content if key == "content" else next(
                (str(item.get("content") or "") for item in version.section_snapshot_json
                 if isinstance(item, dict) and item.get("section_id") == key), "",
            )
            undo.chapter_bodies.append(TextChange(
                ref=chapter_id, key=key, after_hash=semantic_text_hash(text), spans=spans, version_id=version.id,
            ))
    output = book.output
    if output is not None:
        from app.services.memoir.book_output_versions import load_latest_book_output_version

        latest_output = await load_latest_book_output_version(db, output_id=output.id)
        manuscript = deepcopy(latest_output.manuscript_json) if latest_output else None
        if isinstance(manuscript, dict):
            for path in list(_copy_paths(manuscript)):
                value = _get_path(manuscript, path)
                if isinstance(value, str) and _contains(value, corrections):
                    corrected, spans = replaced(value)
                    _set_path(manuscript, path, corrected)
                    undo.book_copy.append(TextChange(
                        ref=".".join(map(str, path)), key=_path_key(path),
                        after_hash=semantic_text_hash(corrected), spans=spans,
                    ))
            if manuscript != latest_output.manuscript_json:
                await append_book_output_version(
                    db, output=output, content=latest_output.content,
                    chapter_snapshot_json=list(latest_output.chapter_snapshot_json or []),
                    manuscript_json=manuscript, created_by=MemoirCreatedBy.SYSTEM,
                )
                invalidate_book_output(output)
                db.add(output)
    for item in corrections:
        if dialect_glossary.add_glossary_entry(project, source=item.source, target=item.target, note="语音编辑确认的写法"):
            undo.glossary_added.append((item.source, item.target))
    await db.flush()
    return undo


def _path_key(path: tuple) -> str:
    return "\x1f".join(f"{'i' if isinstance(key, int) else 's'}{key}" for key in path)


def _parse_path_key(key: str) -> tuple:
    return tuple(int(part[1:]) if part[0] == "i" else part[1:] for part in key.split("\x1f"))


# --- Undo


async def revert_writing_corrections(db: AsyncSession, *, user_id: str, project: Project, undo: WritingUndo) -> None:
    """Restore the exact pre-correction text; any newer change to a corrected object refuses the whole undo."""
    from app.services.memoir.book_output_state import invalidate_book_output
    from app.services.memoir.book_output_versions import append_book_output_version, load_latest_book_output_version
    from app.services.memoir.completion_issues import lock_project_chapters
    from app.services.memoir.projects import apply_memoir_project_update
    from app.services.memoir.voice_transcripts import apply_corrected_writing_text

    await lock_project_chapters(db, project_id=project.id)
    body_versions = {change.ref: change.version_id for change in undo.chapter_bodies}
    # Contexts first: loading them refreshes rows the book below relies on.
    before = await _chapter_contexts(db, project.id, list(body_versions))
    book = await _load_book(db, project)
    chapters = {chapter.id: chapter for chapter in book.chapters}
    sections = {section.id: section for section in book.sections}
    utterances = {utterance.id: utterance for utterance in book.utterances}

    def conflict() -> HTTPException:
        return HTTPException(409, "这次修改之后内容又有变化，不能撤销；请直接再说一次要怎么改")

    if undo.project_title and project.title != undo.project_title.after:
        raise conflict()
    if undo.narrator_name and _narrator_name(project) != undo.narrator_name.after:
        raise conflict()
    for change in undo.chapter_titles:
        if getattr(chapters.get(change.ref), "title", None) != change.after:
            raise conflict()
    for change in undo.section_titles:
        if getattr(sections.get(change.ref), "title", None) != change.after:
            raise conflict()
    for change in undo.transcripts:
        utterance = utterances.get(change.ref)
        if (
            utterance is None
            or semantic_text_hash(current_utterance_text(utterance)) != change.after_hash
            or (utterance.chat_message and utterance.chat_message.transcript_version_id != change.version_id)
        ):
            raise conflict()
    for chapter_id, version_id in body_versions.items():
        if getattr(book.latest_drafts.get(chapter_id), "id", None) != version_id:
            raise conflict()

    restored_versions: dict[str, tuple[str | None, str]] = {}
    for change in undo.transcripts:
        utterance = utterances[change.ref]
        old_version = utterance.chat_message.transcript_version_id if utterance.chat_message else None
        try:
            original = revert_text(current_utterance_text(utterance), list(change.spans))
        except WritingConflict as exc:
            raise conflict() from exc
        _previous, new_version = await apply_corrected_writing_text(db, utterance, original, project_id=project.id)
        restored_versions[utterance.chat_message.id if utterance.chat_message else utterance.id] = (old_version, new_version)
    profile_update: dict[str, str] = {}
    if undo.project_title:
        profile_update["title"] = undo.project_title.before
    if undo.narrator_name:
        profile_update["elder_name"] = undo.narrator_name.before
    if profile_update:
        await apply_memoir_project_update(db, project, MemoirProjectUpdate(**profile_update))
    for change in undo.chapter_titles:
        await _rename_chapter(db, user_id=user_id, project_id=project.id, chapter_id=change.ref, title=change.before)
    for change in undo.section_titles:
        await _rename_section(db, user_id=user_id, project_id=project.id, section_id=change.ref, title=change.before)
    people_by_message: dict[str, dict[str, list[Span]]] = {}
    for change in undo.people:
        people_by_message.setdefault(change.ref, {})
        if change.key:
            people_by_message[change.ref][change.key] = list(change.spans)
    for message_id, spans_by_key in people_by_message.items():
        message = await db.get(MemoirChatMessage, message_id)
        if message is None:
            continue
        old_version, new_version = restored_versions.get(message_id, (message.transcript_version_id, None))

        def patch_person(key: str, value: str, spans_by_key: dict[str, list[Span]] = spans_by_key) -> str:
            try:
                return revert_text(value, spans_by_key[key]) if key in spans_by_key else value
            except WritingConflict:
                return value  # A later reading already changed this person; keep it.

        _patch_people(message, expected_version=old_version, new_version=new_version, patch=patch_person)
    await db.flush()

    spans_by_chapter: dict[str, dict[str, list[Span]]] = {}
    for change in undo.chapter_bodies:
        spans_by_chapter.setdefault(change.ref, {})[change.key] = list(change.spans)

    def patch_for(chapter_id: str) -> Callable[[str, str], str]:
        def patch(key: str, text: str) -> str:
            spans = spans_by_chapter.get(chapter_id, {}).get(key)
            if not spans:
                return text
            try:
                return revert_text(text, spans)
            except WritingConflict as exc:
                raise conflict() from exc
        return patch

    await _rebase_chapters(
        db, project_id=project.id,
        latest={chapter_id: book.latest_drafts[chapter_id] for chapter_id in body_versions},
        before=before, patch_for=patch_for,
    )
    if book.output is not None and undo.book_copy:
        latest_output = await load_latest_book_output_version(db, output_id=book.output.id)
        manuscript = deepcopy(latest_output.manuscript_json) if latest_output else None
        if isinstance(manuscript, dict):
            for change in undo.book_copy:
                path = _parse_path_key(change.key)
                value = _get_path(manuscript, path)
                # Automatic copy may already have been rewritten from the corrected
                # body; it will follow the restored body through the normal flow.
                if isinstance(value, str) and semantic_text_hash(value) == change.after_hash:
                    try:
                        _set_path(manuscript, path, revert_text(value, list(change.spans)))
                    except WritingConflict:
                        continue
            if manuscript != latest_output.manuscript_json:
                await append_book_output_version(
                    db, output=book.output, content=latest_output.content,
                    chapter_snapshot_json=list(latest_output.chapter_snapshot_json or []),
                    manuscript_json=manuscript, created_by=MemoirCreatedBy.SYSTEM,
                )
                invalidate_book_output(book.output)
                db.add(book.output)
    for source, target in undo.glossary_added:
        dialect_glossary.remove_glossary_entry(project, source=source, target=target)
    await db.flush()
