from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.risk import service
from app.risk.schemas import ModelSettingsOut, ModelSettingsPatch

router = APIRouter(prefix="/model", tags=["model"])


@router.get("/settings", response_model=ModelSettingsOut)
def get_settings(db: Session = Depends(get_db)) -> dict:
    """Получение действующих настроек и коэффициентов модели."""
    return service.get_settings(db)


@router.patch("/settings", response_model=ModelSettingsOut)
def patch_settings(body: ModelSettingsPatch, db: Session = Depends(get_db)) -> dict:
    """Изменение настроек модели с автоматическим пересчётом результатов."""
    try:
        return service.update_settings(db, body)
    except ValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            e.errors(include_url=False, include_context=False)) from e
