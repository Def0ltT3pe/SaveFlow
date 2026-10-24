from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import OperationalError

from app.api import api_router, metrics_router

app = FastAPI(
    title="SaveFlow — Risk Analytics",
    version="0.1.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
    redoc_url=None,
)
app.include_router(api_router)
app.include_router(metrics_router)


@app.exception_handler(OperationalError)
def database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "База данных недоступна. Запустите Postgres и обновите страницу."})


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/api/v1/docs")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
