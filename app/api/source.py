from fastapi import APIRouter

from app.schemas import Snapshot, SnapshotResult
from app.services import state

router = APIRouter(prefix="/source", tags=["source"])


@router.put("/snapshot", response_model=SnapshotResult)
def put_snapshot(body: Snapshot) -> SnapshotResult:
    """Загрузка или полная замена снимка исходных данных с автоматическим пересчётом."""
    # TODO: атомарная замена в БД одной транзакцией; настройки модели не трогаем
    state.snapshot = body.model_dump()
    data_version = state.recompute_all()
    return SnapshotResult(
        customers=len(body.customers),
        services=len(body.services),
        transactions=len(body.transactions),
        data_version=data_version,
    )
