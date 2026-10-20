from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.security import create_access_token, decode_access_token, verify_password, hash_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, Token
from app.schemas.user import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/auth", tags=["Autentikasi"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login-form")

def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Kredensial token tidak valid atau kedaluwarsa.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.get(User, user_id)
    if user is None or not user.is_enabled:
        raise credentials_exception
    return user

def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak. Peran Admin diperlukan.",
        )
    return current_user

@router.post("/login", response_model=Token)
def login_json(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah.",
        )
    if not user.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun Anda telah dinonaktifkan.",
        )

    token_str = create_access_token({"sub": user.id, "role": user.role})
    return Token(access_token=token_str, user=UserOut.model_validate(user))

@router.post("/login-form", response_model=Token)
def login_form(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
):
    return login_json(LoginRequest(username=form_data.username, password=form_data.password), db)

@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user

@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    users = db.query(User).order_by(User.username).all()
    return [UserOut.model_validate(u) for u in users]

@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    req: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username sudah digunakan.",
        )
    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        first_name=req.first_name,
        last_name=req.last_name,
        email=req.email,
        role=req.role,
        is_enabled=req.is_enabled,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)

@router.post("/users/{user_id}/toggle", response_model=UserOut)
def toggle_user(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Anda tidak dapat menonaktifkan akun sendiri.",
        )
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")
    user.is_enabled = not user.is_enabled
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)
