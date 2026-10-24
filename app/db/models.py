from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Money = Numeric()


class Base(DeclarativeBase):
    pass


# --- Источник ---------------------------------------------------------------


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(Text)
    balance: Mapped[int] = mapped_column(BigInteger)
    withdraw_balance: Mapped[int] = mapped_column("withdrawBalance", BigInteger)
    total_replenished: Mapped[int] = mapped_column("totalReplenished", BigInteger)
    bonus_balance: Mapped[int] = mapped_column("bonusBalance", BigInteger)
    bonus_state: Mapped[str] = mapped_column("bonusState", Text)


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[int] = mapped_column("ownerId", BigInteger, ForeignKey("accounts.id"))
    payment_term: Mapped[str] = mapped_column("paymentTerm", Text)
    auto_prolong: Mapped[bool] = mapped_column("autoProlong", Boolean)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    price: Mapped[int] = mapped_column(BigInteger)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    performed_at: Mapped[datetime | None] = mapped_column("performedAt", DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True))
    type: Mapped[str] = mapped_column(Text)
    service_id: Mapped[int] = mapped_column("serviceId", BigInteger, ForeignKey("services.id"))


# --- Модель -----------------------------------------------------------------


class ModelSettingsRow(Base):
    __tablename__ = "model_settings"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime(timezone=True))


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    loaded_at: Mapped[datetime] = mapped_column("loadedAt", DateTime(timezone=True))
    reference_date: Mapped[datetime] = mapped_column("referenceDate", DateTime(timezone=True))
    accounts: Mapped[int] = mapped_column(Integer)
    services: Mapped[int] = mapped_column(Integer)
    transactions: Mapped[int] = mapped_column(Integer)


class ServiceStatusChange(Base):
    __tablename__ = "service_status_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column("snapshotId", BigInteger, ForeignKey("snapshots.id"))
    service_id: Mapped[int] = mapped_column("serviceId", BigInteger)
    owner_id: Mapped[int] = mapped_column("ownerId", BigInteger)
    old_status: Mapped[str | None] = mapped_column("oldStatus", Text)
    new_status: Mapped[str | None] = mapped_column("newStatus", Text)
    price: Mapped[int] = mapped_column(BigInteger)
    payment_term: Mapped[str] = mapped_column("paymentTerm", Text)
    changed_at: Mapped[datetime] = mapped_column("changedAt", DateTime(timezone=True))


class RiskRun(Base):
    __tablename__ = "risk_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    computed_at: Mapped[datetime] = mapped_column("computedAt", DateTime(timezone=True))
    as_of: Mapped[datetime] = mapped_column("asOf", DateTime(timezone=True))
    settings_version: Mapped[int] = mapped_column("settingsVersion", Integer)
    settings: Mapped[dict | None] = mapped_column(JSONB)
    snapshot_id: Mapped[int | None] = mapped_column("snapshotId", BigInteger, ForeignKey("snapshots.id"))
    reason: Mapped[str] = mapped_column(Text)


class ServiceRisk(Base):
    __tablename__ = "service_risk"

    service_id: Mapped[int] = mapped_column("serviceId", BigInteger, ForeignKey("services.id"), primary_key=True)
    account_id: Mapped[int] = mapped_column("accountId", BigInteger, ForeignKey("accounts.id"))
    scored: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    contributions: Mapped[list] = mapped_column(JSONB)
    days_to_expiry: Mapped[Decimal] = mapped_column("daysToExpiry", Numeric(10, 3))
    renewals_in_horizon: Mapped[int] = mapped_column("renewalsInHorizon", Integer)
    monthly_price: Mapped[Decimal] = mapped_column("monthlyPrice", Money)
    horizon_revenue: Mapped[Decimal] = mapped_column("horizonRevenue", Money)
    secured_by_balance: Mapped[Decimal] = mapped_column("securedByBalance", Money)
    at_risk: Mapped[Decimal] = mapped_column("atRisk", Money)
    expected_loss: Mapped[Decimal] = mapped_column("expectedLoss", Money)
    expected_monthly_loss: Mapped[Decimal] = mapped_column("expectedMonthlyLoss", Money)


class AccountRisk(Base):
    __tablename__ = "account_risk"

    account_id: Mapped[int] = mapped_column("accountId", BigInteger, ForeignKey("accounts.id"), primary_key=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    level: Mapped[str] = mapped_column(Text)
    reasons: Mapped[list] = mapped_column(JSONB)
    live_services: Mapped[int] = mapped_column("liveServices", Integer)
    monthly_revenue: Mapped[Decimal] = mapped_column("monthlyRevenue", Money)
    horizon_revenue: Mapped[Decimal] = mapped_column("horizonRevenue", Money)
    secured_by_balance: Mapped[Decimal] = mapped_column("securedByBalance", Money)
    at_risk: Mapped[Decimal] = mapped_column("atRisk", Money)
    expected_loss: Mapped[Decimal] = mapped_column("expectedLoss", Money)
    expected_monthly_loss: Mapped[Decimal] = mapped_column("expectedMonthlyLoss", Money)
