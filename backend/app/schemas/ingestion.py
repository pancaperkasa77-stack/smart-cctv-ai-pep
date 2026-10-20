from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_JPEG_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES = 3 * 1024 * 1024


class DetectionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1, max_length=200)
    camera_key: Literal["live-1-apd", "live-2-apd", "live-1-vehicle", "live-2-vehicle"]
    camera_name: str = Field(min_length=1, max_length=100)
    category: Literal["apd", "vehicle"]
    label: str = Field(min_length=1, max_length=150)
    severity: Literal["warning", "critical"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    captured_at: datetime
    snapshot_jpeg: str = Field(min_length=1, max_length=4 * ((MAX_JPEG_BYTES + 2) // 3))

    @field_validator("event_key", "camera_name", "label")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value

    @field_validator("captured_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def matching_category(self):
        if not self.camera_key.endswith("-" + self.category):
            raise ValueError("camera_key and category disagree")
        return self


class DetectionEventOut(BaseModel):
    id: int
    event_key: str
    created: bool
