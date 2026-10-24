import base64
import hashlib
import io
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, update

from app.core import oidc
from app.db import session as dbs
from app.db.models import AuthSession, OidcLoginRequest

ISSUER = "http://localhost:8180/realms/saveflow"       # как видит браузер, и iss в токенах
INTERNAL = "http://keycloak:8080/realms/saveflow"      # как видит бэкенд из Docker
CLIENT_ID = "saveflow-panel"
REDIRECT_URI = "http://localhost:8080/api/v1/auth/oidc/callback"


class FakeProvider:
    """Провайдер OIDC в памяти: discovery, JWKS, обмен кода на id_token с проверкой PKCE."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.endpoints_base = ISSUER   # Keycloak с KC_HOSTNAME отдаёт в discovery адреса для браузера
        self.codes: dict[str, dict] = {}
        self.requests: list[str] = []
        self.claims_override: dict = {}
        self.sign_with = None
        self.headers_override: dict = {}
        self.down = False
        self.token_error: dict | None = None

    def discovery(self):
        base = self.endpoints_base
        return {"issuer": ISSUER, "authorization_endpoint": f"{base}/protocol/openid-connect/auth",
                "token_endpoint": f"{base}/protocol/openid-connect/token",
                "jwks_uri": f"{base}/protocol/openid-connect/certs",
                "end_session_endpoint": f"{base}/protocol/openid-connect/logout"}

    def jwks(self):
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"},
                         {"kty": "RSA", "use": "enc", "kid": "enc-key", "n": "AQAB", "e": "AQAB"}]}

    def authorize(self, location: str, user: str = "analyst", roles=("analyst",)) -> dict:
        """Пользователь ввёл пароль у провайдера: вернуть параметры возврата на callback."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        code = f"code-{len(self.codes)}"
        self.codes[code] = {"query": q, "user": user, "roles": list(roles)}
        return {"code": code, "state": q["state"]}

    def token(self, form: dict):
        grant = self.codes.pop(form.get("code"), None)
        if self.token_error:
            return 400, self.token_error
        if grant is None or form.get("redirect_uri") != grant["query"]["redirect_uri"]:
            return 400, {"error": "invalid_grant", "error_description": "Code not valid"}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=")
        if challenge.decode() != grant["query"]["code_challenge"]:
            return 400, {"error": "invalid_grant", "error_description": "PKCE verification failed"}
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": f"id-{grant['user']}", "iat": now, "exp": now + 300,
                  "nonce": grant["query"]["nonce"], "preferred_username": grant["user"],
                  "email": f"{grant['user']}@saveflow.test", "roles": grant["roles"], **self.claims_override}
        claims = {k: v for k, v in claims.items() if v is not None}
        key = self.sign_with or self.key
        headers = {"kid": self.kid, **self.headers_override}
        return 200, {"id_token": jwt.encode(claims, key, algorithm=headers.pop("alg", "RS256"), headers=headers),
                     "access_token": "opaque", "token_type": "Bearer"}

    def urlopen(self, request, timeout=None):
        url = request.full_url
        self.requests.append(url)
        if self.down:
            raise URLError("connection refused")
        assert url.startswith(INTERNAL + "/"), f"бэкенд ходит к провайдеру по внутреннему адресу: {url}"
        path = url[len(INTERNAL):]
        if path == "/.well-known/openid-configuration":
            status, body = 200, self.discovery()
        elif path == "/protocol/openid-connect/certs":
            status, body = 200, self.jwks()
        elif path == "/protocol/openid-connect/token":
            form = {k: v[0] for k, v in parse_qs(request.data.decode()).items()}
            status, body = self.token(form)
        else:
            status, body = 404, {"error": "not_found"}
        data = io.BytesIO(json.dumps(body).encode())
        if status != 200:
            raise HTTPError(url, status, "error", {}, data)
        return data


@pytest.fixture
def provider(monkeypatch, tmp_path):
    fake = FakeProvider()
    monkeypatch.setattr(oidc, "urlopen", fake.urlopen)
    # Тесты «выключают» настройку пустой переменной; без этого значение взялось бы из локального .env разработчика
    monkeypatch.setattr(dbs, "ENV_FILE", tmp_path / ".env")
    oidc._cache.clear()
    for name, value in {"OIDC_ISSUER": ISSUER, "OIDC_INTERNAL_ISSUER": INTERNAL, "OIDC_CLIENT_ID": CLIENT_ID,
                        "OIDC_REDIRECT_URI": REDIRECT_URI, "OIDC_REQUIRED_ROLE": "analyst", "OIDC_NAME": "Keycloak",
                        "OIDC_POST_LOGOUT_REDIRECT_URI": "http://localhost:8080/login"}.items():
        monkeypatch.setenv(name, value)
    yield fake
    oidc._cache.clear()


@pytest.fixture
def api(engine):
    import logging

    from fastapi.testclient import TestClient

    from main import app

    logging.disable(logging.CRITICAL)
    dbs.get_engine.cache_clear()
    yield TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    logging.disable(logging.NOTSET)
    dbs.get_engine.cache_clear()


def fragment(response) -> dict:
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert location.path == "/login"
    return {k: v[0] for k, v in parse_qs(location.fragment).items()}


def start(api):
    r = api.get("/api/v1/auth/oidc/login")
    assert r.status_code == 302, r.text
    return r


def sso_login(api, provider, **user) -> dict:
    back = provider.authorize(start(api).headers["location"], **user)
    return fragment(api.get("/api/v1/auth/oidc/callback", params=back))


def me(api, token):
    return api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})


# --- Включение и выключение --------------------------------------------------------------


def test_oidc_is_off_without_issuer(api, monkeypatch):
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    monkeypatch.setattr(dbs, "_env_file", lambda: {})
    assert api.get("/api/v1/auth/oidc/config").json() == {"enabled": False, "name": None}
    for path in ("login", "callback", "logout"):
        assert api.get(f"/api/v1/auth/oidc/{path}").status_code == 404


def test_config_tells_front_to_show_the_button(api, provider):
    assert api.get("/api/v1/auth/oidc/config").json() == {"enabled": True, "name": "Keycloak"}


# --- Успешный вход -------------------------------------------------------------------


@pytest.mark.parametrize("endpoints_base", [ISSUER, INTERNAL])
def test_login_through_provider_gives_a_regular_session(api, provider, endpoints_base):
    provider.endpoints_base = endpoints_base  # discovery с адресами для браузера или с внутренними — работает оба
    r = start(api)
    location = urlsplit(r.headers["location"])
    assert f"{location.scheme}://{location.netloc}" == "http://localhost:8180"   # браузеру — внешний адрес
    q = parse_qs(location.query)
    assert q["response_type"] == ["code"] and q["client_id"] == [CLIENT_ID] and q["redirect_uri"] == [REDIRECT_URI]
    assert "openid" in q["scope"][0].split() and q["code_challenge_method"] == ["S256"] and q["nonce"][0]
    assert "saveflow_oidc_state" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]

    back = provider.authorize(r.headers["location"])
    callback = api.get("/api/v1/auth/oidc/callback", params=back)
    token = fragment(callback)["sso_token"]
    assert me(api, token).json() == {"username": "analyst"}
    assert 'saveflow_oidc_state=""' in callback.headers["set-cookie"]   # cookie входа удалена
    assert all(u.startswith(INTERNAL) for u in provider.requests)       # токен и ключи — по внутреннему адресу


def test_sso_session_works_like_password_session(api, provider):
    token = sso_login(api, provider)["sso_token"]
    assert api.get("/api/v1/customers", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert api.get("/api/v1/events/version", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert api.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert me(api, token).status_code == 401


def test_password_login_still_works_as_fallback(api, provider):
    r = api.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200 and me(api, r.json()["access_token"]).status_code == 200


def test_role_is_not_required_when_not_configured(api, provider, monkeypatch):
    monkeypatch.setenv("OIDC_REQUIRED_ROLE", "")
    assert "sso_token" in sso_login(api, provider, user="guest", roles=())


def test_username_falls_back_to_email(api, provider):
    provider.claims_override = {"preferred_username": None}
    token = sso_login(api, provider)["sso_token"]
    assert me(api, token).json() == {"username": "analyst@saveflow.test"}


def test_provider_key_rotation_is_picked_up(api, provider):
    sso_login(api, provider)                       # ключи закэшированы
    provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider.kid = "key-2"
    assert "sso_token" in sso_login(api, provider)


# --- Отказы: вход не проходит, но и 500 нет ------------------------------------------------


NO_ROLE = "У пользователя «guest» нет роли «analyst» для доступа к панели. Войдите другим пользователем."


def test_user_without_role_is_logged_out_at_provider(api, provider):
    # Иначе провайдер помнит guest и следующий вход молча вернёт его же — сменить пользователя нельзя
    back = provider.authorize(start(api).headers["location"], user="guest", roles=())
    r = api.get("/api/v1/auth/oidc/callback", params=back)
    assert r.status_code == 302 and r.headers["location"].startswith(f"{ISSUER}/protocol/openid-connect/logout?")
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    assert q["client_id"] == CLIENT_ID and q["id_token_hint"].count(".") == 2   # выход без вопроса «точно выйти?»
    to_panel = urlsplit(q["post_logout_redirect_uri"])
    assert (to_panel.netloc, to_panel.path) == ("localhost:8080", "/login")
    assert parse_qs(to_panel.query) == {"sso_error": [NO_ROLE]}
    assert 'saveflow_oidc_state=""' in r.headers["set-cookie"]


def test_user_without_role_without_provider_logout_sees_error(api, provider, monkeypatch):
    monkeypatch.setenv("OIDC_POST_LOGOUT_REDIRECT_URI", "")
    result = sso_login(api, provider, user="guest", roles=())
    assert "sso_token" not in result and result["sso_error"] == NO_ROLE


def test_switch_user_asks_provider_for_password_again(api, provider):
    # после отказа провайдер помнит прежнего пользователя: prompt=login заставляет его снова показать форму
    plain = parse_qs(urlsplit(start(api).headers["location"]).query)
    assert "prompt" not in plain
    again = api.get("/api/v1/auth/oidc/login", params={"prompt": "login"})
    assert parse_qs(urlsplit(again.headers["location"]).query)["prompt"] == ["login"]
    assert "sso_token" in fragment(api.get("/api/v1/auth/oidc/callback", params=provider.authorize(again.headers["location"])))
    assert api.get("/api/v1/auth/oidc/login", params={"prompt": "none&x=1"}).status_code == 422


@pytest.mark.parametrize("claims,headers,message", [
    ({"aud": "other-app"}, {}, "другого приложения"),
    ({"iss": "http://evil.test/realms/saveflow"}, {}, "другим издателем"),
    ({"exp": int(time.time()) - 600, "iat": int(time.time()) - 900}, {}, "Срок действия"),
    ({"nonce": "чужой"}, {}, "nonce"),
    ({"aud": [CLIENT_ID, "other-app"], "azp": "other-app"}, {}, "azp"),
    ({"exp": None}, {}, "проверку"),
    ({}, {"kid": "unknown-key"}, "Ключ подписи"),
    ({}, {"alg": "HS256"}, "неподдерживаемым алгоритмом"),
])
def test_bad_id_token_is_rejected(api, provider, claims, headers, message):
    provider.claims_override = claims
    provider.headers_override = headers
    if headers.get("alg") == "HS256":
        provider.sign_with = "секрет-клиента-как-ключ"
    result = sso_login(api, provider)
    assert "sso_token" not in result and message in result["sso_error"], result


def test_token_signed_by_someone_else_is_rejected(api, provider):
    provider.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    result = sso_login(api, provider)
    assert "sso_token" not in result and "подписи" in result["sso_error"]


def test_callback_works_only_once(api, provider):
    back = provider.authorize(start(api).headers["location"])
    assert "sso_token" in fragment(api.get("/api/v1/auth/oidc/callback", params=back))
    # повтор той же ссылки возврата, даже с той же cookie
    again = fragment(api.get("/api/v1/auth/oidc/callback", params=back,
                             headers={"Cookie": f"saveflow_oidc_state={back['state']}"}))
    assert "sso_token" not in again and "устарел или уже завершён" in again["sso_error"]


def test_callback_from_another_browser_is_rejected(api, provider):
    back = provider.authorize(start(api).headers["location"])
    api.cookies.clear()   # ссылку возврата открыли в браузере, который вход не начинал
    result = fragment(api.get("/api/v1/auth/oidc/callback", params=back))
    assert "sso_token" not in result and "другом окне" in result["sso_error"]


@pytest.mark.parametrize("params", [{}, {"code": "x"}, {"state": "x"}, {"code": "x", "state": "ш" * 500}])
def test_callback_without_proper_parameters(api, provider, params):
    start(api)
    result = fragment(api.get("/api/v1/auth/oidc/callback", params=params))
    assert "sso_token" not in result and result["sso_error"]


def test_expired_login_request_is_rejected(api, provider, session):
    back = provider.authorize(start(api).headers["location"])
    session.execute(update(OidcLoginRequest).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    session.commit()
    result = fragment(api.get("/api/v1/auth/oidc/callback", params=back))
    assert "sso_token" not in result and "устарел" in result["sso_error"]


def test_provider_refusal_is_shown(api, provider):
    start(api)
    result = fragment(api.get("/api/v1/auth/oidc/callback",
                              params={"error": "access_denied", "error_description": "User cancelled"}))
    assert result["sso_error"] == "Провайдер не выполнил вход: User cancelled"


def test_token_endpoint_error_is_shown(api, provider):
    provider.token_error = {"error": "invalid_client", "error_description": "Invalid client credentials"}
    result = sso_login(api, provider)
    assert "sso_token" not in result and "Invalid client credentials" in result["sso_error"]


def test_provider_down_means_message_not_500(api, provider):
    provider.down = True
    result = fragment(api.get("/api/v1/auth/oidc/login"))
    assert "недоступен" in result["sso_error"] and "логину и паролю" in result["sso_error"]


def test_only_state_hash_is_stored(api, provider, session):
    state = parse_qs(urlsplit(start(api).headers["location"]).query)["state"][0]
    hashes = session.scalars(select(OidcLoginRequest.state_hash)).all()
    assert hashlib.sha256(state.encode()).digest() in hashes and state.encode() not in hashes


# --- Выход у провайдера ------------------------------------------------------------------


def test_logout_goes_through_provider_and_back_to_panel(api, provider):
    r = api.get("/api/v1/auth/oidc/logout")
    location = urlsplit(r.headers["location"])
    assert r.status_code == 302 and r.headers["location"].startswith(f"{ISSUER}/protocol/openid-connect/logout")
    assert parse_qs(location.query) == {"client_id": [CLIENT_ID], "post_logout_redirect_uri": ["http://localhost:8080/login"]}


def test_logout_without_provider_returns_to_login(api, provider):
    provider.down = True
    r = api.get("/api/v1/auth/oidc/logout")
    assert r.status_code == 302 and r.headers["location"] == "/login"

RELOGIN = "http://localhost:8080/api/v1/auth/oidc/login"


def sso_logout(api, token):
    return api.post("/api/v1/auth/oidc/logout", headers={"Authorization": f"Bearer {token}"})


def test_panel_logout_skips_provider_question_and_opens_its_login_form(api, provider, session):
    token = sso_login(api, provider)["sso_token"]
    r = sso_logout(api, token)
    assert r.status_code == 200
    location = urlsplit(r.json()["redirect"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/protocol/openid-connect/logout"
    q = {k: v[0] for k, v in parse_qs(location.query).items()}
    # С id_token_hint провайдер не спрашивает «Выйти?»; вернёт на начало входа — пользователь видит его форму
    assert q["client_id"] == CLIENT_ID and q["post_logout_redirect_uri"] == RELOGIN
    assert jwt.decode(q["id_token_hint"], options={"verify_signature": False})["preferred_username"] == "analyst"
    assert me(api, token).status_code == 401
    row = session.execute(select(AuthSession.revoked_at, AuthSession.id_token)
                          .where(AuthSession.token_hash == hashlib.sha256(token.encode()).digest())).one()
    assert row.revoked_at is not None and row.id_token is None   # после выхода id_token в базе не хранится


def test_id_token_is_kept_only_for_oidc_sessions(api, provider, session):
    sso = sso_login(api, provider)["sso_token"]
    password = api.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
    stored = {t: session.scalar(select(AuthSession.id_token)
                                .where(AuthSession.token_hash == hashlib.sha256(t.encode()).digest()))
              for t in (sso, password)}
    assert stored[sso].count(".") == 2 and stored[password] is None


def test_logout_without_id_token_still_goes_through_provider(api, provider):
    token = api.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
    q = parse_qs(urlsplit(sso_logout(api, token).json()["redirect"]).query)
    assert "id_token_hint" not in q and q["post_logout_redirect_uri"] == [RELOGIN]
    assert me(api, token).status_code == 401


def test_logout_hint_dropped_when_provider_changed_keys(api, provider):
    # Иначе провайдер показал бы страницу ошибки вместо выхода
    token = sso_login(api, provider)["sso_token"]
    provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider.kid = "key-2"
    q = parse_qs(urlsplit(sso_logout(api, token).json()["redirect"]).query)
    assert "id_token_hint" not in q and q["post_logout_redirect_uri"] == [RELOGIN]


def test_expired_id_token_is_still_a_logout_hint(provider):
    # id_token живёт минуты, сессия панели — часы: к моменту выхода он почти всегда просрочен
    cfg = oidc.config()
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "id-analyst", "iat": now - 7200, "exp": now - 3600}
    expired = jwt.encode(claims, provider.key, algorithm="RS256", headers={"kid": provider.kid})
    assert oidc.logout_hint(cfg, expired) == expired
    other = jwt.encode({**claims, "aud": "other-app"}, provider.key, algorithm="RS256", headers={"kid": provider.kid})
    assert oidc.logout_hint(cfg, other) is None
    assert oidc.logout_hint(cfg, "not-a-token") is None and oidc.logout_hint(cfg, None) is None


def test_panel_logout_with_provider_down_returns_to_login(api, provider):
    token = sso_login(api, provider)["sso_token"]
    provider.down = True
    oidc._cache.clear()
    r = sso_logout(api, token)
    assert r.status_code == 200 and r.json() == {"redirect": "/login"} and me(api, token).status_code == 401


def test_panel_logout_needs_session(api, provider):
    assert api.post("/api/v1/auth/oidc/logout").status_code == 401
