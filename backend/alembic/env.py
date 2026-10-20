"""
Alembic environment configuration.

env.py membaca DATABASE_URL dari environment variable (melalui app.core.config.settings)
sehingga satu alembic.ini cukup untuk semua environment (dev, docker, production).

Cara menjalankan migrasi:
    # Di dalam container backend (sudah otomatis saat startup via CMD di Dockerfile)
    alembic upgrade head

    # Generate migrasi baru setelah mengubah model:
    alembic revision --autogenerate -m "deskripsi_perubahan"
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# ── Tambahkan root project ke sys.path ───────────────────────────────────────
# Agar import app.* bekerja saat alembic dijalankan dari folder backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings
from app.db.session import Base

# Import semua models agar metadata ter-register sebelum autogenerate dijalankan
import app.models  # noqa: F401

# ── Konfigurasi logging dari alembic.ini ─────────────────────────────────────
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Metadata target untuk autogenerate ───────────────────────────────────────
target_metadata = Base.metadata


def get_url() -> str:
    """Baca DATABASE_URL dari settings (environment variable)."""
    return settings.database_url


def run_migrations_offline() -> None:
    """
    Mode offline: generate SQL script tanpa koneksi database.
    Berguna untuk review migrasi sebelum dieksekusi.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Mode online: koneksi langsung ke database dan jalankan migrasi.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
