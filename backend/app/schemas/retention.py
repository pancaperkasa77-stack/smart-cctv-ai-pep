from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class RetentionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    max_records: int = Field(ge=1, le=1_000_000, strict=True)


class RetentionChange(RetentionProposal):
    preview_token: str = Field(min_length=1, max_length=4096)
    confirmed: Literal[True]
    version: int = Field(ge=1, strict=True)


class RetentionSummary(BaseModel):
    enabled: bool
    max_records: int
    protected_open_cases: int
    total_records: int
    eligible_for_deletion: int
    pending_file_deletions: int
    last_run_at: datetime | None
    last_error: str | None
    version: int


class RetentionPreview(RetentionSummary):
    preview_token: str
