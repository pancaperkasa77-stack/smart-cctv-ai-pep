from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Float, Index, text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Violation(Base):
    """
    Satu deteksi pelanggaran yang dicatat oleh AI engine.

    Siklus hidup:
    1. AI mendeteksi → is_case=False, status='baru' (alert biasa)
    2. Operator/Supervisor mempromosikan → is_case=True (menjadi Kasus)
    3. Kasus diproses → status='diproses', assigned_to_user_id diisi
    4. Kasus selesai → status='selesai', resolved_at diisi

    category : 'apd' | 'vehicle'
    severity : 'warning' | 'critical'
    status   : 'baru' | 'diproses' | 'selesai'
    """
    __tablename__ = "violations"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    has_snapshot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    snapshot_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_case: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="baru", index=True)
    assigned_to_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    source_event_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("uq_violations_source_event_key", "source_event_key", unique=True,
              mssql_where=text("source_event_key IS NOT NULL"),
              sqlite_where=text("source_event_key IS NOT NULL")),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    camera = relationship("Camera", foreign_keys=[camera_id], lazy="select")
    assigned_to = relationship("User", foreign_keys=[assigned_to_user_id], lazy="select")
    notes = relationship(
        "CaseNote",
        back_populates="violation",
        order_by="CaseNote.created_at",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Violation id={self.id} category={self.category!r} "
            f"label={self.label!r} status={self.status!r}>"
        )
