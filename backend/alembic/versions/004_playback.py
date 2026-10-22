"""Add global playback display preference. Inference and existing tables unchanged."""
import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "playback_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("mode", sa.String(3), nullable=False, server_default="raw"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1", name="ck_playback_singleton"),
        sa.CheckConstraint("mode IN ('raw', 'ai')", name="ck_playback_mode"),
        sa.CheckConstraint("version >= 1", name="ck_playback_version"),
    )
    op.bulk_insert(table, [dict(id=1, mode="raw", version=1)])


def downgrade():
    op.drop_table("playback_settings")
