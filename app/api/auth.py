import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.security import get_current_token, get_current_user, issue_token, revoke_token
from app.schemas import LoginRequest, TokenResponse, UserOut

public_router = APIRouter(prefix="/auth", tags=["auth"])
router = APIRouter(prefix="/auth", tags=["auth"])


@public_router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest) -> TokenResponse:
    """Вход по логину и паролю."""
    ok_user = secrets.compare_digest(body.username.encode(), settings.demo_user.encode())
    ok_password = secrets.compare_digest(body.password.encode(), settings.demo_password.encode())
    if not (ok_user and ok_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    return TokenResponse(access_token=issue_token(body.username))


@router.get("/me", response_model=UserOut)
def me(username: str = Depends(get_current_user)) -> UserOut:
    """Получение текущего пользователя."""
    return UserOut(username=username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: str = Depends(get_current_token)) -> Response:
    """Завершение текущей сессии."""
    revoke_token(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
