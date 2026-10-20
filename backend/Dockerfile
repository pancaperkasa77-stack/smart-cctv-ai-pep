# ─────────────────────────────────────────────────────────────────────────────
# Backend FastAPI — Dockerfile
# Base: python:3.11-slim-bookworm
# Includes: Microsoft ODBC Driver 18 for SQL Server
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim-bookworm AS base

# ── System dependencies & Microsoft ODBC Driver 18 ────────────────────────
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        curl gnupg2 ca-certificates apt-transport-https \
        libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 \
    && \
    # Tambahkan repo Microsoft
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] \
         https://packages.microsoft.com/debian/12/prod bookworm main" \
         > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update -qq && \
    ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Copy application source ───────────────────────────────────────────────
COPY . .

# ── Buat direktori storage snapshot ──────────────────────────────────────
RUN mkdir -p /app/storage/snapshots

EXPOSE 8000

# Jalankan migrasi Alembic terlebih dahulu, baru start Uvicorn
CMD ["sh", "-c", "alembic upgrade head && python -m app.db.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
