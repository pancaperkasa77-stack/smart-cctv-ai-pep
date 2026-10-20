from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user
from app.db.session import get_db
from app.models.camera import Camera
from app.models.violation import Violation
from app.models.user import User
from app.schemas.report import CameraReportRow, DailyTrendRow, ReportsSummaryOut

router = APIRouter(prefix="/reports", tags=["Laporan & Analitik"])

@router.get("", response_model=ReportsSummaryOut)
def get_reports_summary(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    quick: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = date.today()
    if quick:
        days = 30 if quick == "30" else 7
        end_d = today
        start_d = today - timedelta(days=days - 1)
    elif from_date and to_date:
        start_d = from_date
        end_d = to_date
    else:
        end_d = today
        start_d = today - timedelta(days=6)

    if start_d > end_d:
        raise HTTPException(status_code=400, detail="Tanggal awal harus sebelum atau sama dengan tanggal akhir.")

    span_days = (end_d - start_d).days + 1
    if span_days > 31:
        raise HTTPException(status_code=400, detail=f"Rentang maksimal 31 hari (dipilih {span_days} hari).")

    start_dt = datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_d + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    # Query violations dalam rentang waktu
    violations = (
        db.query(Violation)
        .filter(Violation.created_at >= start_dt, Violation.created_at < end_dt)
        .all()
    )

    total_violations = len(violations)
    avg_per_day = round(total_violations / span_days, 1)

    case_rows = [v for v in violations if v.is_case]
    case_total = len(case_rows)
    completed_cases = [v for v in case_rows if v.status == "selesai"]
    completion_rate = (len(completed_cases) / case_total) if case_total > 0 else None

    # Hitung rata-rata waktu respons dalam menit jika ada resolved_at
    response_times = [
        (v.resolved_at - v.created_at).total_seconds() / 60.0
        for v in completed_cases if v.resolved_at and v.created_at
    ]
    avg_response_minutes = round(sum(response_times) / len(response_times), 1) if response_times else None

    apd_total = sum(1 for v in violations if v.category == "apd")
    vehicle_total = sum(1 for v in violations if v.category == "vehicle")
    apd_pct = int(round(100.0 * apd_total / total_violations)) if total_violations > 0 else 0
    vehicle_pct = (100 - apd_pct) if total_violations > 0 else 0

    # Tren harian
    daily_map = {start_d + timedelta(days=i): {"apd": 0, "vehicle": 0} for i in range(span_days)}
    for v in violations:
        v_date = v.created_at.date()
        if v_date in daily_map:
            if v.category in daily_map[v_date]:
                daily_map[v_date][v.category] += 1

    daily_trend = [
        DailyTrendRow(date=d, apd=val["apd"], vehicle=val["vehicle"])
        for d, val in sorted(daily_map.items())
    ]

    # Breakdown per kamera
    cameras = db.query(Camera).filter(Camera.is_active == True).all()
    by_camera = []
    for c in cameras:
        cnt = sum(1 for v in violations if v.camera_id == c.id)
        by_camera.append(
            CameraReportRow(
                camera_name=c.name,
                location=c.location,
                category=c.category,
                count=cnt,
            )
        )
    by_camera.sort(key=lambda r: r.count, reverse=True)

    months_id = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    def fmt_d(dt: date) -> str:
        return f"{dt.day:02d} {months_id[dt.month - 1]}"

    range_label = fmt_d(start_d) if start_d == end_d else f"{fmt_d(start_d)} – {fmt_d(end_d)}"

    return ReportsSummaryOut(
        start_date=start_d,
        end_date=end_d,
        range_label=range_label,
        total_violations=total_violations,
        avg_per_day=avg_per_day,
        case_total=case_total,
        completion_rate=completion_rate,
        avg_response_minutes=avg_response_minutes,
        apd_total=apd_total,
        vehicle_total=vehicle_total,
        apd_pct=apd_pct,
        vehicle_pct=vehicle_pct,
        daily_trend=daily_trend,
        by_camera=by_camera,
    )
