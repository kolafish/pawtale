from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirCreatedBy, MemoirTranscriptPolishLevel
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirBookOutput, MemoirBookOutputVersion
from app.schemas.memoir_book import MemoirBookLevelEditorialCopyTarget, MemoirBookManuscriptQuote
from app.services.memoir.book_manuscript_cleanup import original_quote_candidates, body_excerpt_candidates
from app.services.memoir.book_output_manuscript import stored_manuscript_metadata, fallback_chapter_quotes
from app.services.memoir.book_output_snapshots import book_content_from_chapter_snapshots, normalized_section_snapshots
from app.services.memoir.semantic_state import semantic_json_signature


async def load_latest_book_output_version(
    db: AsyncSession,
    *,
    output_id: str,
) -> MemoirBookOutputVersion | None:
    return await db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.output_id == output_id)
        .order_by(
            MemoirBookOutputVersion.version.desc(),
            MemoirBookOutputVersion.created_at.desc(),
            MemoirBookOutputVersion.id.desc(),
        )
        .limit(1)
    )


async def append_book_output_version(
    db: AsyncSession,
    *,
    output: MemoirBookOutput,
    content: str,
    chapter_snapshot_json: list[dict[str, object]],
    manuscript_json: dict[str, object],
    created_by: MemoirCreatedBy,
) -> MemoirBookOutputVersion:
    await db.execute(select(MemoirBookOutput.id).where(MemoirBookOutput.id == output.id).with_for_update())
    latest = await load_latest_book_output_version(db, output_id=output.id)
    version = MemoirBookOutputVersion(
        output_id=output.id,
        project_id=output.project_id,
        plan_id=output.plan_id,
        version=(latest.version if latest else 0) + 1,
        content=content,
        chapter_snapshot_json=deepcopy(chapter_snapshot_json),
        manuscript_json=deepcopy(manuscript_json),
        created_by=created_by.value,
    )
    db.add(version)
    await db.flush()
    return version


def _same_book_body(left: MemoirBookOutputVersion, right: MemoirBookOutputVersion) -> bool:
    if left.chapter_snapshot_json != right.chapter_snapshot_json:
        return False
    if left.content != right.content and not all(
        version.content == book_content_from_chapter_snapshots(
            title=book_output_version_title(version, fallback=""),
            snapshots=version.chapter_snapshot_json,
        )
        for version in (left, right)
    ):
        return False
    # Intro/quotes have field ownership; they do not make the assembled body
    # a manual rewrite. Continue tracing through them to protect real body edits.
    def chapters(version: MemoirBookOutputVersion) -> list[dict]:
        return [
            {key: value for key, value in chapter.items() if key not in {"intro", "pull_quotes", "illustrations"}}
            for chapter in version.manuscript_json.get("chapters", [])
        ]
    return chapters(left) == chapters(right)


def _without_automatic_copy(version: MemoirBookOutputVersion) -> dict:
    manuscript = deepcopy(version.manuscript_json)
    for section in manuscript.get("front_matter", []):
        if section.get("kind") == "preface":
            section["content"] = ""
    afterword = manuscript.get("back_matter", {}).get("afterword")
    if afterword is not None:
        afterword["content"] = ""
    for field in ("back_cover_quote", "editorial_copy_model"):
        manuscript.get("render_settings", {}).pop(field, None)
    for chapter in manuscript.get("chapters", []):
        chapter.pop("intro", None)
        chapter.pop("pull_quotes", None)
    return manuscript


async def revision_compatible_version(
    db: AsyncSession, baseline: MemoirBookOutputVersion,
) -> MemoirBookOutputVersion | None:
    """Accept only system copy updates; any intervening authored edit is a conflict."""
    versions = (await db.scalars(select(MemoirBookOutputVersion).where(
        MemoirBookOutputVersion.output_id == baseline.output_id,
        MemoirBookOutputVersion.version > baseline.version,
    ).order_by(MemoirBookOutputVersion.version))).all()
    expected = _without_automatic_copy(baseline)
    if any(version.created_by != MemoirCreatedBy.SYSTEM.value
           or version.content != baseline.content
           or not _same_book_body(baseline, version)
           or _without_automatic_copy(version) != expected for version in versions):
        return None
    return versions[-1] if versions else baseline


async def book_body_is_protected(
    db: AsyncSession, *, output: MemoirBookOutput, version: MemoirBookOutputVersion,
) -> bool:
    from app.services.memoir import book_editorial_actions, book_review_answer_state

    applied_ids = {
        book_review_answer_state.application_marker(output).get("output_version_id"),
        book_editorial_actions.application_marker(output).get("output_version_id"),
    }
    # Trace unchanged body content through title/cover/image versions. This also
    # classifies existing design-only user versions without changing their audit provenance.
    current = version
    while True:
        if current.id in applied_ids:
            return True
        previous = await db.scalar(
            select(MemoirBookOutputVersion)
            .where(MemoirBookOutputVersion.output_id == output.id, MemoirBookOutputVersion.version < current.version)
            .order_by(MemoirBookOutputVersion.version.desc()).limit(1)
        )
        if previous is None or not _same_book_body(current, previous):
            return current.created_by == MemoirCreatedBy.USER.value
        current = previous


async def sync_saved_sections_to_protected_book(
    db: AsyncSession, *, version: MemoirBookChapterDraftVersion, edited_section_ids: set[str],
) -> MemoirBookOutputVersion | None:
    """Apply an explicit section save without reassembling unrelated protected prose."""
    if not edited_section_ids:
        return None
    output = await db.scalar(select(MemoirBookOutput).where(
        MemoirBookOutput.project_id == version.project_id,
    ).with_for_update().execution_options(populate_existing=True))
    if output is None:
        return None
    latest = await load_latest_book_output_version(db, output_id=output.id)
    if latest is None or not await book_body_is_protected(db, output=output, version=latest):
        return None
    snapshots = deepcopy(latest.chapter_snapshot_json)
    snapshot = next((item for item in snapshots if item.get("chapter_id") == version.chapter_id), None)
    manuscript = deepcopy(latest.manuscript_json)
    chapter = next((item for item in manuscript.get("chapters", []) if item.get("chapter_id") == version.chapter_id), None)
    if snapshot is None or chapter is None:
        return None  # Never reintroduce an omitted chapter through a text save.
    baseline = await db.get(MemoirBookChapterDraftVersion, snapshot.get("draft_version_id")) if snapshot.get("draft_version_id") else None
    if baseline is None or baseline.material_snapshot_json != version.material_snapshot_json:
        return None
    title = book_output_version_title(latest, fallback=output.title)
    if latest.content != book_content_from_chapter_snapshots(title=title, snapshots=snapshots):
        return None  # A legacy free-form book has no unambiguous section mapping.
    saved = {item["section_id"]: item for item in snapshot.get("sections", [])}
    rendered = {item["section_id"]: item for item in chapter.get("sections", [])}
    revised = {item["section_id"]: item for item in normalized_section_snapshots(version.section_snapshot_json)}
    if not edited_section_ids <= saved.keys() & rendered.keys() & revised.keys():
        return None
    if saved.keys() != revised.keys() or any(
        saved[key].get("used_material_ids") != revised[key].get("used_material_ids") for key in saved
    ):
        return None  # Source/structure changes continue through the existing assembly rules.
    changed = {key for key in edited_section_ids if saved[key]["content"] != revised[key]["content"]}
    if not changed:
        return None
    for key in changed:
        text = revised[key]["content"]
        saved[key].update(content=text, content_char_count=len(text))
        for field in ("body_digest", "body_digest_hash"):
            saved[key].pop(field, None)
        rendered[key].update(content=text, char_count=len(text))
    snapshot["content"] = "\n\n".join(item["content"] for item in snapshot["sections"])
    snapshot["content_char_count"] = len(snapshot["content"])
    snapshot.update(draft_id=version.draft_id, draft_version_id=version.id, draft_version=version.version)
    chapter["content"] = snapshot["content"]
    for item in manuscript.get("table_of_contents", []):
        if item.get("chapter_id") == version.chapter_id:
            item["char_count"] = snapshot["content_char_count"]
            for section in item.get("sections", []):
                if section["section_id"] in changed:
                    section["char_count"] = saved[section["section_id"]]["content_char_count"]
    from app.clock import utcnow_naive
    from app.services.memoir.book_output_state import invalidate_book_output

    invalidate_book_output(output)
    output.updated_at = utcnow_naive()
    return await append_book_output_version(
        db, output=output, content=book_content_from_chapter_snapshots(title=title, snapshots=snapshots),
        chapter_snapshot_json=snapshots, manuscript_json=manuscript, created_by=MemoirCreatedBy.USER,
    )


def book_output_version_title(
    version: MemoirBookOutputVersion,
    *,
    fallback: str,
) -> str:
    manuscript = (
        version.manuscript_json
        if isinstance(version.manuscript_json, dict)
        else {}
    )
    profile = manuscript.get("profile")
    if isinstance(profile, dict):
        title = profile.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    for line in version.content.splitlines():
        title = line.strip().lstrip("#").strip()
        if title:
            return title
    return fallback


def manuscript_chapters(version: MemoirBookOutputVersion) -> dict[str, dict[str, object]]:
    manuscript = version.manuscript_json if isinstance(version.manuscript_json, dict) else {}
    chapters = manuscript.get("chapters")
    return {
        chapter["chapter_id"]: chapter
        for chapter in (chapters if isinstance(chapters, list) else [])
        if isinstance(chapter, dict) and isinstance(chapter.get("chapter_id"), str)
    }


class AutomaticEditorialCopyState(BaseModel):
    ownership_version: int = 0
    signature_version: int = 0
    input_signature: str = ""
    processed_version: int = 0
    status: Literal["succeeded", "failed", "not_started"] = "not_started"
    manual_targets: list[MemoirBookLevelEditorialCopyTarget] = Field(default_factory=list)
    manual_chapter_fields: dict[str, list[Literal["intro", "pull_quotes"]]] = Field(default_factory=dict)
    chapter_input_signatures: dict[str, str] = Field(default_factory=dict)
    failed_chapter_ids: list[str] = Field(default_factory=list)


AUTOMATIC_EDITORIAL_COPY_KEY = "automatic_editorial_copy"
EDITORIAL_COPY_POLICY_VERSION = 3


def chapter_editorial_copy_signature(snapshot: dict[str, object]) -> str:
    # Copy describes the current body; audit metadata and stale source summaries
    # are not evidence. Original quotations must also occur in their own section.
    candidates = original_quote_candidates(snapshot, max_chars=90, for_signature=True)
    return semantic_json_signature({
        "editorial_evidence_version": EDITORIAL_COPY_POLICY_VERSION,
        **{key: snapshot.get(key) for key in ("chapter_title", "content")},
        "original_quotes": [
            (source.get("utterance_id"), max(candidates.get(source.get("utterance_id"), ()), key=len, default=""))
            for source in snapshot.get("source_refs", []) if isinstance(source, dict)
        ],
    })


def book_editorial_copy_signature(title: str, snapshot: list[dict[str, object]]) -> str:
    # Book copy also depends on chapter identity and order, but not audit fields.
    return semantic_json_signature({"title": title, "chapters": [
        (item["chapter_id"], chapter_editorial_copy_signature(item)) for item in snapshot
    ]})


def refresh_book_level_copy(previous: list[dict], current: list[dict], *, preserved: set[str]) -> bool:
    """Keep front matter on reorder and additive narration within existing chapters.
    Removed or corrected evidence can invalidate factual claims in the front matter.
    """
    before = {chapter["chapter_id"]: chapter for chapter in previous}
    if set(before) != {chapter["chapter_id"] for chapter in current}:
        return True
    for chapter in current:
        old = before[chapter["chapter_id"]]
        if chapter["chapter_id"] in preserved or (old["content"] == chapter["content"] and old.get("source_refs") == chapter.get("source_refs")):
            continue
        old_sources = {(ref["utterance_id"], ref.get("quote")) for ref in old.get("source_refs", [])}
        new_sources = {(ref["utterance_id"], ref.get("quote")) for ref in chapter.get("source_refs", [])}
        if not old_sources < new_sources:
            return True
    return False


def _corrected_excerpt(text: str, stored: dict, snapshot: dict, candidates: tuple[str, ...]) -> str | None:
    # A unique one-character correction in the same section can keep its place
    # as the opening excerpt. Never guess between repeated or ambiguous passages.
    sections = [s for s in stored.get("sections", []) if text in s.get("content", "")]
    if len(sections) != 1 or sections[0]["content"].count(text) != 1:
        return None
    contents = [s["content"] for s in snapshot.get("sections", []) if s["section_id"] == sections[0]["section_id"]]
    if len(contents) != 1:
        return None
    matches = [line for line in candidates if len(line) == len(text) and contents[0].count(line) == 1
        and sum(a != b for a, b in zip(line, text)) == 1]
    return matches[0] if len(matches) == 1 else None


def retained_chapter_quotes(
    stored: dict[str, object], snapshot: dict[str, object], *, preserve_manual: bool = False,
    polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.HIGH,
) -> list[MemoirBookManuscriptQuote]:
    def refs(value):
        return {item["utterance_id"]: item for item in value.get("source_refs", []) if isinstance(item, dict)}
    old_refs, new_refs = refs(stored), refs(snapshot)
    quotes = [MemoirBookManuscriptQuote.model_validate(value) for value in stored.get("pull_quotes", [])]
    excerpts = body_excerpt_candidates(str(snapshot.get("content") or ""), max_chars=90)
    originals = original_quote_candidates(snapshot, max_chars=90) if polish_level == MemoirTranscriptPolishLevel.LOW else {}
    retained = []
    for quote in quotes:
        if (not preserve_manual and quote.source in {"editorial_generated", "original_quote"}
                and quote.text not in str(snapshot.get("content") or "")):
            corrected = _corrected_excerpt(quote.text, stored, snapshot, excerpts)
            if corrected:
                # A correction in prose is an excerpt of that prose, even if
                # the previous wording also matched the original recording.
                retained.append(MemoirBookManuscriptQuote(text=corrected, source="editorial_generated"))
                continue
        if not all(
            source_id in new_refs and (
                (new_refs[source_id].get("transcript_version_id"), new_refs[source_id].get("quote"))
                == (old_refs.get(source_id, {}).get("transcript_version_id"), old_refs.get(source_id, {}).get("quote"))
                or quote.text in str(new_refs[source_id].get("quote") or "")
            ) for source_id in quote.source_ref_ids
        ):
            continue
        if quote.source == "original_quote" and (not quote.source_ref_ids or not all(
            quote.text in str(new_refs[source_id].get("quote") or "")
            for source_id in quote.source_ref_ids
        )):
            continue
        if quote.source == "original_quote" and not preserve_manual and not all(
            any(quote.text in run for run in originals.get(source_id, ()))
            for source_id in quote.source_ref_ids
        ):
            continue
        if quote.source == "editorial_generated" and (
            quote.text not in str(snapshot.get("content") or "")
            or (not preserve_manual and quote.text not in excerpts)
        ):
            continue
        retained.append(quote)
    return retained


def automatic_editorial_copy_state(
    output: MemoirBookOutput, versions: list[MemoirBookOutputVersion],
) -> AutomaticEditorialCopyState:
    state = AutomaticEditorialCopyState.model_validate((output.metadata_json or {}).get(AUTOMATIC_EDITORIAL_COPY_KEY, {}))
    if state.ownership_version >= 1:
        return state
    if not state.processed_version and versions and versions[0].created_by in {"user", "ai"}:
        state.manual_targets = ["subtitle", "preface", "afterword", "back_cover_quote"]
        state.manual_chapter_fields = {
            chapter_id: ["intro", "pull_quotes"] for chapter_id in manuscript_chapters(versions[0])
        }
    # Before the first automatic pass, every AI copy change was explicitly
    # requested. Thereafter inspect only versions after our last saved pass.
    for before, after in zip(versions, versions[1:]):
        if after.version <= state.processed_version or after.created_by not in {"user", "ai"}:
            continue
        old = stored_manuscript_metadata(before.manuscript_json)
        new = stored_manuscript_metadata(after.manuscript_json)
        for target in ("subtitle", "preface", "afterword", "back_cover_quote"):
            if old.get(target) != new.get(target) and target not in state.manual_targets:
                state.manual_targets.append(target)
        old_chapters, new_chapters = manuscript_chapters(before), manuscript_chapters(after)
        for chapter_id, chapter in new_chapters.items():
            for field in ("intro", "pull_quotes"):
                if old_chapters.get(chapter_id, {}).get(field) != chapter.get(field):
                    fields = state.manual_chapter_fields.setdefault(chapter_id, [])
                    if field not in fields:
                        fields.append(field)
    state.ownership_version = 1
    output.metadata_json = {**(output.metadata_json or {}), AUTOMATIC_EDITORIAL_COPY_KEY: state.model_dump(mode="json")}
    return state


def claim_editorial_copy_fields(
    output: MemoirBookOutput, *, targets: list[MemoirBookLevelEditorialCopyTarget],
    chapter_fields: dict[str, list[Literal["intro", "pull_quotes"]]],
) -> None:
    """Record explicit intent, including saving unchanged or empty text."""
    state = automatic_editorial_copy_state(output, output.versions)
    state.manual_targets = sorted(set(state.manual_targets) | set(targets))
    for chapter_id, fields in chapter_fields.items():
        if not fields:
            continue
        state.manual_chapter_fields[chapter_id] = sorted(set(state.manual_chapter_fields.get(chapter_id, [])) | set(fields))
    output.metadata_json = {**(output.metadata_json or {}), AUTOMATIC_EDITORIAL_COPY_KEY: state.model_dump(mode="json")}


async def repair_automatic_chapter_quotes(
    db: AsyncSession, *, output: MemoirBookOutput, version: MemoirBookOutputVersion,
    state: AutomaticEditorialCopyState,
    polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.HIGH,
) -> MemoirBookOutputVersion:
    """Upgrade old automatic excerpts deterministically; keep authored choices and AI checkpoints."""
    from app.services.memoir.book_output_state import invalidate_book_output
    manuscript = deepcopy(version.manuscript_json)
    snapshots = {item["chapter_id"]: item for item in version.chapter_snapshot_json}
    for chapter in manuscript.get("chapters", []):
        chapter_id = chapter.get("chapter_id")
        if ("pull_quotes" in state.manual_chapter_fields.get(chapter_id, [])
                or chapter_id not in snapshots or chapter.get("pull_quotes") == []):
            continue
        snapshot = snapshots[chapter_id]
        quotes = retained_chapter_quotes(chapter, snapshot, polish_level=polish_level)
        if not quotes:
            quotes = fallback_chapter_quotes(str(snapshot.get("content") or ""), snapshot, polish_level=polish_level)
        chapter["pull_quotes"] = [quote.model_dump(mode="json") for quote in quotes]
    if manuscript == version.manuscript_json:
        return version
    updated = await append_book_output_version(db, output=output, content=version.content,
        chapter_snapshot_json=version.chapter_snapshot_json, manuscript_json=manuscript,
        created_by=MemoirCreatedBy.SYSTEM)
    invalidate_book_output(output)
    return updated
