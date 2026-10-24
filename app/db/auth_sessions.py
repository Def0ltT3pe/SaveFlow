import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select, update

from app.db.models import AuthSession, OidcLoginRequest
from app.db.session import session_factory, setting

TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128
LOGIN_REQUEST_TTL = timedelta(minutes=10)


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


def issue(username: str, *, id_token: str | None = None, now: datetime | None = None) -> str:
    """Новая сессия; возвращает токен для клиента. В БД сохраняется только его хеш.

    id_token — для входа через OIDC: по нему при выходе провайдер завершает свою сессию без лишнего вопроса.
    """
    now = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    with session_factory()() as s:
        # Заодно убираем давно истёкшие сессии, чтобы таблица не росла.
        s.execute(delete(AuthSession).where(AuthSession.expires_at < now - timedelta(days=1)))
        s.execute(insert(AuthSession).values(token_hash=_hash(token), username=username, id_token=id_token,
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


def revoke(token, *, now: datetime | None = None) -> str | None:
    """Выход: сессия перестаёт действовать сразу на всех воркерах. Возвращает её id_token, если входили через OIDC."""
    token_hash = _hash(token)
    if token_hash is None:
        return None
    active = (AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))
    with session_factory()() as s:
        id_token = s.scalar(select(AuthSession.id_token).where(*active).with_for_update())
        # После выхода id_token в базе не нужен
        s.execute(update(AuthSession).where(*active)
                  .values(revoked_at=now or datetime.now(timezone.utc), id_token=None))
        s.commit()
    return id_token


def save_login_request(state: str, nonce: str, code_verifier: str, *, now: datetime | None = None) -> None:
    """Начатый вход через OIDC: по state при возврате от провайдера находятся nonce и code_verifier."""
    now = now or datetime.now(timezone.utc)
    with session_factory()() as s:
        s.execute(delete(OidcLoginRequest).where(OidcLoginRequest.expires_at < now))
        s.execute(insert(OidcLoginRequest).values(state_hash=_hash(state), nonce=nonce, code_verifier=code_verifier,
                                                  created_at=now, expires_at=now + LOGIN_REQUEST_TTL))
        s.commit()


def take_login_request(state, *, now: datetime | None = None) -> tuple[str, str] | None:
    """(nonce, code_verifier) начатого входа; запись удаляется сразу — повторный возврат с тем же state не пройдёт."""
    state_hash = _hash(state)
    if state_hash is None:
        return None
    now = now or datetime.now(timezone.utc)
    with session_factory()() as s:
        row = s.execute(delete(OidcLoginRequest).where(OidcLoginRequest.state_hash == state_hash)
                        .returning(OidcLoginRequest.nonce, OidcLoginRequest.code_verifier,
                                   OidcLoginRequest.expires_at)).first()
        s.commit()
    if row is None or row.expires_at <= now:
        return None
    return row.nonce, row.code_verifier
