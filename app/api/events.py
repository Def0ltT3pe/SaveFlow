import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.security import get_user_from_query
from app.schemas import Versions
from app.services import state

router = APIRouter(prefix="/events", tags=["events"])
stream_router = APIRouter(prefix="/events", tags=["events"])

POLL_INTERVAL_SECONDS = 1.0


def current_versions() -> Versions:
    return Versions(data_version=state.data_version, model_version=state.model_settings.version)


@router.get("/version", response_model=Versions)
def version() -> Versions:
    """Лёгкий polling: фронт перезапрашивает данные, когда версия изменилась."""
    return current_versions()


@stream_router.get("/stream", dependencies=[Depends(get_user_from_query)])
async def stream(request: Request) -> StreamingResponse:
    """SSE: событие `update` при каждом изменении данных или настроек модели."""

    async def events():
        last = None
        while not await request.is_disconnected():
            now = current_versions()
            if now != last:
                yield f"event: update\ndata: {json.dumps(now.model_dump())}\n\n"
                last = now
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
