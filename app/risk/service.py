from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    AccountRisk,
    Service,
    ServiceRisk,
    ServiceStatusChange,
    Snapshot,
    Transaction,
)
from app.risk import store
from app.risk.fmt import eur
from app.risk.registry import all_features
from app.risk.schemas import (
    ChurnTimeline,
    CustomerDetail,
    CustomerListItem,
    CustomerPage,
    FeatureInfo,
    LevelCounts,
    ModelSettingsOut,
    ModelSettingsPatch,
    Money,
    Reason,
    RiskRunOut,
    RiskSummary,
    ServiceRiskOut,
    SnapshotIn,
    SnapshotResult,
    TimelinePoint,
    TxStatsOut,
)
from app.risk.scoring import score_portfolio

SORT_COLUMNS = {
    "id": Account.id,
    "email": Account.email,
    "balance": Account.balance,
    "totalReplenished": Account.total_replenished,
    "score": AccountRisk.score,
    "expectedLoss": AccountRisk.expected_loss,
}
LEVELS = ("high", "medium", "low", "none")
MAX_PAGE = 500


def _json(model) -> dict:
    return model.model_dump(mode="json", by_alias=True)


def _float(value) -> float:
    return float(value) if value is not None else 0.0


def _run_out(run) -> RiskRunOut | None:
    if run is None:
        return None
    return RiskRunOut(id=run.id, computed_at=run.computed_at, as_of=run.as_of,
                      settings_version=run.settings_version, snapshot_id=run.snapshot_id, reason=run.reason)


# --- Пересчёт ----------------------------------------------------------------


def _recalculate(session: Session, reason: str, snapshot_id: int | None = None) -> RiskRunOut:
    settings, version, _ = store.load_settings(session)
    as_of = store.resolve_as_of(session, settings)
    accounts, services, tx_stats = store.load_portfolio(session)
    store.write_results(session, score_portfolio(accounts, services, tx_stats, settings, as_of))
    run = store.insert_run(session, as_of=as_of, settings_version=version,
                           snapshot_id=snapshot_id or store.latest_snapshot_id(session), reason=reason)
    store.notify(session, {"event": reason, "runId": run.id, "settingsVersion": version,
                           "asOf": as_of.isoformat()})
    return _run_out(run)


def recalculate(session: Session, reason: str = "manual") -> dict:
    """Пересчитать все оценки (например, по таймеру в режиме asOfMode=now)."""
    try:
        store.lock(session)
        run = _recalculate(session, reason)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _json(run)


# --- PUT /api/v1/source/snapshot ---------------------------------------------


def replace_snapshot(session: Session, snapshot: SnapshotIn | dict) -> dict:
    """Полностью заменяет accounts/services/transactions снимком и пересчитывает модель.

    Всё в одной транзакции: при любой ошибке в БД остаются прежние данные.
    Настройки модели снимок не затрагивает. Изменения статусов услуг пишутся в историю.
    """
    snap = snapshot if isinstance(snapshot, SnapshotIn) else SnapshotIn.model_validate(snapshot)
    reference = snap.reference_date or snap.latest_timestamp() or datetime.now(timezone.utc)
    try:
        store.lock(session)
        old = {row.id: row for row in session.execute(
            select(Service.id, Service.status, Service.owner_id, Service.price, Service.payment_term))}

        session.execute(text(
            "TRUNCATE public.account_risk, public.service_risk, "
            "public.transactions, public.services, public.accounts"))
        for model, rows in ((Account, snap.accounts), (Service, snap.services), (Transaction, snap.transactions)):
            if rows:
                session.execute(insert(model), [r.model_dump() for r in rows])
            table = model.__tablename__
            session.execute(text(
                f"SELECT setval(pg_get_serial_sequence('public.{table}', 'id'), "
                f"coalesce((SELECT max(id) FROM public.{table}), 1), "
                f"(SELECT count(*) > 0 FROM public.{table}))"))

        snapshot_id = session.execute(
            insert(Snapshot).values(reference_date=reference, accounts=len(snap.accounts),
                                    services=len(snap.services), transactions=len(snap.transactions))
            .returning(Snapshot.id)
        ).scalar_one()

        new = {s.id: s for s in snap.services}
        changes = []
        for sid in sorted(old.keys() | new.keys()):
            before, after = old.get(sid), new.get(sid)
            old_status = before.status if before else None
            new_status = after.status if after else None
            if old_status != new_status:
                src = after or before
                changes.append(dict(snapshot_id=snapshot_id, service_id=sid,
                                    owner_id=after.owner_id if after else before.owner_id,
                                    old_status=old_status, new_status=new_status, price=src.price,
                                    payment_term=src.payment_term, changed_at=reference))
        if changes:
            session.execute(insert(ServiceStatusChange), changes)

        run = _recalculate(session, "snapshot", snapshot_id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _json(SnapshotResult(
        snapshot_id=snapshot_id, reference_date=reference, accounts=len(snap.accounts),
        services=len(snap.services), transactions=len(snap.transactions),
        status_changes=len(changes), run=run,
    ))


# --- GET/PATCH /api/v1/model/settings ----------------------------------------


def _settings_out(session: Session) -> ModelSettingsOut:
    settings, version, updated_at = store.load_settings(session)
    features = [FeatureInfo(key=f.key, title=f.title, description=f.description, default_weight=f.default_weight)
                for f in all_features().values()]
    return ModelSettingsOut(settings=settings, version=version, updated_at=updated_at, features=features)


def get_settings(session: Session) -> dict:
    return _json(_settings_out(session))


def update_settings(session: Session, patch: ModelSettingsPatch | dict) -> dict:
    """Меняет настройки (частично) и сразу пересчитывает оценки."""
    patch = patch if isinstance(patch, ModelSettingsPatch) else ModelSettingsPatch.model_validate(patch)
    try:
        store.lock(session)
        current, _, _ = store.load_settings(session)
        store.save_settings(session, patch.apply_to(current))
        _recalculate(session, "settings")
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _json(_settings_out(session))


# --- GET /api/v1/customers, /api/v1/customers/{id} ---------------------------


def _money(r: AccountRisk | None) -> Money:
    if r is None:
        return Money(monthly_revenue=0, horizon_revenue=0, secured_by_balance=0, at_risk=0,
                     expected_loss=0, expected_monthly_loss=0)
    return Money(monthly_revenue=_float(r.monthly_revenue), horizon_revenue=_float(r.horizon_revenue),
                 secured_by_balance=_float(r.secured_by_balance), at_risk=_float(r.at_risk),
                 expected_loss=_float(r.expected_loss), expected_monthly_loss=_float(r.expected_monthly_loss))


def _customer_fields(a: Account, r: AccountRisk | None) -> dict:
    score = float(r.score) if r is not None and r.score is not None else None
    reasons = [Reason.model_validate(x) for x in (r.reasons if r is not None else [])]
    return dict(
        id=a.id, email=a.email, balance=a.balance, withdraw_balance=a.withdraw_balance,
        bonus_balance=a.bonus_balance, bonus_state=a.bonus_state, total_replenished=a.total_replenished,
        score=score, loyalty=round(100 - score, 2) if score is not None else None,
        level=r.level if r is not None else "none",
        main_reason=reasons[0].text if reasons else None, reasons=reasons,
        live_services=r.live_services if r is not None else 0, money=_money(r),
    )


def get_customers(
    session: Session,
    *,
    sort: str = "score",
    order: str = "desc",
    levels: list[str] | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Рейтинг клиентов.

    sort: id | email | balance | totalReplenished | score | expectedLoss; order: asc | desc.
    levels: фильтр по уровням (high/medium/low/none), search: подстрока email.
    Клиенты без оценки (score = null) при сортировке по score всегда в конце.
    """
    if sort not in SORT_COLUMNS:
        raise ValueError(f"sort должен быть одним из {sorted(SORT_COLUMNS)}")
    if order not in ("asc", "desc"):
        raise ValueError("order должен быть asc или desc")
    if levels and (bad := set(levels) - set(LEVELS)):
        raise ValueError(f"неизвестные уровни {sorted(bad)}; доступны {list(LEVELS)}")
    limit = min(max(limit, 1), MAX_PAGE)
    offset = max(offset, 0)

    stmt = select(Account, AccountRisk).outerjoin(AccountRisk, AccountRisk.account_id == Account.id)
    if levels:
        stmt = stmt.where(func.coalesce(AccountRisk.level, "none").in_(levels))
    if search:
        pattern = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Account.email.ilike(f"%{pattern}%", escape="\\"))
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    column = SORT_COLUMNS[sort]
    ordering = (column.asc() if order == "asc" else column.desc()).nulls_last()
    rows = session.execute(stmt.order_by(ordering, Account.id.asc()).limit(limit).offset(offset)).all()
    items = [CustomerListItem(**_customer_fields(a, r)) for a, r in rows]
    return _json(CustomerPage(items=items, total=total, limit=limit, offset=offset, sort=sort, order=order))


def get_customer(session: Session, customer_id: int) -> dict | None:
    """Подробная оценка клиента с услугами и вкладами признаков. None, если клиента нет."""
    account = session.get(Account, customer_id)
    if account is None:
        return None
    risk = session.get(AccountRisk, customer_id)
    rows = session.execute(
        select(Service, ServiceRisk).outerjoin(ServiceRisk, ServiceRisk.service_id == Service.id)
        .where(Service.owner_id == customer_id).order_by(Service.id)
    ).all()
    tx = store.load_tx_stats(session, [s.id for s, _ in rows])
    services = []
    for s, sr in rows:
        stats = tx.get(s.id)
        services.append(ServiceRiskOut(
            id=s.id, name=s.name, status=s.status, payment_term=s.payment_term, auto_prolong=s.auto_prolong,
            price=s.price, created_at=s.created_at, expires_at=s.expires_at,
            scored=bool(sr and sr.scored),
            score=float(sr.score) if sr and sr.score is not None else None,
            probability=float(sr.probability) if sr and sr.probability is not None else None,
            days_to_expiry=_float(sr and sr.days_to_expiry),
            renewals_in_horizon=sr.renewals_in_horizon if sr else 0,
            monthly_price=_float(sr and sr.monthly_price), horizon_revenue=_float(sr and sr.horizon_revenue),
            secured_by_balance=_float(sr and sr.secured_by_balance), at_risk=_float(sr and sr.at_risk),
            expected_loss=_float(sr and sr.expected_loss),
            expected_monthly_loss=_float(sr and sr.expected_monthly_loss),
            contributions=sr.contributions if sr else [],
            transactions=TxStatsOut(total=stats.total if stats else 0, performed=stats.performed if stats else 0,
                                    not_performed=stats.not_performed if stats else 0,
                                    last_performed_at=stats.last_performed_at if stats else None),
        ))
    run = store.latest_run(session)
    return _json(CustomerDetail(
        **_customer_fields(account, risk), services=services,
        as_of=run.as_of if run else None, computed_at=run.computed_at if run else None,
    ))


# --- GET /api/v1/analytics/summary и /metrics --------------------------------


def _summary(session: Session) -> RiskSummary:
    settings, _, _ = store.load_settings(session)
    run = store.latest_run(session)
    level = func.coalesce(AccountRisk.level, "none")
    high = level == "high"
    a = session.execute(
        select(
            func.count(Account.id),
            *(func.count().filter(level == name) for name in LEVELS),
            func.avg(AccountRisk.score),
            func.sum(AccountRisk.monthly_revenue), func.sum(AccountRisk.horizon_revenue),
            func.sum(AccountRisk.secured_by_balance), func.sum(AccountRisk.at_risk),
            func.sum(AccountRisk.expected_loss), func.sum(AccountRisk.expected_monthly_loss),
            func.sum(AccountRisk.at_risk).filter(high), func.sum(Account.balance).filter(high),
            func.sum(Account.withdraw_balance).filter(high),
        ).select_from(Account).outerjoin(AccountRisk, AccountRisk.account_id == Account.id)
    ).one()
    (total, n_high, n_medium, n_low, n_none, avg_score, monthly, horizon, secured, at_risk,
     loss, monthly_loss, high_at_risk, high_balance, high_withdraw) = a
    s = session.execute(
        select(func.count(Service.id), func.count().filter(ServiceRisk.scored),
               func.count().filter(ServiceRisk.score >= settings.thresholds.medium))
        .select_from(Service).outerjoin(ServiceRisk, ServiceRisk.service_id == Service.id)
    ).one()

    money = Money(monthly_revenue=_float(monthly), horizon_revenue=_float(horizon),
                  secured_by_balance=_float(secured), at_risk=_float(at_risk),
                  expected_loss=_float(loss), expected_monthly_loss=_float(monthly_loss))
    h = settings.horizon_days
    explanation = (
        f"Ожидаемая потеря за {h} дн.: {eur(money.expected_loss)}. Это сумма по всем действующим услугам: "
        f"вероятность ухода клиента (score / 100) × деньги, которые клиент должен заплатить за продления "
        f"в ближайшие {h} дн. и которые ещё не покрыты его балансом при включённом автопродлении. "
        f"Всего за {h} дн. ожидается {eur(money.horizon_revenue)}, из них {eur(money.secured_by_balance)} "
        f"уже лежит на балансах и спишется автоматически, под риском {eur(money.at_risk)}. "
        f"Ежемесячная выручка {eur(money.monthly_revenue)}, ожидаемая потеря в месяц {eur(money.expected_monthly_loss)}."
    )
    return RiskSummary(
        run=_run_out(run), horizon_days=h, accounts_total=total,
        accounts_by_level=LevelCounts(high=n_high, medium=n_medium, low=n_low, none=n_none),
        average_score=round(float(avg_score), 2) if avg_score is not None else None,
        services_total=s[0], services_live=s[1], services_at_risk=s[2], money=money,
        high_risk_at_risk=_float(high_at_risk), high_risk_balance=_float(high_balance),
        high_risk_withdrawable=_float(high_withdraw), explanation=explanation,
    )


def get_summary(session: Session) -> dict:
    return _json(_summary(session))


def render_metrics(session: Session) -> str:
    """Тело ответа GET /metrics (text/plain; version=0.0.4). Те же числа, что в get_summary."""
    s = _summary(session)
    lines: list[str] = []

    def metric(name: str, help_text: str, samples: list[tuple[str, float]]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.extend(f"{name}{labels} {value}" for labels, value in samples)

    metric("churn_customers", "Customers by churn risk level",
           [(f'{{level="{lvl}"}}', getattr(s.accounts_by_level, lvl)) for lvl in LEVELS])
    metric("churn_average_score", "Average churn risk score of scored customers (0-100)",
           [("", s.average_score or 0)])
    metric("churn_services", "Services by state",
           [('{state="total"}', s.services_total), ('{state="live"}', s.services_live),
            ('{state="at_risk"}', s.services_at_risk)])
    horizon = f'{{horizon_days="{s.horizon_days}"}}'
    metric("churn_expected_loss_eur", "Expected revenue loss over the forecast horizon, EUR",
           [(horizon, round(s.money.expected_loss / 100, 2))])
    metric("churn_revenue_at_risk_eur", "Horizon revenue not secured by balances, EUR",
           [(horizon, round(s.money.at_risk / 100, 2))])
    metric("churn_horizon_revenue_eur", "Revenue due over the forecast horizon, EUR",
           [(horizon, round(s.money.horizon_revenue / 100, 2))])
    metric("churn_monthly_revenue_eur", "Monthly recurring revenue of live services, EUR",
           [("", round(s.money.monthly_revenue / 100, 2))])
    metric("churn_expected_monthly_loss_eur", "Expected monthly recurring revenue loss, EUR",
           [("", round(s.money.expected_monthly_loss / 100, 2))])
    if s.run is not None:
        metric("churn_last_recalculation_timestamp_seconds", "Time of the last model recalculation",
               [("", s.run.computed_at.timestamp())])
        metric("churn_model_settings_version", "Version of model settings used in the last recalculation",
               [("", s.run.settings_version)])
    return "\n".join(lines) + "\n"


# --- График оттока (дополнительная возможность) ------------------------------


def get_churn_timeline(session: Session, days_back: int = 30) -> dict:
    """Факт удалений за days_back дней до опорной даты и прогноз непродлений на горизонт.

    Факт: переходы в deleted между снимками; удалённые услуги без истории — с оценочной датой
    expiresAt + льготный период. Прогноз: для каждой действующей услуги в день ближайшего
    списания добавляется её вероятность ухода.
    """
    settings, _, _ = store.load_settings(session)
    run = store.latest_run(session)
    as_of = run.as_of if run else store.resolve_as_of(session, settings)
    start = as_of - timedelta(days=days_back)
    grace = timedelta(days=settings.prolong_wait_grace_days)

    def day(ts: datetime) -> str:
        return ts.astimezone(timezone.utc).date().isoformat()

    deletions: dict[str, int] = defaultdict(int)
    transitioned: set[int] = set()
    for sid, changed_at in session.execute(
        select(ServiceStatusChange.service_id, ServiceStatusChange.changed_at).where(
            ServiceStatusChange.new_status == "deleted",
            ServiceStatusChange.old_status.is_not(None),
        )
    ):
        transitioned.add(sid)
        if start <= changed_at <= as_of:
            deletions[day(changed_at)] += 1

    estimated: dict[str, int] = defaultdict(int)
    for sid, expires_at in session.execute(select(Service.id, Service.expires_at).where(Service.status == "deleted")):
        if sid not in transitioned and start <= expires_at + grace <= as_of:
            estimated[day(expires_at + grace)] += 1

    due: dict[str, int] = defaultdict(int)
    expected: dict[str, float] = defaultdict(float)
    for days_to_expiry, probability in session.execute(
        select(ServiceRisk.days_to_expiry, ServiceRisk.probability).where(ServiceRisk.scored)
    ):
        offset = max(float(days_to_expiry), 0.0)
        if offset <= settings.horizon_days:
            key = day(as_of + timedelta(days=offset))
            due[key] += 1
            expected[key] += float(probability or 0)

    points = []
    for i in range(days_back, -1, -1):
        key = day(as_of - timedelta(days=i))
        points.append(TimelinePoint(date=key, kind="fact", deletions=deletions[key],
                                    estimated_deletions=estimated[key]))
    for i in range(settings.horizon_days + 1):
        key = day(as_of + timedelta(days=i))
        points.append(TimelinePoint(date=key, kind="forecast", due_services=due[key],
                                    expected_non_renewals=round(expected[key], 4)))
    return _json(ChurnTimeline(as_of=as_of if run else None, horizon_days=settings.horizon_days, points=points))
