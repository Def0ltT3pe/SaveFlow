import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import get_user_from_query
from app.db.models import ModelSettingsRow, RiskRun
from app.db.session import session_factory
from app.schemas import Versions

router = APIRouter(prefix="/events", tags=["events"])
stream_router = APIRouter(prefix="/events", tags=["events"])

POLL_INTERVAL_SECONDS = 1.0


def current_versions(db: Session) -> Versions:
    run_id = db.scalar(select(RiskRun.id).order_by(RiskRun.id.desc()).limit(1))
    settings_version = db.scalar(select(ModelSettingsRow.version).where(ModelSettingsRow.id == 1)) or 0
    return Versions(run_id=run_id, settings_version=settings_version)


def _read_versions() -> Versions:
    with session_factory()() as db:
        return current_versions(db)


@router.get("/version", response_model=Versions)
def version(db: Session = Depends(get_db)) -> Versions:
    """Лёгкий polling: фронт перезапрашивает данные, когда runId изменился."""
    return current_versions(db)


@stream_router.get("/stream", dependencies=[Depends(get_user_from_query)])
async def stream(request: Request) -> StreamingResponse:
    """SSE: событие `update` после каждого пересчёта модели (новые данные или настройки)."""

    async def events():
        last = None
        while not await request.is_disconnected():
            now = await run_in_threadpool(_read_versions)
            if now != last:
                yield f"event: update\ndata: {now.model_dump_json(by_alias=True)}\n\n"
                last = now
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
