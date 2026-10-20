from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Camera(Base):
    """
    Kamera CCTV yang terdaftar di sistem.
    category: 'apd' | 'vehicle'
    stream_path: identifier MediaMTX (mis. 'cam1') → browser plays via WebRTC
    rtsp_url: URL RTSP kamera fisik (dibaca oleh AI stream_worker.py)
    """
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # 'apd' | 'vehicle'
    stream_path: Mapped[str | None] = mapped_column(String(50), nullable=True)   # MediaMTX path
    rtsp_url: Mapped[str | None] = mapped_column(String(255), nullable=True)     # Live RTSP feed
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Camera id={self.id} name={self.name!r} category={self.category!r}>"
