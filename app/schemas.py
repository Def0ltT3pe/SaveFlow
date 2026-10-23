"""Pydantic-схемы API. Деньги в API — в EUR, в хранилище — в евроцентах."""
from enum import StrEnum

from pydantic import BaseModel, Field


class RiskLevel(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"  # «Нет оценки»: недостаточно данных


# --- auth ---

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    username: str


# --- customers ---

class Reason(BaseModel):
    code: str
    label: str
    contribution: float | None = None


class CustomerOut(BaseModel):
    id: str
    email: str
    balance: float
    total_replenished: float
    risk_score: float | None
    risk_level: RiskLevel
    evaluated: bool
    reasons: list[Reason]
    money_at_risk: float
    expected_loss: float


class CustomerList(BaseModel):
    total: int
    items: list[CustomerOut]


class ServiceOut(BaseModel):
    id: str
    name: str | None
    price: float
    status: str
    autoprolong: bool
    payment_term: str
    days_to_expiry: int | None


class CustomerDetail(CustomerOut):
    services: list[ServiceOut]
    explanation: str


# --- analytics ---

class Summary(BaseModel):
    customers_by_level: dict[RiskLevel, int]
    money_at_risk: float
    expected_loss: float
    data_version: int
    model_version: int


# --- model settings ---

class Thresholds(BaseModel):
    high: float = 60
    medium: float = 30


class ModelSettings(BaseModel):
    version: int
    weights: dict[str, float]
    thresholds: Thresholds


class ModelSettingsPatch(BaseModel):
    weights: dict[str, float] | None = None
    thresholds: Thresholds | None = None


# --- source snapshot ---

class Snapshot(BaseModel):
    # TODO: строгие модели после разбора тестового датасета
    customers: list[dict] = Field(default_factory=list)
    services: list[dict] = Field(default_factory=list)
    transactions: list[dict] = Field(default_factory=list)


class SnapshotResult(BaseModel):
    customers: int
    services: int
    transactions: int
    data_version: int
