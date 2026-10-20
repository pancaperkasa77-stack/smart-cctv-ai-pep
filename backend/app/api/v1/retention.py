from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from app.api.v1.auth import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.retention import RetentionChange, RetentionPreview, RetentionProposal, RetentionSummary
from app.services.retention import acquire_lock, policy, summary

router = APIRouter(prefix="/settings/retention", tags=["Retention"])


def check_writer(enabled: bool):
    if enabled and settings.ai_workers_enabled:
        raise HTTPException(409, "Disable legacy AI workers before enabling retention (AI_WORKERS_ENABLED=false).")


@router.get("", response_model=RetentionSummary)
def get_retention(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return summary(db)


@router.post("/preview", response_model=RetentionPreview)
def preview_retention(req: RetentionProposal, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    check_writer(req.enabled)
    admin_id = admin.id
    acquire_lock(db)
    try:
        result = summary(db, req.enabled, req.max_records)
        token = jwt.encode(dict(sub=admin_id, scope="retention-preview", enabled=req.enabled,
            max_records=req.max_records, version=result["version"],
            exp=datetime.now(timezone.utc) + timedelta(minutes=10)), settings.secret_key, algorithm=settings.algorithm)
        return result | {"preview_token": token}
    finally:
        db.rollback()


@router.put("", response_model=RetentionSummary)
def update_retention(req: RetentionChange, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    check_writer(req.enabled)
    admin_id = admin.id
    try:
        claims = jwt.decode(req.preview_token, settings.secret_key, algorithms=[settings.algorithm],
                            options={"require_exp": True, "require_sub": True})
    except JWTError as exc:
        raise HTTPException(409, "Preview invalid or expired; preview again.") from exc
    expected = dict(sub=admin_id, scope="retention-preview", enabled=req.enabled,
                    max_records=req.max_records, version=req.version)
    if any(claims.get(k) != v for k, v in expected.items()):
        raise HTTPException(409, "Preview does not match approved settings or administrator.")
    acquire_lock(db)
    try:
        row = policy(db)
        if row.version != req.version:
            raise HTTPException(409, "Settings changed or preview already used; preview again.")
        row.enabled, row.max_records = req.enabled, req.max_records
        row.version += 1
        db.flush()
        result = summary(db)
        db.commit()  # Worker observes policy on next bounded cycle, never inline.
        return result
    except Exception:
        db.rollback()
        raise
