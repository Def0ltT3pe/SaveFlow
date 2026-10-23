from fastapi import APIRouter

from app.schemas import Summary
from app.services.analytics import aggregate

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=Summary)
def summary() -> Summary:
    """Получение сводных показателей риска и денежной оценки потенциальных потерь."""
    return aggregate()
