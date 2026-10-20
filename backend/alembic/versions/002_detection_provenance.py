"""Add nullable detection provenance without changing existing camera/history rows."""
import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("violations", sa.Column("source_event_key", sa.String(200), nullable=True))
    op.add_column("violations", sa.Column("source_payload_hash", sa.String(64), nullable=True))
    op.add_column("violations", sa.Column("snapshot_sha256", sa.String(64), nullable=True))
    op.add_column("violations", sa.Column("confidence", sa.Float(), nullable=True))
    op.create_index("uq_violations_source_event_key", "violations", ["source_event_key"], unique=True,
                    mssql_where=sa.text("source_event_key IS NOT NULL"),
                    sqlite_where=sa.text("source_event_key IS NOT NULL"))


def downgrade():
    op.drop_index("uq_violations_source_event_key", table_name="violations")
    for column in ("confidence", "snapshot_sha256", "source_payload_hash", "source_event_key"):
        op.drop_column("violations", column)
