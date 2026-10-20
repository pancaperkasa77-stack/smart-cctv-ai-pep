from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user
from app.db.session import get_db
from app.services.retention import acquire_lock
from app.models.case_note import CaseNote
from app.models.violation import Violation
from app.models.user import User
from app.schemas.case_note import CaseNoteCreate, CaseNoteOut

router = APIRouter(prefix="/cases/{violation_id}/notes", tags=["Catatan Investigasi"])

@router.post("", response_model=CaseNoteOut, status_code=status.HTTP_201_CREATED)
def add_note(
    violation_id: int,
    req: CaseNoteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    acquire_lock(db)
    v = db.get(Violation, violation_id)
    if not v or not v.is_case:
        raise HTTPException(status_code=404, detail="Kasus tidak ditemukan.")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Teks catatan tidak boleh kosong.")

    note = CaseNote(
        violation_id=violation_id,
        author_user_id=user.id,
        text=req.text.strip(),
    )
    db.add(note)
    db.flush()
    db.refresh(note)
    out = CaseNoteOut.model_validate(note)
    out.author_name = user.display_name
    db.commit()
    return out
