import importlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import auth_sessions
from app.db import session as dbs
from app.db.models import AuthSession


@pytest.fixture
def api(engine):
    import logging

    from fastapi.testclient import TestClient

    from main import app

    logging.disable(logging.CRITICAL)
    dbs.get_engine.cache_clear()  # те же настройки подключения, что у тестовой базы
    yield TestClient(app, raise_server_exceptions=False)
    logging.disable(logging.NOTSET)
    dbs.get_engine.cache_clear()


def login(api) -> str:
    r = api.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    return r.json()["access_token"]


def me(api, token):
    return api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code


def test_login_survives_backend_restart(api):
    token = login(api)
    assert me(api, token) == 200
    import app.core.security as security

    importlib.reload(security)  # как перезапуск процесса: всё, что было в памяти модуля, обнулилось
    assert me(api, token) == 200


def test_logout_revokes_token(api):
    token = login(api)
    assert api.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert me(api, token) == 401
    other = login(api)  # другие сессии живут
    assert me(api, other) == 200


@pytest.mark.parametrize("token", ["", "x" * 1000, "not-a-real-token"])
def test_bad_tokens_are_rejected(api, token):
    assert me(api, token) == 401
    assert api.get("/api/v1/events/stream", params={"token": token}).status_code == 401


def test_non_ascii_token_in_sse_query_is_rejected(api):
    # В заголовке такой токен не отправить, а в параметре URL для SSE — можно
    assert api.get("/api/v1/events/stream", params={"token": "токен"}).status_code == 401


def test_only_token_hash_is_stored(api, session):
    token = login(api)
    rows = session.scalars(select(AuthSession.token_hash)).all()
    assert token.encode() not in rows and auth_sessions._hash(token) in rows


def test_session_expires(api):
    now = datetime.now(timezone.utc)
    token = auth_sessions.issue("admin", now=now - auth_sessions.session_ttl() - timedelta(minutes=1))
    assert auth_sessions.username_for(token) is None
    assert me(api, token) == 401


def test_session_ttl_setting(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_HOURS", "2")
    assert auth_sessions.session_ttl() == timedelta(hours=2)
    monkeypatch.setenv("SESSION_TTL_HOURS", "abc")
    assert auth_sessions.session_ttl() == timedelta(hours=12)


def test_snapshot_upload_keeps_sessions(api, source_data):
    token = login(api)
    r = api.put("/api/v1/source/snapshot", json=source_data, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and me(api, token) == 200
