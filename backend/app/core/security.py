from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# ── Password Hashing ──────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """Menghasilkan bcrypt hash dari plaintext password."""
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Memverifikasi plaintext password terhadap hash yang tersimpan."""
    try:
        pwd_bytes = plain_password.encode("utf-8")
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False


# ── JWT Token ─────────────────────────────────────────────────────────────────

def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """
    Membuat JWT access token.

    Args:
        data: Payload yang akan di-encode (biasanya mengandung `sub` berupa user ID).
        expires_delta: Durasi kedaluwarsa. Jika None, menggunakan nilai dari settings.

    Returns:
        JWT token string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Mendekode dan memvalidasi JWT access token.

    Returns:
        Payload dict.

    Raises:
        JWTError: Jika token tidak valid atau sudah kedaluwarsa.
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
