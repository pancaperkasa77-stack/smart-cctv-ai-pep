from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class CaseNote(Base):
    """
    Catatan investigasi yang ditulis operator/supervisor pada sebuah kasus.
    Setiap kasus (Violation dengan is_case=True) dapat memiliki banyak catatan.
    """
    __tablename__ = "case_notes"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    violation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("violations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────
    violation = relationship("Violation", back_populates="notes")
    author = relationship("User", foreign_keys=[author_user_id], lazy="select")

    def __repr__(self) -> str:
        return f"<CaseNote id={self.id} violation_id={self.violation_id}>"
