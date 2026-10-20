from datetime import date
from pydantic import BaseModel

class DailyTrendRow(BaseModel):
    date: date
    apd: int
    vehicle: int

class CameraReportRow(BaseModel):
    camera_name: str
    location: str
    category: str
    count: int

class ReportsSummaryOut(BaseModel):
    start_date: date
    end_date: date
    range_label: str
    total_violations: int
    avg_per_day: float
    case_total: int
    completion_rate: float | None = None
    avg_response_minutes: float | None = None
    apd_total: int
    vehicle_total: int
    apd_pct: int
    vehicle_pct: int
    daily_trend: list[DailyTrendRow]
    by_camera: list[CameraReportRow]
