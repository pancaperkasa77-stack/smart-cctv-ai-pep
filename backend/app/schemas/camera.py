from datetime import datetime
from pydantic import BaseModel, ConfigDict

class CameraBase(BaseModel):
    name: str
    ip_address: str
    location: str
    category: str  # 'apd' | 'vehicle'
    stream_path: str | None = None
    rtsp_url: str | None = None
    is_active: bool = True

class CameraCreate(CameraBase):
    pass

class CameraUpdate(BaseModel):
    name: str | None = None
    ip_address: str | None = None
    location: str | None = None
    category: str | None = None
    stream_path: str | None = None
    rtsp_url: str | None = None
    is_active: bool | None = None

class CameraOut(CameraBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
