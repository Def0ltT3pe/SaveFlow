import time
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.db.session import DEFAULT_DATABASE_URL, get_engine, masked_url, setting

ROOT = Path(__file__).resolve().parents[2]


def migrations_head() -> str | None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def check_database() -> dict:
    """Куда смотрит бэкенд, доступна ли БД и накатаны ли миграции. Пароль в отчёт не попадает."""
    _, source = setting("DATABASE_URL", DEFAULT_DATABASE_URL)
    report = {"url": masked_url(), "source": source, "ok": False, "error": None, "connectSeconds": None,
              "revision": None, "head": migrations_head(), "accounts": None}
    started = time.perf_counter()
    try:
        with get_engine().connect() as conn:
            report["connectSeconds"] = round(time.perf_counter() - started, 2)
            if conn.scalar(text("SELECT to_regclass('public.alembic_version')")) is not None:
                report["revision"] = conn.scalar(text("SELECT version_num FROM public.alembic_version"))
            if report["revision"] == report["head"]:
                report["accounts"] = conn.scalar(text("SELECT count(*) FROM public.accounts"))
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {str(exc).strip().splitlines()[0]}"
        return report
    report["ok"] = report["revision"] == report["head"]
    if not report["ok"]:
        report["error"] = "миграции не накатаны: выполните alembic upgrade head"
    return report
