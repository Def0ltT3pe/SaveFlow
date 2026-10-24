from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.risk import service
from app.risk.schemas import CustomerDetail, CustomerPage, RiskLevel, SortField, SortOrder

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=CustomerPage)
def list_customers(
    sort: SortField = "score",
    order: SortOrder = "desc",
    level: list[RiskLevel] | None = Query(None, description="Фильтр по уровням, можно несколько"),
    search: str | None = Query(None, description="Подстрока email или ID клиента (42 или #42)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """Получение списка клиентов с оценками риска; фильтрация и сортировка рейтинга."""
    return service.get_customers(db, sort=sort, order=order, levels=level, search=search,
                                 limit=limit, offset=offset)


@router.get("/{customer_id}", response_model=CustomerDetail)
def get_customer(customer_id: int, db: Session = Depends(get_db)) -> dict:
    """Получение подробной оценки клиента, его услуг, признаков риска и объяснений."""
    customer = service.get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return customer
