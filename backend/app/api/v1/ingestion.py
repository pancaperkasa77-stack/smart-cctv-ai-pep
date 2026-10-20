import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.session import get_db
from app.schemas.ingestion import DetectionEvent, DetectionEventOut, MAX_BODY_BYTES
from app.services.ingestion import persist_detection

router = APIRouter(prefix="/internal", include_in_schema=False)


def require_detection_token(request: Request) -> None:
    expected = settings.detection_ingest_token
    if not expected.strip():
        raise HTTPException(503, "Detection ingestion is disabled.")
    authorization = request.headers.get("authorization", "")
    scheme, _, bearer = authorization.partition(" ")
    token = bearer if scheme.lower() == "bearer" else request.headers.get("x-detection-token", "")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "Invalid detection token.", headers={"WWW-Authenticate": "Bearer"})


@router.post("/detection-events", response_model=DetectionEventOut)
async def ingest_detection(request: Request, _: None = Depends(require_detection_token),
                           db: Session = Depends(get_db)):
    # Stream limit applies even without Content-Length (chunked uploads).
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Detection request exceeds 3 MiB.")
        body.extend(chunk)
    try:
        event = DetectionEvent.model_validate(json.loads(body))
    except (ValidationError, ValueError, UnicodeDecodeError):
        # Never reflect the token or multi-megabyte image through error details.
        raise HTTPException(422, "Invalid detection event payload.") from None
    try:
        return await run_in_threadpool(persist_detection, db, event, settings.snapshot_dir)
    except (SQLAlchemyError, OSError):
        raise HTTPException(503, "Detection persistence unavailable; retry event.") from None
