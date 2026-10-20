from datetime import datetime
from pydantic import BaseModel, ConfigDict

class UserBase(BaseModel):
    username: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    role: str = "operator"
    is_enabled: bool = True

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    role: str | None = None
    is_enabled: bool | None = None
    password: str | None = None

class UserOut(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str
    created_at: datetime
    updated_at: datetime | None = None
