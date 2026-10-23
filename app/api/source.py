from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.risk import service
from app.risk.schemas import SnapshotIn, SnapshotResult

router = APIRouter(prefix="/source", tags=["source"])


@router.put("/snapshot", response_model=SnapshotResult)
def put_snapshot(body: SnapshotIn, db: Session = Depends(get_db)) -> dict:
    """Загрузка или полная замена снимка исходных данных с автоматическим пересчётом."""
    return service.replace_snapshot(db, body)
