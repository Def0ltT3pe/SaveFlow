import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    AccountRisk,
    ModelSettingsRow,
    RiskRun,
    Service,
    ServiceRisk,
    Snapshot,
    Transaction,
)
from app.risk.domain import AccountData, ServiceData, TxStats
from app.risk.registry import all_features
from app.risk.schemas import ModelSettings
from app.risk.scoring import AccountResult

# Ключ advisory-блокировки: загрузка снимка и пересчёты выполняются строго по очереди.
RECALC_LOCK_KEY = 727_001
NOTIFY_CHANNEL = "risk_updated"


def lock(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": RECALC_LOCK_KEY})


def notify(session: Session, payload: dict) -> None:
    """Событие уходит слушателям LISTEN risk_updated только после COMMIT."""
    session.execute(text("SELECT pg_notify(:channel, :payload)"),
                    {"channel": NOTIFY_CHANNEL, "payload": json.dumps(payload, default=str)})


# --- Настройки ---------------------------------------------------------------


def load_settings(session: Session) -> tuple[ModelSettings, int, datetime]:
    stmt = select(ModelSettingsRow).where(ModelSettingsRow.id == 1).execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if row is None:
        session.execute(insert(ModelSettingsRow).values(id=1, data={}))
        row = session.scalar(stmt)
    data = dict(row.data or {})
    # Веса удалённых из кода признаков молча отбрасываются, новые получают значения по умолчанию.
    known = all_features()
    data["weights"] = {k: v for k, v in (data.get("weights") or {}).items() if k in known}
    return ModelSettings.model_validate(data), row.version, row.updated_at


def save_settings(session: Session, settings: ModelSettings) -> tuple[int, datetime]:
    row = session.execute(
        update(ModelSettingsRow)
        .where(ModelSettingsRow.id == 1)
        .values(data=settings.model_dump(mode="json", by_alias=True),
                version=ModelSettingsRow.version + 1, updated_at=func.now())
        .returning(ModelSettingsRow.version, ModelSettingsRow.updated_at)
        .execution_options(synchronize_session=False)
    ).one()
    return row.version, row.updated_at


def resolve_as_of(session: Session, settings: ModelSettings) -> datetime:
    if settings.as_of_mode == "fixed":
        return settings.as_of_fixed
    if settings.as_of_mode == "snapshot":
        latest = session.scalar(select(Snapshot.reference_date).order_by(Snapshot.id.desc()).limit(1))
        if latest is not None:
            return latest
    return datetime.now(timezone.utc)


def latest_snapshot_id(session: Session) -> int | None:
    return session.scalar(select(Snapshot.id).order_by(Snapshot.id.desc()).limit(1))


# --- Исходные данные ---------------------------------------------------------


def load_portfolio(session: Session) -> tuple[list[AccountData], list[ServiceData], dict[int, TxStats]]:
    accounts = [
        AccountData(a.id, a.email, a.balance, a.withdraw_balance, a.total_replenished,
                    a.bonus_balance, a.bonus_state)
        for a in session.scalars(select(Account).order_by(Account.id))
    ]
    services = [
        ServiceData(s.id, s.name, s.owner_id, s.payment_term, s.auto_prolong, s.created_at,
                    s.expires_at, s.status, s.price)
        for s in session.scalars(select(Service).order_by(Service.id))
    ]
    return accounts, services, load_tx_stats(session)


def load_tx_stats(session: Session, service_ids: list[int] | None = None) -> dict[int, TxStats]:
    performed = Transaction.status == "performed"
    stmt = select(
        Transaction.service_id,
        func.count(),
        func.count().filter(performed),
        func.count().filter(~performed),
        func.max(Transaction.performed_at),
    ).group_by(Transaction.service_id)
    if service_ids is not None:
        stmt = stmt.where(Transaction.service_id.in_(service_ids))
    return {sid: TxStats(total, done, not_done, last) for sid, total, done, not_done, last in session.execute(stmt)}


# --- Результаты --------------------------------------------------------------


def write_results(session: Session, results: list[AccountResult]) -> None:
    session.execute(delete(ServiceRisk))
    session.execute(delete(AccountRisk))
    account_rows, service_rows = [], []
    for r in results:
        account_rows.append(dict(
            account_id=r.account.id, score=r.score, level=r.level, reasons=r.reasons,
            live_services=r.live_services, monthly_revenue=r.monthly_revenue,
            horizon_revenue=r.horizon_revenue, secured_by_balance=r.secured_by_balance,
            at_risk=r.at_risk, expected_loss=r.expected_loss,
            expected_monthly_loss=r.expected_monthly_loss,
        ))
        for s in r.services:
            service_rows.append(dict(
                service_id=s.service.id, account_id=r.account.id, scored=s.scored, score=s.score,
                probability=s.probability,
                contributions=[{"feature": c.feature, "title": c.title, "value": c.value,
                                "weight": c.weight, "points": c.points, "text": c.text}
                               for c in s.contributions],
                days_to_expiry=round(s.days_to_expiry, 3), renewals_in_horizon=s.renewals_in_horizon,
                monthly_price=round(s.monthly_price, 2), horizon_revenue=round(s.horizon_revenue, 2),
                secured_by_balance=round(s.secured_by_balance, 2), at_risk=round(s.at_risk, 2),
                expected_loss=round(s.expected_loss, 2),
                expected_monthly_loss=round(s.expected_monthly_loss, 2),
            ))
    if account_rows:
        session.execute(insert(AccountRisk), account_rows)
    if service_rows:
        session.execute(insert(ServiceRisk), service_rows)


def insert_run(session: Session, *, as_of: datetime, settings_version: int,
               snapshot_id: int | None, reason: str) -> RiskRun:
    run_id = session.execute(
        insert(RiskRun)
        .values(as_of=as_of, settings_version=settings_version, snapshot_id=snapshot_id, reason=reason)
        .returning(RiskRun.id)
    ).scalar_one()
    return session.get(RiskRun, run_id)


def latest_run(session: Session) -> RiskRun | None:
    return session.scalar(select(RiskRun).order_by(RiskRun.id.desc()).limit(1))
