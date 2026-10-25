"""Сборка роутеров.

api_router — всё под /api/v1; защищённые роутеры закрыты на уровне подключения,
чтобы нельзя было забыть авторизацию на отдельном эндпоинте.
Публичные: POST /api/v1/auth/login, /api/v1/auth/oidc/* (вход через OIDC; кроме POST /auth/oidc/logout), GET /metrics,
/api/v1/docs, /api/v1/openapi.json, /healthz.
"""
from fastapi import APIRouter, Depends

from app.api import analytics, auth, customers, events, metrics, model, oidc, source
from app.core.security import get_current_user

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.public_router)
api_router.include_router(oidc.router)
api_router.include_router(auth.router, dependencies=[Depends(get_current_user)])
api_router.include_router(oidc.session_router, dependencies=[Depends(get_current_user)])
api_router.include_router(customers.router, dependencies=[Depends(get_current_user)])
api_router.include_router(analytics.router, dependencies=[Depends(get_current_user)])
api_router.include_router(model.router, dependencies=[Depends(get_current_user)])
api_router.include_router(source.router, dependencies=[Depends(get_current_user)])
api_router.include_router(events.router, dependencies=[Depends(get_current_user)])
api_router.include_router(events.stream_router)

# /metrics живёт вне /api/v1 и без авторизации
metrics_router = metrics.router
