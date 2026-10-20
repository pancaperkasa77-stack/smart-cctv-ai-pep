from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.api.v1.auth import get_current_user
from app.api.v1.violations import _to_out
from app.db.session import get_db
from app.services.retention import acquire_lock
from app.models.violation import Violation
from app.models.user import User
from app.schemas.violation import ViolationOut, ViolationUpdate

router = APIRouter(prefix="/cases", tags=["Kasus Investigasi"])

@router.get("", response_model=list[ViolationOut])
def list_cases(
    limit: int = Query(100, ge=1, le=100),
    filter_type: str = "all",  # 'all' | 'apd' | 'vehicle' | 'open'
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        db.query(Violation)
        .options(
            joinedload(Violation.camera),
            joinedload(Violation.assigned_to),
            joinedload(Violation.notes),
        )
        .filter(Violation.is_case == True)
    )

    if filter_type in ("apd", "vehicle"):
        query = query.filter(Violation.category == filter_type)
    elif filter_type == "open":
        query = query.filter(Violation.status != "selesai")

    query = query.order_by(Violation.created_at.desc(), Violation.id.desc()).limit(limit)
    return [_to_out(v) for v in query.all()]

@router.post("/{violation_id}/promote", response_model=ViolationOut)
def promote_to_case(
    violation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acquire_lock(db)
    v = db.get(Violation, violation_id)
    if not v:
        raise HTTPException(status_code=404, detail="Pelanggaran tidak ditemukan.")
    v.is_case = True
    if v.status == "baru":
        v.status = "baru"
    db.flush()
    result = _to_out(v)
    db.commit()
    return result

@router.patch("/{violation_id}", response_model=ViolationOut)
def update_case(
    violation_id: int,
    req: ViolationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acquire_lock(db)
    v = db.get(Violation, violation_id)
    if not v or not v.is_case:
        raise HTTPException(status_code=404, detail="Kasus tidak ditemukan.")

    if req.is_case is not None:
        v.is_case = req.is_case

    if req.status is not None:
        v.status = req.status
        if req.status == "selesai":
            if v.resolved_at is None:
                v.resolved_at = datetime.now(timezone.utc)
        else:
            v.resolved_at = None

    if req.assigned_to_user_id is not None:
        if req.assigned_to_user_id == "":
            v.assigned_to_user_id = None
        else:
            assignee = db.get(User, req.assigned_to_user_id)
            if not assignee:
                raise HTTPException(status_code=404, detail="Operator penanggung jawab tidak ditemukan.")
            if not assignee.is_enabled:
                raise HTTPException(status_code=400, detail="Operator penanggung jawab dinonaktifkan.")
            v.assigned_to_user_id = assignee.id

    db.flush()
    result = _to_out(v)
    db.commit()
    return result
