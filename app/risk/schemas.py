from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.risk.registry import all_features, default_weights

BIGINT_MAX = 2**63 - 1
# Без пробелов, управляющих символов (в т.ч. \x00, который PostgreSQL не хранит в text) и <>"`,
# чтобы адрес не мог превратиться в разметку в панели. Апостроф разрешён: o'neil@… — реальный адрес.
EMAIL_PATTERN = "^[^@\\s<>\"`\\x00-\\x1f\\x7f]+@[^@\\s<>\"`\\x00-\\x1f\\x7f]+$"

Id = Annotated[int, Field(ge=1, le=BIGINT_MAX)]
Cents = Annotated[int, Field(ge=0, le=BIGINT_MAX)]
PaymentTerm = Literal["hour", "week", "month", "quarter_year", "half_year", "year"]
ServiceStatus = Literal["active", "blocked", "deleted", "suspended", "prolong_wait"]
RiskLevel = Literal["high", "medium", "low", "none"]
SortField = Literal["id", "email", "balance", "totalReplenished", "score", "expectedLoss"]
SortOrder = Literal["asc", "desc"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- Вход: снимок источника (PUT /api/v1/source/snapshot) -------------------


class AccountIn(CamelModel):
    id: Id
    email: str = Field(min_length=3, max_length=320, pattern=EMAIL_PATTERN)
    balance: Cents = 0
    withdraw_balance: Cents = 0
    total_replenished: Cents = 0
    bonus_balance: Cents = 0
    bonus_state: Literal["locked", "unlocked"] = "locked"


class ServiceIn(CamelModel):
    id: Id
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9-]+$")
    owner_id: Id
    payment_term: PaymentTerm
    auto_prolong: bool = False
    created_at: AwareDatetime
    expires_at: AwareDatetime
    status: ServiceStatus
    price: Cents

    @model_validator(mode="after")
    def _dates(self):
        if self.expires_at < self.created_at:
            raise ValueError("expiresAt раньше createdAt")
        return self


class TransactionIn(CamelModel):
    id: Id
    amount: int = Field(ge=0, le=1000)
    status: Literal["not_performed", "performed"] = "not_performed"
    performed_at: AwareDatetime | None = None
    created_at: AwareDatetime
    type: Literal["prolong", "order"]
    service_id: Id

    @model_validator(mode="after")
    def _performed(self):
        if self.status == "not_performed" and self.performed_at is not None:
            raise ValueError("у not_performed не может быть performedAt")
        if self.status == "performed":
            if self.performed_at is None:
                raise ValueError("у performed обязателен performedAt")
            if self.performed_at < self.created_at:
                raise ValueError("performedAt раньше createdAt")
        return self


class SnapshotIn(CamelModel):
    """Полный снимок источника в формате data.json.

    referenceDate — необязательное расширение: момент, на который снят снимок.
    Если его нет, берётся самая поздняя дата из самих данных.
    """

    reference_date: AwareDatetime | None = None
    accounts: list[AccountIn]
    services: list[ServiceIn] = []
    transactions: list[TransactionIn] = []

    @model_validator(mode="after")
    def _integrity(self):
        problems: list[str] = []

        def duplicates(values) -> list:
            seen, dup = set(), []
            for v in values:
                if v in seen:
                    dup.append(v)
                seen.add(v)
            return dup[:10]

        for name, rows in (("accounts", self.accounts), ("services", self.services), ("transactions", self.transactions)):
            if dup := duplicates(r.id for r in rows):
                problems.append(f"{name}: повторяющиеся id {dup}")
        if dup := duplicates(a.email for a in self.accounts):
            problems.append(f"accounts: повторяющиеся email {dup}")
        account_ids = {a.id for a in self.accounts}
        if orphans := sorted({s.owner_id for s in self.services} - account_ids)[:10]:
            problems.append(f"services: ownerId без аккаунта {orphans}")
        service_ids = {s.id for s in self.services}
        if orphans := sorted({t.service_id for t in self.transactions} - service_ids)[:10]:
            problems.append(f"transactions: serviceId без услуги {orphans}")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def latest_timestamp(self) -> datetime | None:
        stamps = [s.created_at for s in self.services]
        stamps += [t.created_at for t in self.transactions]
        stamps += [t.performed_at for t in self.transactions if t.performed_at]
        return max(stamps, default=None)


# --- Настройки модели --------------------------------------------------------


class Thresholds(CamelModel):
    """Пороги уровней риска по score (0..100): score >= high → высокий, >= medium → средний."""

    high: float = Field(60, ge=0, le=100)
    medium: float = Field(30, ge=0, le=100)

    @model_validator(mode="after")
    def _order(self):
        if self.medium >= self.high:
            raise ValueError("порог medium должен быть меньше high")
        return self


class ModelSettings(CamelModel):
    weights: dict[str, float] = Field(default_factory=default_weights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    horizon_days: int = Field(30, ge=1, le=365, description="Горизонт прогноза в днях")
    prolong_wait_grace_days: int = Field(5, ge=0, le=60, description="Сколько дней prolong_wait до удаления")
    new_service_days: int = Field(30, ge=1, le=365, description="Сколько дней услуга считается новой")
    aggregation: Literal["max", "revenue_weighted"] = Field(
        "max", description="Как score услуг сводится в score клиента"
    )
    as_of_mode: Literal["snapshot", "now", "fixed"] = Field(
        "snapshot", description="Опорная дата: дата снимка, текущее время или asOfFixed"
    )
    as_of_fixed: AwareDatetime | None = None

    @field_validator("weights")
    @classmethod
    def _weights(cls, value: dict[str, float]) -> dict[str, float]:
        known = all_features()
        if unknown := sorted(set(value) - set(known)):
            raise ValueError(f"неизвестные признаки: {unknown}; доступны: {sorted(known)}")
        for key, weight in value.items():
            if not 0 <= weight <= 100:
                raise ValueError(f"вес {key} должен быть от 0 до 100")
        return {**default_weights(), **value}

    @model_validator(mode="after")
    def _as_of(self):
        if self.as_of_mode == "fixed" and self.as_of_fixed is None:
            raise ValueError("для asOfMode=fixed нужна asOfFixed")
        return self


class ThresholdsPatch(CamelModel):
    high: float | None = None
    medium: float | None = None


class ModelSettingsPatch(CamelModel):
    """Тело PATCH /api/v1/model/settings: только изменяемые поля, weights можно передать частично."""

    model_config = ConfigDict(extra="forbid")

    weights: dict[str, float] | None = None
    thresholds: ThresholdsPatch | None = None
    horizon_days: int | None = None
    prolong_wait_grace_days: int | None = None
    new_service_days: int | None = None
    aggregation: Literal["max", "revenue_weighted"] | None = None
    as_of_mode: Literal["snapshot", "now", "fixed"] | None = None
    as_of_fixed: AwareDatetime | None = None

    def apply_to(self, current: ModelSettings) -> ModelSettings:
        data = current.model_dump()
        patch = self.model_dump(exclude_unset=True)
        data["weights"].update(patch.pop("weights", None) or {})
        thresholds = patch.pop("thresholds", None) or {}
        data["thresholds"].update({k: v for k, v in thresholds.items() if v is not None})
        for key, value in patch.items():
            if value is not None or key == "as_of_fixed":
                data[key] = value
        return ModelSettings.model_validate(data)


class FeatureInfo(CamelModel):
    key: str
    title: str
    description: str
    default_weight: float


class ModelSettingsOut(CamelModel):
    settings: ModelSettings
    version: int
    updated_at: datetime
    features: list[FeatureInfo]


# --- Выход: результаты оценки ------------------------------------------------


class Reason(CamelModel):
    feature: str
    title: str
    text: str
    points: float | None = None
    service_id: int | None = None
    service_name: str | None = None
    services: int = Field(1, description="Сколько услуг клиента дают этот сигнал")


class Contribution(CamelModel):
    feature: str
    title: str
    value: float = Field(description="Значение признака 0..1")
    weight: float
    points: float = Field(description="Вклад в score = weight × value")
    text: str | None


class Money(CamelModel):
    """Денежные показатели в евроцентах."""

    monthly_revenue: float = Field(description="Выручка в месяц с действующих услуг")
    horizon_revenue: float = Field(description="Сколько клиент должен заплатить за продления в горизонте")
    secured_by_balance: float = Field(description="Часть horizon_revenue, которую автопродление спишет с баланса")
    at_risk: float = Field(description="horizon_revenue − secured_by_balance")
    expected_loss: float = Field(description="Σ вероятность ухода × at_risk")
    expected_monthly_loss: float = Field(description="Σ вероятность ухода × месячная цена")


class TxStatsOut(CamelModel):
    total: int
    performed: int
    not_performed: int
    last_performed_at: datetime | None


class ServiceRiskOut(CamelModel):
    id: int
    name: str
    status: ServiceStatus
    payment_term: PaymentTerm
    auto_prolong: bool
    price: int
    created_at: datetime
    expires_at: datetime
    scored: bool = Field(description="false для deleted/blocked: они уже потеряны и не оцениваются")
    score: float | None
    probability: float | None
    days_to_expiry: float
    renewals_in_horizon: int
    monthly_price: float
    horizon_revenue: float
    secured_by_balance: float
    at_risk: float
    expected_loss: float
    expected_monthly_loss: float
    contributions: list[Contribution]
    transactions: TxStatsOut


class CustomerListItem(CamelModel):
    id: int
    email: str
    balance: int
    withdraw_balance: int
    bonus_balance: int
    bonus_state: str
    total_replenished: int
    score: float | None = Field(description="0..100, null — оценки нет")
    loyalty: float | None = Field(description="100 − score")
    level: RiskLevel
    main_reason: str | None
    reasons: list[Reason]
    live_services: int
    money: Money


class CustomerPage(CamelModel):
    items: list[CustomerListItem]
    total: int
    limit: int
    offset: int
    sort: SortField
    order: SortOrder


class CustomerDetail(CustomerListItem):
    as_of: datetime | None
    computed_at: datetime | None
    services: list[ServiceRiskOut]


class LevelCounts(CamelModel):
    high: int = 0
    medium: int = 0
    low: int = 0
    none: int = 0


class RiskRunOut(CamelModel):
    id: int
    computed_at: datetime
    as_of: datetime
    settings_version: int
    snapshot_id: int | None
    reason: str


class RiskSummary(CamelModel):
    run: RiskRunOut | None
    horizon_days: int
    accounts_total: int
    accounts_by_level: LevelCounts
    average_score: float | None
    services_total: int
    services_live: int
    services_at_risk: int = Field(description="Действующие услуги со score не ниже порога medium")
    money: Money
    high_risk_at_risk: float = Field(description="at_risk клиентов с высоким риском")
    high_risk_balance: float = Field(description="Сумма balance клиентов с высоким риском")
    high_risk_withdrawable: float = Field(description="Сумма withdrawBalance клиентов с высоким риском")
    explanation: str


class SnapshotResult(CamelModel):
    snapshot_id: int
    reference_date: datetime
    accounts: int
    services: int
    transactions: int
    status_changes: int
    run: RiskRunOut


class TimelinePoint(CamelModel):
    date: str
    kind: Literal["fact", "forecast"]
    deletions: int = Field(0, description="Факт: услуги, перешедшие в deleted между снимками")
    estimated_deletions: int = Field(0, description="Факт (оценка даты): удалённые услуги без истории, дата = expiresAt + льготный период")
    due_services: int = Field(0, description="Прогноз: услуги с ближайшим продлением в этот день")
    expected_non_renewals: float = Field(0, description="Прогноз: Σ вероятностей непродления")


class ChurnTimeline(CamelModel):
    as_of: datetime | None
    horizon_days: int
    points: list[TimelinePoint]
