"""Store book issue chapter references independently from review cache.

Revision ID: a64e8d31f902
Revises: e3d9a7c1b502
"""

from hashlib import sha256
import json

from alembic import op
import sqlalchemy as sa


revision = "a64e8d31f902"
down_revision = "e3d9a7c1b502"
branch_labels = None
depends_on = None


def _legacy_candidate_refs(candidates: list) -> list[tuple[str, list[str]]]:
    """Reconstruct fingerprints persisted before book issues owned their refs."""
    seen: set[tuple[str, str]] = set()
    occurrences: dict[str, int] = {}
    refs: list[tuple[str, list[str]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        question = str(candidate.get("question") or "").strip()
        reason = str(candidate.get("reason") or "").strip()
        if not question or not reason:
            continue
        issue_type = str(candidate.get("issue_type") or "").strip()
        raw_material_ids = candidate.get("material_ids", [])
        raw_chapter_ids = candidate.get("chapter_ids", [])
        if not isinstance(raw_material_ids, list) or not isinstance(raw_chapter_ids, list):
            continue
        material_ids = [str(value).strip() for value in raw_material_ids if str(value).strip()]
        chapter_ids = [str(value).strip() for value in raw_chapter_ids if str(value).strip()]
        payload = {
            "scope": "book",
            "chapter_id": None,
            "issue_type": issue_type,
            "material_ids": sorted(set(material_ids)),
            "related_scope_ids": sorted(set(chapter_ids)),
        }
        base = sha256(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        key = (base, " ".join(question.split()))
        if key in seen:
            continue
        seen.add(key)
        occurrence = occurrences.get(base, 0)
        occurrences[base] = occurrence + 1
        fingerprint = sha256(f"{base}:{occurrence}".encode()).hexdigest() if occurrence else base
        refs.append((fingerprint, chapter_ids))
    return refs


def upgrade() -> None:
    op.add_column(
        "memoir_completion_issues",
        sa.Column("related_chapter_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    connection = op.get_bind()
    reviews = connection.execute(
        sa.text(
            "SELECT review.project_id, review.input_signature, review.review_json "
            "FROM memoir_book_reviews AS review "
            "WHERE EXISTS ("
            "SELECT 1 FROM memoir_completion_issues AS issue "
            "WHERE issue.project_id = review.project_id "
            "AND issue.input_signature = review.input_signature "
            "AND issue.scope = 'book'"
            ")"
        )
    ).mappings()
    for review in reviews:
        payload = review["review_json"]
        candidates = payload.get("candidate_issues", []) if isinstance(payload, dict) else []
        if not isinstance(candidates, list):
            continue
        for fingerprint, chapter_ids in _legacy_candidate_refs(candidates):
            connection.execute(
                sa.text(
                    "UPDATE memoir_completion_issues "
                    "SET related_chapter_ids_json = CAST(:chapter_ids AS JSON) "
                    "WHERE project_id = :project_id AND input_signature = :input_signature "
                    "AND scope = 'book' AND fingerprint = :fingerprint"
                ),
                {
                    "chapter_ids": json.dumps(chapter_ids),
                    "project_id": review["project_id"],
                    "input_signature": review["input_signature"],
                    "fingerprint": fingerprint,
                },
            )
    op.alter_column("memoir_completion_issues", "related_chapter_ids_json", server_default=None)


def downgrade() -> None:
    op.drop_column("memoir_completion_issues", "related_chapter_ids_json")
