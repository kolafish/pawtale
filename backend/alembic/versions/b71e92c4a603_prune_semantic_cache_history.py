"""Keep current semantic caches and put book answer metadata on the issue.

Revision ID: b71e92c4a603
Revises: a64e8d31f902
"""

from alembic import op
import sqlalchemy as sa


revision = "b71e92c4a603"
down_revision = "a64e8d31f902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memoir_completion_issues",
        sa.Column("resolution", sa.String(length=32), nullable=False, server_default=""),
    )
    connection = op.get_bind()
    reviews = connection.execute(sa.text(
        "SELECT project_id, input_signature, review_json FROM memoir_book_reviews"
    )).mappings()
    for review in reviews:
        payload = review["review_json"]
        candidates = payload.get("candidate_issues", []) if isinstance(payload, dict) else []
        if not isinstance(candidates, list):
            continue
        issues = connection.execute(sa.text(
            "SELECT id, issue_type, question, related_chapter_ids_json "
            "FROM memoir_completion_issues WHERE project_id = :project_id "
            "AND input_signature = :signature AND scope = 'book'"
        ), {"project_id": review["project_id"], "signature": review["input_signature"]}).mappings().all()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            resolution = candidate.get("resolution")
            if resolution not in {"new_material", "editorial_change", "user_decision"}:
                continue
            question = " ".join(str(candidate.get("question") or "").split())
            issue_type = str(candidate.get("issue_type") or "")
            chapter_ids = candidate.get("chapter_ids")
            if not question or not isinstance(chapter_ids, list):
                continue
            matches = [
                issue for issue in issues
                if issue["issue_type"] == issue_type
                and " ".join(issue["question"].split()) == question
                and sorted(issue["related_chapter_ids_json"] or []) == sorted(chapter_ids)
            ]
            if len(matches) == 1:
                connection.execute(sa.text(
                    "UPDATE memoir_completion_issues SET resolution = :resolution WHERE id = :id"
                ), {"resolution": resolution, "id": matches[0]["id"]})
    op.alter_column("memoir_completion_issues", "resolution", server_default=None)

    for table, partition in (
        ("memoir_section_digests", "project_id, section_id, engine"),
        ("memoir_chapter_digests", "project_id, chapter_id, engine"),
    ):
        op.execute(sa.text(f"""
            WITH ranked AS (
                SELECT id, status,
                    row_number() OVER (
                        PARTITION BY {partition}
                        ORDER BY (status = 'ready') DESC, updated_at DESC, id DESC
                    ) AS position
                FROM {table}
            )
            DELETE FROM {table} AS cache USING ranked
            WHERE cache.id = ranked.id AND (ranked.status <> 'ready' OR ranked.position > 1)
        """))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id, status,
                row_number() OVER (
                    PARTITION BY project_id, engine
                    ORDER BY (status = 'ready') DESC, updated_at DESC, id DESC
                ) AS position
            FROM memoir_book_reviews
        )
        DELETE FROM memoir_book_reviews AS cache USING ranked
        WHERE cache.id = ranked.id
          AND (ranked.status <> 'ready' OR ranked.position > 1)
          AND NOT EXISTS (
              SELECT 1 FROM memoir_book_revision_proposals AS proposal
              WHERE proposal.review_id = cache.id
          )
    """))


def downgrade() -> None:
    op.drop_column("memoir_completion_issues", "resolution")
