"""001_initial_schema

Initial database schema untuk Smart CCTV AI — Pertamina EP.
Membuat tabel: users, cameras, violations, case_notes
Lengkap dengan index, foreign keys, dan constraint.

Revision ID : 001
Revises     : (kosong — migrasi pertama)
Create Date : 2026-09-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# ── Identitas Migrasi ─────────────────────────────────────────────────────────
revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ─────────────────────────────────────────────────────────────────────────
    # Tabel: users
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("email", sa.String(150), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="operator"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("GETUTCDATE()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Tabel: cameras
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("ip_address", sa.String(50), nullable=False),
        sa.Column("location", sa.String(150), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),          # 'apd' | 'vehicle'
        sa.Column("stream_path", sa.String(50), nullable=True),        # MediaMTX path
        sa.Column("rtsp_url", sa.String(255), nullable=True),          # RTSP kamera fisik
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("GETUTCDATE()"),
        ),
    )
    op.create_index("ix_cameras_category", "cameras", ["category"])

    # ─────────────────────────────────────────────────────────────────────────
    # Tabel: violations
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "violations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "camera_id",
            sa.Integer(),
            sa.ForeignKey("cameras.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("category", sa.String(20), nullable=False),          # 'apd' | 'vehicle'
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="warning"),
        sa.Column("has_snapshot", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("snapshot_path", sa.String(255), nullable=True),
        sa.Column("is_case", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="baru"),
        sa.Column(
            "assigned_to_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("GETUTCDATE()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Index untuk query dashboard & filter
    op.create_index("ix_violations_created_at", "violations", ["created_at"])
    op.create_index("ix_violations_category", "violations", ["category"])
    op.create_index("ix_violations_is_case", "violations", ["is_case"])
    op.create_index("ix_violations_status", "violations", ["status"])
    op.create_index("ix_violations_camera_id", "violations", ["camera_id"])

    # ─────────────────────────────────────────────────────────────────────────
    # Tabel: case_notes
    # ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "case_notes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "violation_id",
            sa.BigInteger(),
            sa.ForeignKey("violations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("GETUTCDATE()"),
        ),
    )
    op.create_index("ix_case_notes_violation_id", "case_notes", ["violation_id"])


def downgrade() -> None:
    """
    Menghapus semua tabel yang dibuat di upgrade().
    PERINGATAN: Seluruh data akan hilang. Jalankan hanya di lingkungan development.
    """
    op.drop_table("case_notes")
    op.drop_table("violations")
    op.drop_table("cameras")
    op.drop_table("users")
