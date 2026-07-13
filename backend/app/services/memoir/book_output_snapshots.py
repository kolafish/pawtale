from __future__ import annotations

from app.models.memoir import MemoirBookChapterDraftVersion, MemoirBookPlanChapter


def _safe_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def chapter_snapshot(
    chapter: MemoirBookPlanChapter,
    version: MemoirBookChapterDraftVersion,
    *,
    chapter_index: int,
    source_refs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    content = version.content.strip()
    refs = source_refs or []
    source_ref_snapshot = [
        {
            "utterance_id": _safe_str(item.get("utterance_id")),
            "transcript_version_id": _safe_str(item.get("transcript_version_id")) or None,
            "voice_session_id": _safe_str(item.get("voice_session_id")) or None,
            "quote": _safe_str(item.get("quote")),
            "summary": _safe_str(item.get("summary"))
            or _safe_str(item.get("source_summary")),
            "representative_quote": _safe_str(item.get("representative_quote"))
            or _safe_str(item.get("quote")),
        }
        for item in refs
        if isinstance(item, dict) and _safe_str(item.get("utterance_id"))
    ]
    chapter_metadata = chapter.metadata_json if isinstance(chapter.metadata_json, dict) else {}
    return {
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "chapter_index": chapter_index,
        "chapter_sort_order": chapter.sort_order,
        "chapter_summary": (
            _safe_str(chapter_metadata.get("chapter_summary"))
            or _safe_str(chapter_metadata.get("summary"))
            or _safe_str(chapter_metadata.get("editorial_summary"))
        ),
        "metadata_json": chapter_metadata,
        "draft_id": chapter.draft.id if chapter.draft else "",
        "draft_version_id": version.id,
        "draft_version": version.version,
        "block_snapshot": [
            item for item in (version.block_snapshot_json or []) if isinstance(item, dict)
        ],
        "content_char_count": len(content),
        "source_count": len(source_ref_snapshot),
        "source_refs": source_ref_snapshot,
        "content": content,
    }
