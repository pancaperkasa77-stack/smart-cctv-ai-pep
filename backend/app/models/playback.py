"""Global display preference; does not control inference or persistence."""
from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PlaybackSettings(Base):
    __tablename__ = "playback_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_playback_singleton"),
        CheckConstraint("mode IN ('raw', 'ai')", name="ck_playback_mode"),
        CheckConstraint("version >= 1", name="ck_playback_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, autoincrement=False)
    mode: Mapped[str] = mapped_column(String(3), nullable=False, default="raw", server_default="raw")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
