from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """
    Konfigurasi aplikasi — dibaca dari environment variables / file .env.
    Semua nilai ini diatur di docker-compose.yml atau file .env lokal.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────
    # Contoh: mssql+pyodbc://sa:Pass@sqlserver:1433/SmartCctvAiDb
    #         ?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
    database_url: str = ""

    # ── JWT / Autentikasi ─────────────────────────────────────────────────
    secret_key: str = "CHANGE_ME_IN_ENV"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 jam

    # ── Storage ───────────────────────────────────────────────────────────
    snapshot_dir: str = "/app/storage/snapshots"
    detection_ingest_token: str = ""
    ai_workers_enabled: bool = True

    # ── CORS ─────────────────────────────────────────────────────────────
    frontend_origin: str = "http://localhost:8086"

    # ── App metadata ──────────────────────────────────────────────────────
    app_name: str = "Smart CCTV AI — Pertamina EP"
    api_v1_prefix: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    """
    Mengembalikan instance Settings yang di-cache per proses.
    Gunakan sebagai FastAPI dependency: Depends(get_settings).
    """
    return Settings()


# Instance global untuk kemudahan import di seluruh modul
settings = get_settings()
