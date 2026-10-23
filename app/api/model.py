from fastapi import APIRouter, HTTPException, status

from app.schemas import ModelSettings, ModelSettingsPatch
from app.services import state

router = APIRouter(prefix="/model", tags=["model"])


@router.get("/settings", response_model=ModelSettings)
def get_settings() -> ModelSettings:
    """Получение действующих настроек и коэффициентов модели."""
    return state.model_settings


@router.patch("/settings", response_model=ModelSettings)
def patch_settings(body: ModelSettingsPatch) -> ModelSettings:
    """Изменение настроек модели с автоматическим пересчётом результатов."""
    current = state.model_settings
    weights = dict(current.weights)
    if body.weights:
        unknown = set(body.weights) - set(weights)
        if unknown:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown features: {sorted(unknown)}")
        weights.update(body.weights)
    state.model_settings = ModelSettings(
        version=current.version + 1,
        weights=weights,
        thresholds=body.thresholds or current.thresholds,
    )
    state.recompute_all()
    return state.model_settings
