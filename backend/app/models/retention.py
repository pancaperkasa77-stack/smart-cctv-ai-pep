"""Retention policy, durable file cleanup, and compact ingestion replay history."""
from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class RetentionSettings(Base):
    __tablename__ = "retentionsettings"
    __table_args__ = (CheckConstraint("id = 1"), CheckConstraint("max_records >= 1 AND max_records <= 1000000"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_records: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))


class FileGC(Base):
    __tablename__ = "file_gc"
    filename: Mapped[str] = mapped_column(String(255), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))


class DetectionTombstone(Base):
    __tablename__ = "detection_tombstones"
    source_event_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[int] = mapped_column(BigInteger, nullable=False)
