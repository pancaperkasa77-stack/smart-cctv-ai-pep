from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user, require_admin
from app.db.session import get_db
from app.models.playback import PlaybackSettings
from app.models.user import User
from app.schemas.playback import PlaybackChange, PlaybackSummary

router = APIRouter(prefix="/settings/playback", tags=["Playback"])


@router.get("", response_model=PlaybackSummary)
def get_playback(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(PlaybackSettings, 1)
    if row is None:
        raise HTTPException(503, "Playback settings unavailable; apply database migrations.")
    return row


@router.put("", response_model=PlaybackSummary)
def update_playback(req: PlaybackChange, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        result = db.execute(
            update(PlaybackSettings)
            .where(PlaybackSettings.id == 1, PlaybackSettings.version == req.version)
            .values(mode=req.mode, version=PlaybackSettings.version + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise HTTPException(409, "Playback settings changed; reload and try again.")
        db.commit()
        return PlaybackSummary(mode=req.mode, version=req.version + 1)
    except Exception:
        db.rollback()
        raise
