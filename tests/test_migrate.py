from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text

import main
from app.db import migrate
from app.db import session as db

OLD_REVISION = "0003_money_unbounded"


def _revision(engine) -> str:
    with engine.connect() as conn:
        return conn.scalar(text("SELECT version_num FROM public.alembic_version"))


def test_startup_upgrades_database_left_behind_after_pull(engine):
    db.get_engine.cache_clear()
    config = migrate.alembic_config()
    command.downgrade(config, OLD_REVISION)
    assert _revision(engine) == OLD_REVISION

    with TestClient(main.app) as client:
        assert client.get("/healthz").status_code == 200

    head = ScriptDirectory.from_config(config).get_current_head()
    assert _revision(engine) == head
    db.get_engine.cache_clear()


def test_startup_without_database_still_serves(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "1")
    db.get_engine.cache_clear()
    try:
        assert migrate.upgrade_to_head() is False
        with TestClient(main.app) as client:
            assert client.get("/healthz").status_code == 200
    finally:
        db.get_engine.cache_clear()


def test_auto_migrate_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("AUTO_MIGRATE", "0")
    called = []
    monkeypatch.setattr(main, "upgrade_to_head", lambda: called.append(True))
    with TestClient(main.app) as client:
        assert client.get("/healthz").status_code == 200
    assert called == []
