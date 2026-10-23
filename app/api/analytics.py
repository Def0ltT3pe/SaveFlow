from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.risk import service
from app.risk.schemas import ChurnTimeline, RiskSummary

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=RiskSummary)
def summary(db: Session = Depends(get_db)) -> dict:
    """Получение сводных показателей риска и денежной оценки потенциальных потерь."""
    return service.get_summary(db)


@router.get("/churn-timeline", response_model=ChurnTimeline)
def churn_timeline(days_back: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)) -> dict:
    """График оттока: факт удалений и прогноз непродлений на горизонт."""
    return service.get_churn_timeline(db, days_back=days_back)
