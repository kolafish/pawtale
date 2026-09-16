"""Replace the memoir project-row revision mutex with append-only events."""

from alembic import op
import sqlalchemy as sa


revision = "b7e2c4d6f809"
down_revision = "f2c8a419d607"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memoir_project_revision_events",
        sa.Column("revision", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("revision"),
        sa.UniqueConstraint(
            "project_id",
            "transaction_id",
            name="uq_memoir_project_revision_events_transaction",
        ),
    )
    op.create_index(
        "ix_memoir_project_revision_events_project_id",
        "memoir_project_revision_events",
        ["project_id"],
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('memoir_project_revision_events', 'revision'),
            GREATEST(COALESCE((SELECT MAX(memoir_data_revision) FROM projects), 0), 1),
            true
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pawtale_bump_memoir_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            old_project_id varchar;
            new_project_id varchar;
        BEGIN
            IF TG_OP != 'INSERT' THEN
                old_project_id := pawtale_memoir_project_id(TG_ARGV[0], to_jsonb(OLD));
            END IF;
            IF TG_OP != 'DELETE' THEN
                new_project_id := pawtale_memoir_project_id(TG_ARGV[0], to_jsonb(NEW));
            END IF;
            INSERT INTO memoir_project_revision_events (project_id, transaction_id)
            SELECT DISTINCT value, txid_current()
            FROM unnest(ARRAY[old_project_id, new_project_id]) AS value
            JOIN projects ON projects.id = value AND projects.type = 'memoir'
            WHERE value IS NOT NULL
            ON CONFLICT ON CONSTRAINT uq_memoir_project_revision_events_transaction
            DO NOTHING;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pawtale_bump_memoir_project_content_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.type = 'memoir' THEN
                INSERT INTO memoir_project_revision_events (project_id, transaction_id)
                VALUES (NEW.id, txid_current())
                ON CONFLICT ON CONSTRAINT uq_memoir_project_revision_events_transaction
                DO NOTHING;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_memoir_revision_memoir_editing_jobs "
        "ON memoir_editing_jobs"
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pawtale_bump_memoir_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            old_project_id varchar;
            new_project_id varchar;
        BEGIN
            IF TG_OP != 'INSERT' THEN
                old_project_id := pawtale_memoir_project_id(TG_ARGV[0], to_jsonb(OLD));
            END IF;
            IF TG_OP != 'DELETE' THEN
                new_project_id := pawtale_memoir_project_id(TG_ARGV[0], to_jsonb(NEW));
            END IF;
            UPDATE projects
            SET memoir_data_revision = memoir_data_revision + 1
            WHERE type = 'memoir'
              AND id IN (
                SELECT DISTINCT value
                FROM unnest(ARRAY[old_project_id, new_project_id]) AS value
                WHERE value IS NOT NULL
              );
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pawtale_bump_memoir_project_content_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.type = 'memoir' THEN
                UPDATE projects
                SET memoir_data_revision = memoir_data_revision + 1
                WHERE id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_memoir_revision_memoir_editing_jobs
        AFTER INSERT OR UPDATE OR DELETE ON memoir_editing_jobs
        FOR EACH ROW EXECUTE FUNCTION pawtale_bump_memoir_revision('direct')
        """
    )
    op.drop_index(
        "ix_memoir_project_revision_events_project_id",
        table_name="memoir_project_revision_events",
    )
    op.drop_table("memoir_project_revision_events")
