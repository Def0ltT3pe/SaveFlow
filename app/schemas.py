"""Схемы API, которых нет в app.risk.schemas: авторизация и версии для живого обновления."""
from pydantic import BaseModel

from app.risk.schemas import CamelModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    username: str


class OidcInfo(BaseModel):
    enabled: bool
    name: str | None = None


class Versions(CamelModel):
    run_id: int | None
    settings_version: int
