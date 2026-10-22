from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlaybackChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["raw", "ai"]
    version: int = Field(ge=1, strict=True)


class PlaybackSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mode: Literal["raw", "ai"]
    version: int
