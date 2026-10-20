from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.api.v1.auth import get_current_user
from app.db.session import get_db
from app.models.violation import Violation
from app.models.user import User
from app.schemas.violation import ViolationOut

router = APIRouter(prefix="/violations", tags=["Pelanggaran"])

def _to_out(v: Violation) -> ViolationOut:
    vo = ViolationOut.model_validate(v)
    if v.camera:
        vo.camera_name = v.camera.name
        vo.location = v.camera.location
    if v.assigned_to:
        vo.assigned_to_name = v.assigned_to.display_name
    if v.notes:
        for n, no in zip(v.notes, vo.notes):
            if n.author:
                no.author_name = n.author.display_name
    return vo

@router.get("", response_model=list[ViolationOut])
def list_violations(
    category: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Violation).options(
        joinedload(Violation.camera),
        joinedload(Violation.assigned_to),
        joinedload(Violation.notes),
    )
    if category:
        query = query.filter(Violation.category == category)

    query = query.order_by(Violation.created_at.desc(), Violation.id.desc()).limit(limit)
    return [_to_out(v) for v in query.all()]

@router.get("/dashboard-stats")
def dashboard_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.models.camera import Camera
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    total_cameras = db.query(Camera).filter(Camera.is_active == True).count()
    total_violations = db.query(Violation).count()
    open_cases = db.query(Violation).filter(Violation.is_case == True, Violation.status != "selesai").count()
    last_24h = db.query(Violation).filter(Violation.created_at >= since_24h).count()

    apd_cams = db.query(Camera).filter(Camera.category == "apd", Camera.is_active == True).count()
    apd_viols = db.query(Violation).filter(Violation.category == "apd").count()

    veh_cams = db.query(Camera).filter(Camera.category == "vehicle", Camera.is_active == True).count()
    veh_viols = db.query(Violation).filter(Violation.category == "vehicle").count()

    recent_feed = (
        db.query(Violation)
        .options(joinedload(Violation.camera))
        .order_by(Violation.created_at.desc())
        .limit(8)
        .all()
    )

    return {
        "total_cameras": total_cameras,
        "total_violations": total_violations,
        "open_cases": open_cases,
        "last_24h": last_24h,
        "apd_camera_count": apd_cams,
        "apd_violation_count": apd_viols,
        "vehicle_camera_count": veh_cams,
        "vehicle_violation_count": veh_viols,
        "feed": [_to_out(v) for v in recent_feed],
    }


@router.get("/category-stats")
def category_stats(
    category: Literal["apd", "vehicle"],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    query = db.query(Violation).filter(Violation.category == category)
    return {
        "total_today": query.filter(Violation.created_at >= start,
                                    Violation.created_at < start + timedelta(days=1)).count(),
        "open_cases": query.filter(Violation.is_case == True, Violation.status != "selesai").count(),
    }
