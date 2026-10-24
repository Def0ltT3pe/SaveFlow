"""Аутентификация.

Login выдаёт случайный токен; сессии хранятся в Postgres (app/db/auth_sessions.py), поэтому вход
переживает перезапуск бэкенда и работает с несколькими воркерами. В БД лежит только хеш токена.
TODO: JWT (HS256) + token_version пользователя, пароли через bcrypt/argon2.
"""
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import auth_sessions

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def issue_token(username: str, id_token: str | None = None) -> str:
    return auth_sessions.issue(username, id_token=id_token)


def revoke_token(token: str) -> str | None:
    """Завершает сессию; возвращает id_token провайдера, если вход был через OIDC."""
    return auth_sessions.revoke(token)


def get_current_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if creds is None or auth_sessions.username_for(creds.credentials) is None:
        raise _unauthorized()
    return creds.credentials


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    username = auth_sessions.username_for(creds.credentials) if creds is not None else None
    if username is None:
        raise _unauthorized()
    return username


def get_user_from_query(token: str = Query(..., description="Токен из /auth/login")) -> str:
    """Для SSE: EventSource не умеет передавать заголовок Authorization."""
    username = auth_sessions.username_for(token)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return username
