from fastapi import FastAPI
from fastapi.responses import RedirectResponse

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


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/api/v1/docs")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
