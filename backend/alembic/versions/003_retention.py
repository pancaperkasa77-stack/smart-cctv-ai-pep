"""Add disabled retention policy and durable cleanup/replay metadata. No source deletion."""
import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table("retentionsettings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("max_records", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_retention_singleton"),
        sa.CheckConstraint("max_records >= 1 AND max_records <= 1000000", name="ck_retention_max"))
    op.bulk_insert(table, [dict(id=1, enabled=False, max_records=1000, version=1)])
    op.create_table("file_gc",
        sa.Column("filename", sa.String(255), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(500), nullable=True))
    op.create_table("detection_tombstones",
        sa.Column("source_event_key", sa.String(200), primary_key=True),
        sa.Column("source_payload_hash", sa.String(64), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False))
    op.create_index("ix_violations_retention_order", "violations", ["created_at", "id"])
    op.create_index("ix_violations_snapshot_path", "violations", ["snapshot_path"])


def downgrade():
    op.drop_index("ix_violations_snapshot_path", table_name="violations")
    op.drop_index("ix_violations_retention_order", table_name="violations")
    op.drop_table("detection_tombstones")
    op.drop_table("file_gc")
    op.drop_table("retentionsettings")
