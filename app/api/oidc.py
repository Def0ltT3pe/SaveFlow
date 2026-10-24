import secrets
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.core import oidc
from app.core.security import issue_token
from app.db.auth_sessions import LOGIN_REQUEST_TTL
from app.schemas import OidcInfo

router = APIRouter(prefix="/auth/oidc", tags=["auth"])

STATE_COOKIE = "saveflow_oidc_state"
COOKIE_PATH = "/api/v1/auth/oidc"
PANEL_LOGIN = "/login"


def _require() -> oidc.OidcConfig:
    cfg = oidc.config()
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вход через OIDC не настроен (OIDC_ISSUER)")
    return cfg


def _to_panel(**fragment: str) -> RedirectResponse:
    # Токен и ошибка — во фрагменте после #: браузер не отправляет его на сервер, в логи он не попадёт
    response = RedirectResponse(f"{PANEL_LOGIN}#{urlencode(fragment)}", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
    return response


@router.get("/config", response_model=OidcInfo)
def oidc_config() -> OidcInfo:
    """Включён ли вход через внешнего провайдера (OIDC) и как подписать кнопку."""
    cfg = oidc.config()
    return OidcInfo(enabled=cfg is not None, name=cfg.name if cfg else None)


@router.get("/login", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse)
def oidc_login(prompt: Literal["login"] | None = Query(
        None, description="login — заново спросить пароль у провайдера, чтобы войти другим пользователем")) -> RedirectResponse:
    """Начало входа через OIDC: переход на страницу входа провайдера (Authorization Code + PKCE)."""
    cfg = _require()
    try:
        url, state = oidc.start_login(cfg, prompt)
    except oidc.OidcError as e:
        return _to_panel(sso_error=str(e))
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    # state в cookie привязывает возврат от провайдера к этому браузеру: чужую ссылку возврата подсунуть нельзя
    response.set_cookie(STATE_COOKIE, state, max_age=int(LOGIN_REQUEST_TTL.total_seconds()),
                        path=COOKIE_PATH, httponly=True, samesite="lax",
                        secure=cfg.redirect_uri.startswith("https://"))
    return response


@router.get("/callback", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse)
def oidc_callback(request: Request, code: str | None = None, state: str | None = None,
                  error: str | None = None, error_description: str | None = None) -> RedirectResponse:
    """Возврат от провайдера: проверка id_token и выдача обычной сессии SaveFlow."""
    cfg = _require()
    cookie = request.cookies.get(STATE_COOKIE) or ""
    try:
        if error:
            raise oidc.OidcError(f"Провайдер не выполнил вход: {error_description or error}")
        if not code or not state or not secrets.compare_digest(cookie.encode(), state.encode()):
            raise oidc.OidcError("Вход начат в другом окне или устарел. Начните вход заново.")
        username = oidc.finish_login(cfg, code, state)
    except oidc.OidcAccessDenied as e:
        # Провайдер помнит этого пользователя и при следующем входе молча вернёт его же: завершаем там его сессию
        url = None
        if cfg.post_logout_redirect_uri:
            try:
                url = oidc.logout_url(cfg, id_token_hint=e.id_token, error=str(e))
            except oidc.OidcError:
                url = None
        if url is None:
            return _to_panel(sso_error=str(e))
        response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
        response.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
        return response
    except oidc.OidcError as e:
        return _to_panel(sso_error=str(e))
    return _to_panel(sso_token=issue_token(username))


@router.get("/logout", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse)
def oidc_logout() -> RedirectResponse:
    """Выход и у провайдера: фронт переходит сюда после POST /auth/logout, если входили через OIDC."""
    cfg = _require()
    try:
        url = oidc.logout_url(cfg)
    except oidc.OidcError:
        url = None
    return RedirectResponse(url or PANEL_LOGIN, status_code=status.HTTP_302_FOUND)
