"""Настройки приложения из переменных окружения."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Учётная запись панели задаётся окружением; пользователи из Keycloak входят через OIDC
    demo_user: str = os.getenv("DEMO_USER", "admin")
    demo_password: str = os.getenv("DEMO_PASSWORD", "admin")


settings = Settings()
