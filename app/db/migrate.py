"""Миграции при старте бэкенда: после git pull достаточно перезапустить сервер."""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db.session import get_engine, masked_url, setting

ROOT = Path(__file__).resolve().parents[2]
# Отдельный ключ от пересчёта: два бэкенда, стартующих одновременно, мигрируют базу по очереди.
MIGRATION_LOCK_KEY = 727_002

log = logging.getLogger("uvicorn.error")


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def auto_migrate_enabled() -> bool:
    return setting("AUTO_MIGRATE", "1")[0].strip().lower() not in ("0", "false", "no", "off")


def upgrade_to_head() -> bool:
    """Накатить миграции до последней. False, если база недоступна или миграция упала: сервер всё равно стартует."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
            try:
                command.upgrade(alembic_config(), "head")
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
    except OperationalError as exc:
        log.warning("Миграции не накатаны: база %s недоступна (%s). Запросы к данным получат 503.",
                    masked_url(), str(exc.orig or exc).strip().splitlines()[0])
        return False
    except Exception:
        log.exception("Миграции упали. Проверьте базу: python -m app.risk.cli dbcheck")
        return False
    log.info("Миграции базы %s накатаны до последней версии", masked_url())
    return True
