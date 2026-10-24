import time

import pytest

from app.db import session as db
from app.risk import cli


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(db, "ENV_FILE", path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.get_engine.cache_clear()
    yield path
    db.get_engine.cache_clear()


def test_url_comes_from_env_file(env_file):
    env_file.write_text('# комментарий\nexport DATABASE_URL="postgresql+psycopg://u:p@db:5432/x"\n', encoding="utf-8")
    assert db.database_url() == "postgresql+psycopg://u:p@db:5432/x"
    assert db.setting("DATABASE_URL", "")[1] == str(env_file)


def test_environment_variable_wins_over_env_file(env_file, monkeypatch):
    env_file.write_text("DATABASE_URL=postgresql+psycopg://file/x\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://env/x")
    assert db.database_url() == "postgresql+psycopg://env/x"


def test_default_without_env_file(env_file):
    assert db.database_url() == db.DEFAULT_DATABASE_URL
    assert db.setting("DATABASE_URL", "x")[1] == "значение по умолчанию"


@pytest.mark.parametrize("raw", ["postgresql://u:p@h/x", "postgres://u:p@h/x"])
def test_url_without_driver_uses_psycopg3(env_file, monkeypatch, raw):
    monkeypatch.setenv("DATABASE_URL", raw)
    assert db.database_url() == "postgresql+psycopg://u:p@h/x"


def test_password_is_masked():
    assert db.masked_url("postgresql+psycopg://churn:s3cr3t@localhost:5432/churn") == \
        "postgresql+psycopg://churn:***@localhost:5432/churn"
    assert "s3cr3t" not in db.masked_url("postgresql+psycopg://u:s3cr3t@h/x")


def test_unreachable_database_fails_fast(env_file, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5999/x")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "2")
    started = time.perf_counter()
    with pytest.raises(Exception):
        db.get_engine().connect()
    assert time.perf_counter() - started < 10  # без таймаута было ~130 с


def test_dbcheck_reports_unreachable_database(env_file, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:secret@localhost:5999/x")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "2")
    assert cli.main(["dbcheck"]) == 1
    out = capsys.readouterr().out
    assert "ОШИБКА" in out and "secret" not in out and "localhost:5999" in out


def test_dbcheck_ok_on_migrated_database(engine, loaded, capsys):
    db.get_engine.cache_clear()
    try:
        assert cli.main(["dbcheck"]) == 0
    finally:
        db.get_engine.cache_clear()
    out = capsys.readouterr().out
    assert "Всё в порядке" in out and "Клиентов:    10" in out


@pytest.mark.parametrize("value", ["5s", "abc", "0", "-3"])
def test_bad_pool_setting_falls_back_to_default(env_file, monkeypatch, caplog, value):
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", value)
    assert db._int("DB_CONNECT_TIMEOUT", 5) == 5
    assert "DB_CONNECT_TIMEOUT" in caplog.text
