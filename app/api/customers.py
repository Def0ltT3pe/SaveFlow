from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.schemas import CustomerDetail, CustomerList, RiskLevel

router = APIRouter(prefix="/customers", tags=["customers"])

SortField = Literal["id", "email", "balance", "total_replenished", "risk_score"]


@router.get("", response_model=CustomerList)
def list_customers(
    sort_by: SortField = "risk_score",
    order: Literal["asc", "desc"] = "desc",
    risk_level: RiskLevel | None = None,
    search: str | None = Query(None, description="Подстрока в ID или email"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> CustomerList:
    """Получение списка клиентов с оценками риска; фильтрация и сортировка рейтинга."""
    # TODO: выборка из risk_scores с сортировкой на стороне БД
    return CustomerList(total=0, items=[])


@router.get("/{customer_id}", response_model=CustomerDetail)
def get_customer(customer_id: str) -> CustomerDetail:
    """Получение подробной оценки клиента, его услуг, признаков риска и объяснений."""
    # TODO: клиент + услуги + вклад признаков + денежная оценка
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
