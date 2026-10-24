import logging
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "postgresql+psycopg://churn:churn@localhost:5432/churn"
# Адрес БД и параметры пула задаются в .env в корне проекта (образец — .env.example).
# Переменная окружения с тем же именем важнее файла.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _env_file() -> dict[str, str]:
    try:
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    values = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def setting(name: str, default: str) -> tuple[str, str]:
    """Значение и откуда оно взято: переменная окружения, .env или значение по умолчанию."""
    if os.environ.get(name):
        return os.environ[name], "переменная окружения"
    if value := _env_file().get(name):
        return value, str(ENV_FILE)
    return default, "значение по умолчанию"


def _normalize(url: str) -> str:
    # postgresql:// без драйвера SQLAlchemy открывает через psycopg2, которого нет в зависимостях:
    # тогда падает каждый запрос. Ставим psycopg 3 явно.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def database_url() -> str:
    return _normalize(setting("DATABASE_URL", DEFAULT_DATABASE_URL)[0])


def masked_url(url: str | None = None) -> str:
    """Адрес БД без пароля — для логов и диагностики."""
    parts = urlsplit(url or database_url())
    if parts.password is None:
        return urlunsplit(parts)
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return urlunsplit(parts._replace(netloc=netloc))


def _int(name: str, default: int) -> int:
    value, source = setting(name, str(default))
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except ValueError:
        pass
    # Опечатка вроде DB_CONNECT_TIMEOUT=5s не должна ронять каждый запрос: берём значение по умолчанию.
    logging.getLogger(__name__).warning("%s=%r (%s) — не положительное целое, использую %s", name, value, source, default)
    return default


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        database_url(),
        pool_pre_ping=True,
        pool_size=_int("DB_POOL_SIZE", 10),
        max_overflow=_int("DB_MAX_OVERFLOW", 20),
        # Без явного таймаута недоступная БД держит запрос ~130 с: пул забивается, клиенты получают 500/504.
        pool_timeout=_int("DB_POOL_TIMEOUT", 15),
        connect_args={"connect_timeout": _int("DB_CONNECT_TIMEOUT", 5)},
    )


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
