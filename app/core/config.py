"""Настройки приложения из переменных окружения."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # TODO: демо-учётка до появления таблицы app_users
    demo_user: str = os.getenv("DEMO_USER", "admin")
    demo_password: str = os.getenv("DEMO_PASSWORD", "admin")


settings = Settings()
