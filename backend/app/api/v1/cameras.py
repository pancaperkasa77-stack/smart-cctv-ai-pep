from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user, require_admin
from app.db.session import get_db
from app.models.camera import Camera
from app.models.user import User
from app.schemas.camera import CameraCreate, CameraOut, CameraUpdate

router = APIRouter(prefix="/cameras", tags=["Kamera"])

@router.get("", response_model=list[CameraOut])
def list_cameras(
    category: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Camera).filter(Camera.is_active == True)
    if category:
        query = query.filter(Camera.category == category)
    return [CameraOut.model_validate(c) for c in query.all()]

@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Kamera tidak ditemukan.")
    return CameraOut.model_validate(cam)

@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def create_camera(
    req: CameraCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    cam = Camera(**req.model_dump())
    db.add(cam)
    db.commit()
    db.refresh(cam)
    return CameraOut.model_validate(cam)

@router.put("/{camera_id}", response_model=CameraOut)
def update_camera(
    camera_id: int,
    req: CameraUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Kamera tidak ditemukan.")
    for key, val in req.model_dump(exclude_unset=True).items():
        setattr(cam, key, val)
    db.commit()
    db.refresh(cam)
    return CameraOut.model_validate(cam)
