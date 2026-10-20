from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
# pool_pre_ping=True → mendeteksi koneksi yang sudah mati sebelum digunakan
# pool_size dan max_overflow disesuaikan untuk workload on-premise ringan
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,  # Ubah ke True untuk debug SQL query di console
)

# ── Session Factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


# ── Declarative Base ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Base class untuk semua SQLAlchemy ORM models."""
    pass


# ── Dependency: FastAPI Request-Scoped Session ────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yang menyediakan satu database session per HTTP request.
    Session ditutup otomatis setelah request selesai (termasuk saat error).

    Penggunaan di endpoint:
        db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
