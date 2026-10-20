from datetime import datetime
from pydantic import BaseModel, ConfigDict

class CaseNoteCreate(BaseModel):
    text: str

class CaseNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    violation_id: int
    author_user_id: str | None = None
    author_name: str | None = None
    text: str
    created_at: datetime
