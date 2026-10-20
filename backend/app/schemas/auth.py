from pydantic import BaseModel
from app.schemas.user import UserOut

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut

class LoginRequest(BaseModel):
    username: str
    password: str
