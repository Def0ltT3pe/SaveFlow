"""Аутентификация.

Заглушка: login выдаёт случайный токен, токены живут в памяти процесса.
TODO: JWT (HS256) + token_version пользователя, пароли через bcrypt/argon2.
"""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)
_tokens: dict[str, str] = {}  # token -> username


def issue_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    _tokens[token] = username
    return token


def revoke_token(token: str) -> None:
    _tokens.pop(token, None)


def get_current_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if creds is None or creds.credentials not in _tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return creds.credentials


def get_current_user(token: str = Depends(get_current_token)) -> str:
    return _tokens[token]
