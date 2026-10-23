"""GET /metrics — Prometheus, без авторизации (скрейпер должен иметь доступ)."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.risk import service

router = APIRouter(tags=["metrics"])

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", response_class=Response)
def metrics(db: Session = Depends(get_db)) -> Response:
    """Получение агрегированных метрик в формате Prometheus."""
    return Response(service.render_metrics(db), media_type=PROMETHEUS_CONTENT_TYPE)
