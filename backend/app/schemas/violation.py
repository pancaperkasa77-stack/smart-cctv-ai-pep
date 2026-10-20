from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict
from app.schemas.case_note import CaseNoteOut

class ViolationBase(BaseModel):
    camera_id: int | None = None
    category: str  # 'apd' | 'vehicle'
    label: str
    severity: str = "warning"
    has_snapshot: bool = False
    snapshot_path: str | None = None

class ViolationCreate(ViolationBase):
    pass

class ViolationUpdate(BaseModel):
    is_case: bool | None = None
    status: Literal["baru", "diproses", "selesai"] | None = None  # 'baru' | 'diproses' | 'selesai'
    assigned_to_user_id: str | None = None

class ViolationOut(ViolationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_event_key: str | None = None
    confidence: float | None = None
    camera_name: str | None = None
    location: str | None = None
    is_case: bool
    status: str
    assigned_to_user_id: str | None = None
    assigned_to_name: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    notes: list[CaseNoteOut] = []
