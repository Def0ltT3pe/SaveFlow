import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select, update

from app.db.models import AuthSession
from app.db.session import session_factory, setting

TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128


def session_ttl() -> timedelta:
    """Срок жизни сессии: SESSION_TTL_HOURS из окружения или .env, по умолчанию 12 часов."""
    value = setting("SESSION_TTL_HOURS", "12")[0]
    try:
        hours = float(value)
    except ValueError:
        hours = 12.0
    return timedelta(hours=hours if hours > 0 else 12.0)


def _hash(token) -> bytes | None:
    # Заведомо чужой токен (не строка, слишком длинный, не ASCII) до БД не доходит.
    if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN_LENGTH or not token.isascii():
        return None
    return hashlib.sha256(token.encode("ascii")).digest()


def issue(username: str, *, now: datetime | None = None) -> str:
    """Новая сессия; возвращает токен для клиента. В БД сохраняется только его хеш."""
    now = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    with session_factory()() as s:
        # Заодно убираем давно истёкшие сессии, чтобы таблица не росла.
        s.execute(delete(AuthSession).where(AuthSession.expires_at < now - timedelta(days=1)))
        s.execute(insert(AuthSession).values(token_hash=_hash(token), username=username,
                                             created_at=now, expires_at=now + session_ttl()))
        s.commit()
    return token


def username_for(token, *, now: datetime | None = None) -> str | None:
    """Пользователь по токену или None: нет такой сессии, истекла или отозвана."""
    token_hash = _hash(token)
    if token_hash is None:
        return None
    now = now or datetime.now(timezone.utc)
    with session_factory()() as s:
        return s.scalar(select(AuthSession.username).where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        ))


def revoke(token, *, now: datetime | None = None) -> None:
    """Выход: сессия перестаёт действовать сразу на всех воркерах."""
    token_hash = _hash(token)
    if token_hash is None:
        return
    with session_factory()() as s:
        s.execute(update(AuthSession)
                  .where(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))
                  .values(revoked_at=now or datetime.now(timezone.utc)))
        s.commit()
