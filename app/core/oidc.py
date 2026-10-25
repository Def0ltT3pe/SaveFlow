import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jwt

from app.db import auth_sessions
from app.db.session import setting

# Только асимметричные подписи: HS* проверялись бы секретом клиента, «none» — вовсе без подписи.
ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"})
HTTP_TIMEOUT_SECONDS = 5
CACHE_SECONDS = 300
CLOCK_SKEW_SECONDS = 30

_cache: dict[str, tuple[float, dict]] = {}


class OidcError(Exception):
    """Вход через провайдера не удался; текст показывается пользователю на странице входа."""


class OidcAccessDenied(OidcError):
    """Провайдер пользователя пустил, а панель — нет. id_token нужен, чтобы завершить его сессию у провайдера:
    иначе следующий вход молча вернёт того же пользователя."""

    def __init__(self, message: str, id_token: str):
        super().__init__(message)
        self.id_token = id_token


@dataclass(frozen=True)
class OidcConfig:
    issuer: str             # iss в токенах и адрес провайдера для браузера
    internal_issuer: str    # тот же провайдер, но адрес для бэкенда (в Docker — по имени контейнера)
    client_id: str
    client_secret: str | None
    redirect_uri: str
    post_logout_redirect_uri: str | None
    scopes: str
    name: str
    required_role: str | None
    roles_claim: str
    username_claim: str


def config() -> OidcConfig | None:
    """Настройки из окружения или .env; None — вход через OIDC выключен (не задан OIDC_ISSUER)."""
    def env(name: str, default: str = "") -> str:
        return setting(name, default)[0].strip()

    issuer = env("OIDC_ISSUER").rstrip("/")
    if not issuer:
        return None
    return OidcConfig(
        issuer=issuer,
        internal_issuer=env("OIDC_INTERNAL_ISSUER").rstrip("/") or issuer,
        client_id=env("OIDC_CLIENT_ID", "saveflow-panel"),
        client_secret=env("OIDC_CLIENT_SECRET") or None,
        redirect_uri=env("OIDC_REDIRECT_URI", "http://localhost:8080/api/v1/auth/oidc/callback"),
        post_logout_redirect_uri=env("OIDC_POST_LOGOUT_REDIRECT_URI") or None,
        scopes=env("OIDC_SCOPES", "openid profile email"),
        name=env("OIDC_NAME", "SSO"),
        required_role=env("OIDC_REQUIRED_ROLE") or None,
        roles_claim=env("OIDC_ROLES_CLAIM", "roles"),
        username_claim=env("OIDC_USERNAME_CLAIM", "preferred_username"),
    )


def _rebase(url: str, old: str, new: str) -> str:
    return new + url[len(old):] if old != new and url.startswith(old + "/") else url


def _browser_url(cfg: OidcConfig, url: str) -> str:
    """Адрес, на который уходит браузер: внутренний адрес провайдера ему недоступен."""
    return _rebase(url, cfg.internal_issuer, cfg.issuer)


def _backend_url(cfg: OidcConfig, url: str) -> str:
    """Адрес для запросов бэкенда: из контейнера localhost провайдера не виден."""
    return _rebase(url, cfg.issuer, cfg.internal_issuer)


def _read_json(response) -> dict:
    try:
        data = json.load(response)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise OidcError("Провайдер входа ответил не JSON")
    return data


def _http(url: str, form: dict | None = None) -> dict:
    body = urlencode(form).encode() if form is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=HTTP_TIMEOUT_SECONDS) as response:
            return _read_json(response)
    except HTTPError as e:
        try:
            detail = _read_json(e)
        except OidcError:
            detail = {}
        reason = detail.get("error_description") or detail.get("error") or f"HTTP {e.code}"
        raise OidcError(f"Провайдер входа отклонил запрос: {reason}") from e
    except (URLError, TimeoutError, OSError) as e:
        raise OidcError("Провайдер входа недоступен. Попробуйте позже или войдите по логину и паролю.") from e


def _cached(url: str, *, refresh: bool = False) -> dict:
    hit = _cache.get(url)
    if hit and not refresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    data = _http(url)
    _cache[url] = (time.monotonic(), data)
    return data


def metadata(cfg: OidcConfig) -> dict:
    meta = _cached(f"{cfg.internal_issuer}/.well-known/openid-configuration")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(meta.get(key), str):
            raise OidcError(f"В настройках провайдера нет {key}")
    return meta


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start_login(cfg: OidcConfig, prompt: str | None = None) -> tuple[str, str]:
    """Адрес страницы входа провайдера и state, который нужно запомнить в cookie браузера.

    prompt="login" — провайдер снова спросит логин и пароль, даже если у него уже есть сессия:
    так можно сменить пользователя после отказа в доступе.
    """
    meta = metadata(cfg)
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    auth_sessions.save_login_request(state, nonce, verifier)
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": cfg.scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if prompt:
        params["prompt"] = prompt
    endpoint = _browser_url(cfg, meta["authorization_endpoint"])
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(params)}", state


def finish_login(cfg: OidcConfig, code: str, state: str) -> tuple[str, str]:
    """Обмен кода на id_token и его проверка; возвращает имя пользователя для сессии SaveFlow и id_token для выхода."""
    request = auth_sessions.take_login_request(state)
    if request is None:
        raise OidcError("Вход устарел или уже завершён. Начните вход заново.")
    nonce, verifier = request
    meta = metadata(cfg)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg.redirect_uri,
            "client_id": cfg.client_id, "code_verifier": verifier}
    if cfg.client_secret:
        form["client_secret"] = cfg.client_secret
    tokens = _http(_backend_url(cfg, meta["token_endpoint"]), form)
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise OidcError("Провайдер не вернул id_token: проверьте, что в OIDC_SCOPES есть openid")
    claims = verify_id_token(cfg, meta, id_token, nonce)
    username = str(claims.get(cfg.username_claim) or claims.get("email") or claims["sub"])
    if cfg.required_role and cfg.required_role not in _roles(claims, cfg.roles_claim):
        raise OidcAccessDenied(f"У пользователя «{username}» нет роли «{cfg.required_role}» для доступа к панели. "
                               f"Войдите другим пользователем.", id_token)
    return username, id_token


def _signing_key(cfg: OidcConfig, meta: dict, kid: str | None, *, fresh: bool = False):
    url = _backend_url(cfg, meta["jwks_uri"])
    # провайдер мог сменить ключи: перечитываем один раз; fresh — сразу текущие, без кэша
    for refresh in (True,) if fresh else (False, True):
        for jwk in _cached(url, refresh=refresh).get("keys", []):
            if not isinstance(jwk, dict) or jwk.get("use", "sig") != "sig" or (kid and jwk.get("kid") != kid):
                continue
            try:
                return jwt.PyJWK(jwk).key
            except (jwt.PyJWTError, KeyError, TypeError, ValueError):
                continue
    raise OidcError("Ключ подписи id_token не найден у провайдера")


def verify_id_token(cfg: OidcConfig, meta: dict, token: str, nonce: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise OidcError("Провайдер вернул повреждённый id_token") from e
    alg = header.get("alg")
    if alg not in ALLOWED_ALGORITHMS:
        raise OidcError(f"id_token подписан неподдерживаемым алгоритмом: {alg}")
    key = _signing_key(cfg, meta, header.get("kid"))
    try:
        claims = jwt.decode(token, key, algorithms=[alg], audience=cfg.client_id, leeway=CLOCK_SKEW_SECONDS,
                            options={"require": ["iss", "sub", "aud", "exp", "iat"], "verify_iss": False})
    except jwt.ExpiredSignatureError as e:
        raise OidcError("Срок действия id_token истёк. Начните вход заново.") from e
    except jwt.InvalidAudienceError as e:
        raise OidcError("id_token выпущен для другого приложения (aud)") from e
    except jwt.InvalidSignatureError as e:
        raise OidcError("id_token не прошёл проверку подписи") from e
    except jwt.PyJWTError as e:
        raise OidcError(f"id_token не прошёл проверку: {e}") from e
    if claims["iss"] != cfg.issuer:
        raise OidcError(f"id_token выпущен другим издателем: {claims['iss']}, ожидался OIDC_ISSUER={cfg.issuer}")
    audience = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    if len(audience) > 1 and claims.get("azp") != cfg.client_id:
        raise OidcError("id_token выпущен для другого приложения (azp)")
    if not secrets.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode()):
        raise OidcError("id_token не относится к этому входу (nonce)")
    return claims


def _roles(claims: dict, path: str) -> set[str]:
    """Роли из claim по пути через точку: roles, realm_access.roles, groups."""
    value = claims
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if isinstance(value, str):
        return {value}
    return {str(v) for v in value} if isinstance(value, list) else set()


def relogin_uri(cfg: OidcConfig) -> str | None:
    """Начало входа через OIDC на этом же бэкенде (рядом с callback). Туда провайдер возвращает после выхода
    из панели: пользователь сразу видит форму входа провайдера, как при входе."""
    base, found, rest = cfg.redirect_uri.rpartition("/callback")
    return f"{base}/login" if found and not rest else cfg.post_logout_redirect_uri


def logout_hint(cfg: OidcConfig, id_token: str | None) -> str | None:
    """id_token сессии, если провайдер его примет как id_token_hint, иначе None (тогда он спросит «Выйти?»).

    Срок не проверяем: id_token живёт минуты, а сессия панели — часы, и провайдер принимает просроченный.
    Подпись проверяем: после перезапуска провайдер мог сменить ключи и на чужой токен показал бы страницу ошибки.
    """
    if not id_token:
        return None
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") not in ALLOWED_ALGORITHMS:
            return None
        key = _signing_key(cfg, metadata(cfg), header.get("kid"), fresh=True)
        claims = jwt.decode(id_token, key, algorithms=[header["alg"]], audience=cfg.client_id,
                            options={"verify_exp": False, "verify_iat": False, "verify_iss": False})
    except (OidcError, jwt.PyJWTError):
        return None
    return id_token if claims.get("iss") == cfg.issuer else None


def logout_url(cfg: OidcConfig, id_token_hint: str | None = None, error: str | None = None,
               back: str | None = None) -> str | None:
    """Выход у провайдера, чтобы следующий вход через него снова спросил пароль; None — провайдер не умеет.

    С id_token_hint провайдер выходит без вопроса «Вы точно хотите выйти?»; error вернётся на панель в ?sso_error=.
    back — куда провайдеру вернуть после выхода; по умолчанию OIDC_POST_LOGOUT_REDIRECT_URI (страница входа панели).
    """
    endpoint = metadata(cfg).get("end_session_endpoint")
    if not isinstance(endpoint, str):
        return None
    params = {"client_id": cfg.client_id}
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    back = back or cfg.post_logout_redirect_uri
    if back:
        if error:
            back += ("&" if "?" in back else "?") + urlencode({"sso_error": error})
        params["post_logout_redirect_uri"] = back
    endpoint = _browser_url(cfg, endpoint)
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(params)}"
