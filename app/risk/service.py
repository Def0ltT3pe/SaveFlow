import functools
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, or_, select
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
    BIGINT_MAX,
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
from app.risk.terms import PERIOD_DAYS

SORT_COLUMNS = {
    "id": Account.id,
    "email": Account.email,
    "balance": Account.balance,
    "totalReplenished": Account.total_replenished,
    "score": AccountRisk.score,
    "expectedLoss": AccountRisk.expected_loss,
}
LEVELS = ("high", "medium", "low", "none")
SOURCE_TABLES = ((Account, "public.accounts"), (Service, "public.services"), (Transaction, "public.transactions"))
MAX_PAGE = 500
MAX_OFFSET = 10**9
MAX_SEARCH = 320
MAX_DAYS_BACK = 365


def _releases_connection(fn):
    """После сборки ответа транзакция завершается и соединение возвращается в пул.

    FastAPI закрывает сессию из Depends(get_db) только после отправки ответа клиенту; по медленной
    сети (туннель, мобильный интернет) это секунды, и всё это время соединение было бы занято.
    Ответ к этому моменту — уже готовый dict, поэтому откат ничего не теряет.
    """

    @functools.wraps(fn)
    def wrapper(session: Session, *args, **kwargs):
        try:
            return fn(session, *args, **kwargs)
        finally:
            session.rollback()

    return wrapper


def _json(model) -> dict:
    return model.model_dump(mode="json", by_alias=True)


def _as_int(value, name: str) -> int:
    """Целое из числа или строки из цифр; всё остальное (bool, '10; DROP …', 1.5) — ValueError."""
    if isinstance(value, bool):
        raise ValueError(f"{name} должен быть целым числом")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value)
    raise ValueError(f"{name} должен быть целым числом")


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
    run = store.insert_run(session, as_of=as_of, settings=settings, settings_version=version,
                           snapshot_id=snapshot_id or store.latest_snapshot_id(session), reason=reason)
    store.notify(session, {"event": reason, "runId": run.id, "settingsVersion": version,
                           "asOf": as_of.isoformat()})
    return _run_out(run)


def _settings_ahead_of_run(session: Session) -> bool:
    latest = store.latest_run(session)
    return latest is None or latest.settings_version < store.settings_version(session)


def _catch_up(session: Session) -> None:
    """Пересчитать, если настройки новее последнего пересчёта, не дожидаясь чужого пересчёта.

    Если пересчёт уже идёт, запрос не ждёт его, держа соединение из пула: тот, кто считает,
    после своего COMMIT снова зайдёт сюда, увидит новую версию настроек и посчитает ещё раз.
    """
    while True:
        try:
            if not store.try_lock(session):
                session.rollback()
                return
            if not _settings_ahead_of_run(session):
                session.rollback()
                # Пока блокировка была у нас, настройки могли сохранить: тот запрос не взял её
                # и рассчитывает на нас. Сверяем версии ещё раз уже после её освобождения.
                if not _settings_ahead_of_run(session):
                    session.rollback()
                    return
                continue
            _recalculate(session, "settings")
            session.commit()
        except Exception:
            session.rollback()
            raise


def recalculate(session: Session, reason: str = "manual") -> dict:
    """Пересчитать все оценки (например, по таймеру в режиме asOfMode=now)."""
    try:
        store.lock(session)
        run = _recalculate(session, reason)
        session.commit()
    except Exception:
        session.rollback()
        raise
    _catch_up(session)
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

        # DELETE, а не TRUNCATE: TRUNCATE берёт ACCESS EXCLUSIVE до конца транзакции, и чтение списка
        # клиентов ждёт всю загрузку (пул соединений кончается) или ловит deadlock. После DELETE читатели
        # до COMMIT видят прежний снимок. Порядок — от зависимых таблиц к accounts из-за внешних ключей.
        for model in (ServiceRisk, AccountRisk, Transaction, Service, Account):
            session.execute(delete(model))
        rows_by_model = {Account: snap.accounts, Service: snap.services, Transaction: snap.transactions}
        for model, table in SOURCE_TABLES:
            rows = rows_by_model[model]
            if rows:
                session.execute(insert(model), [r.model_dump() for r in rows])
            # Следующий id продолжает загруженные; имя таблицы уходит параметром, а не склейкой строк.
            max_id = select(func.max(model.id)).scalar_subquery()
            session.execute(select(func.setval(func.pg_get_serial_sequence(table, "id"),
                                               func.coalesce(max_id, 1), max_id.is_not(None))))

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
    # Пока шла загрузка, могли сохранить новые настройки: их пересчёт не должен потеряться.
    _catch_up(session)
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


@_releases_connection
def get_settings(session: Session) -> dict:
    return _json(_settings_out(session))


@_releases_connection
def update_settings(session: Session, patch: ModelSettingsPatch | dict) -> dict:
    """Меняет настройки (частично) и пересчитывает оценки.

    Если в этот момент уже идёт другой пересчёт, ответ приходит сразу с сохранёнными настройками,
    а новые оценки посчитает текущий пересчёт сразу после своего завершения.
    """
    patch = patch if isinstance(patch, ModelSettingsPatch) else ModelSettingsPatch.model_validate(patch)
    try:
        current, _, _ = store.load_settings(session, for_update=True)
        version, _ = store.save_settings(session, patch.apply_to(current))
        session.commit()
    except Exception:
        session.rollback()
        raise
    try:
        _catch_up(session)
    except Exception:
        # Пересчёт не удался: возвращаем прежние настройки (если после нас их никто не менял),
        # иначе панель показывала бы новые веса рядом с оценками по старым.
        store.revert_settings(session, current, saved_version=version)
        session.commit()
        raise
    return _json(_settings_out(session))


# --- GET /api/v1/customers, /api/v1/customers/{id} ---------------------------


def _money(r: AccountRisk | None) -> Money:
    if r is None:
        return Money(monthly_revenue=0, horizon_revenue=0, secured_by_balance=0, covered_by_bonus=0, at_risk=0,
                     expected_loss=0, expected_monthly_loss=0)
    return Money(monthly_revenue=_float(r.monthly_revenue), horizon_revenue=_float(r.horizon_revenue),
                 secured_by_balance=_float(r.secured_by_balance), covered_by_bonus=_float(r.covered_by_bonus),
                 at_risk=_float(r.at_risk),
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


def _search_id(search: str) -> int | None:
    """ID клиента из строки поиска: «42» или «#42». Число вне BIGINT — не ID, а просто текст."""
    digits = search.removeprefix("#")
    if not digits.isascii() or not digits.isdigit():
        return None
    value = int(digits)
    return value if 1 <= value <= BIGINT_MAX else None


@_releases_connection
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
    levels: фильтр по уровням (high/medium/low/none).
    search: подстрока email или ID клиента целиком («42» или «#42» найдёт клиента 42 и email с «42»).
    Клиенты без оценки (score = null) при сортировке по score всегда в конце.
    """
    if not isinstance(sort, str) or sort not in SORT_COLUMNS:
        raise ValueError(f"sort должен быть одним из {sorted(SORT_COLUMNS)}")
    if order not in ("asc", "desc"):
        raise ValueError("order должен быть asc или desc")
    if isinstance(levels, str):
        levels = [levels]
    if levels and (bad := set(map(str, levels)) - set(LEVELS)):
        raise ValueError(f"неизвестные уровни {sorted(bad)}; доступны {list(LEVELS)}")
    limit = min(max(_as_int(limit, "limit"), 1), MAX_PAGE)
    offset = min(max(_as_int(offset, "offset"), 0), MAX_OFFSET)
    if search is not None:
        search = str(search)
        # Email длиннее 320 символов или с нулевым байтом не бывает: честный ответ — никого не нашли.
        # Исключение здесь стало бы 500 в эндпоинте, а до БД такая строка доходить не должна.
        if len(search) > MAX_SEARCH or "\x00" in search:
            return _json(CustomerPage(items=[], total=0, limit=limit, offset=offset, sort=sort, order=order))

    stmt = select(Account, AccountRisk).outerjoin(AccountRisk, AccountRisk.account_id == Account.id)
    if levels:
        stmt = stmt.where(func.coalesce(AccountRisk.level, "none").in_(levels))
    search = search.strip() if search else ""
    if search:
        # autoescape экранирует % и _: строка поиска ищется буквально и уходит в БД параметром.
        condition = Account.email.icontains(search, autoescape=True)
        if (customer_id := _search_id(search)) is not None:
            condition = or_(condition, Account.id == customer_id)
        stmt = stmt.where(condition)
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    column = SORT_COLUMNS[sort]
    ordering = (column.asc() if order == "asc" else column.desc()).nulls_last()
    rows = session.execute(stmt.order_by(ordering, Account.id.asc()).limit(limit).offset(offset)).all()
    items = [CustomerListItem(**_customer_fields(a, r)) for a, r in rows]
    return _json(CustomerPage(items=items, total=total, limit=limit, offset=offset, sort=sort, order=order))


@_releases_connection
def get_customer(session: Session, customer_id: int | str) -> dict | None:
    """Подробная оценка клиента с услугами и вкладами признаков.

    None, если клиента нет или id не является целым числом в диапазоне bigint.
    """
    try:
        customer_id = _as_int(customer_id, "customer_id")
    except ValueError:
        return None
    if not 1 <= customer_id <= BIGINT_MAX:
        return None
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
            secured_by_balance=_float(sr and sr.secured_by_balance),
            covered_by_bonus=_float(sr and sr.covered_by_bonus), at_risk=_float(sr and sr.at_risk),
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
    run = store.latest_run(session)
    settings = store.run_settings(session, run)
    level = func.coalesce(AccountRisk.level, "none")
    high = level == "high"
    a = session.execute(
        select(
            func.count(Account.id),
            *(func.count().filter(level == name) for name in LEVELS),
            func.avg(AccountRisk.score),
            func.sum(AccountRisk.monthly_revenue), func.sum(AccountRisk.horizon_revenue),
            func.sum(AccountRisk.secured_by_balance), func.sum(AccountRisk.covered_by_bonus),
            func.sum(AccountRisk.at_risk),
            func.sum(AccountRisk.expected_loss), func.sum(AccountRisk.expected_monthly_loss),
            func.sum(AccountRisk.at_risk).filter(high), func.sum(Account.balance).filter(high),
            func.sum(Account.withdraw_balance).filter(high),
        ).select_from(Account).outerjoin(AccountRisk, AccountRisk.account_id == Account.id)
    ).one()
    (total, n_high, n_medium, n_low, n_none, avg_score, monthly, horizon, secured, by_bonus, at_risk,
     loss, monthly_loss, high_at_risk, high_balance, high_withdraw) = a
    s = session.execute(
        select(func.count(Service.id), func.count().filter(ServiceRisk.scored),
               func.count().filter(ServiceRisk.score >= settings.thresholds.medium))
        .select_from(Service).outerjoin(ServiceRisk, ServiceRisk.service_id == Service.id)
    ).one()

    money = Money(monthly_revenue=_float(monthly), horizon_revenue=_float(horizon),
                  secured_by_balance=_float(secured), covered_by_bonus=_float(by_bonus), at_risk=_float(at_risk),
                  expected_loss=_float(loss), expected_monthly_loss=_float(monthly_loss))
    h = settings.horizon_days
    explanation = (
        f"Ожидаемая потеря за {h} дн.: {eur(money.expected_loss)}. По каждой услуге: вероятность, что её не продлят "
        f"(score услуги / 100), × её сумма под угрозой — продления за ближайшие {h} дн., которые автопродление "
        f"не спишет ни с баланса, ни с бонусов. Всего продлений на {eur(money.horizon_revenue)}: "
        f"{eur(money.secured_by_balance)} спишется с балансов, {eur(money.covered_by_bonus)} оплатят бонусы, "
        f"под угрозой {eur(money.at_risk)}. Ежемесячная выручка {eur(money.monthly_revenue)}, "
        f"ожидаемая потеря в месяц {eur(money.expected_monthly_loss)}."
    )
    return RiskSummary(
        run=_run_out(run), horizon_days=h, accounts_total=total,
        accounts_by_level=LevelCounts(high=n_high, medium=n_medium, low=n_low, none=n_none),
        average_score=round(float(avg_score), 2) if avg_score is not None else None,
        services_total=s[0], services_live=s[1], services_at_risk=s[2], money=money,
        high_risk_at_risk=_float(high_at_risk), high_risk_balance=_float(high_balance),
        high_risk_withdrawable=_float(high_withdraw), explanation=explanation,
    )


@_releases_connection
def get_summary(session: Session) -> dict:
    return _json(_summary(session))


@_releases_connection
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
    metric("churn_covered_by_bonus_eur", "Horizon renewals that autoprolong pays with bonuses, EUR",
           [(horizon, round(s.money.covered_by_bonus / 100, 2))])
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


@_releases_connection
def _unpaid_renewals(session: Session, as_of: datetime, horizon_days: int, day) -> tuple[
        dict[str, float], dict[str, float], list[tuple[str, float, str]]]:
    """Продления в горизонте, не покрытые деньгами на счёте, по дням: сумма под угрозой и ожидаемые потери.

    Деньги оплачивают продления по порядку наступления (scoring._paid_renewals), поэтому непокрытые — последние
    renewals − paid продлений услуги. Потеря продления = вероятность непродления услуги × цена: в сумме по дням
    это expectedLoss услуги. Третье значение — для каждого клиента день первого непокрытого продления (тогда он
    либо платит, либо уходит), его вероятность ухода и уровень риска.
    """
    at_risk: dict[str, float] = defaultdict(float)
    loss: dict[str, float] = defaultdict(float)
    first: dict[int, tuple[float, float, str]] = {}
    for (account_id, days_to_expiry, renewals, secured, bonus, service_probability, price, term, status,
         score, level) in session.execute(
        select(ServiceRisk.account_id, ServiceRisk.days_to_expiry, ServiceRisk.renewals_in_horizon,
               ServiceRisk.secured_by_balance, ServiceRisk.covered_by_bonus, ServiceRisk.probability,
               Service.price, Service.payment_term, Service.status, AccountRisk.score, AccountRisk.level)
        .join(Service, Service.id == ServiceRisk.service_id)
        .join(AccountRisk, AccountRisk.account_id == ServiceRisk.account_id)
        .where(ServiceRisk.scored)
    ):
        probability = float(score or 0) / 100
        paid = round((float(secured) + float(bonus)) / price) if price else renewals
        first_due = 0.0 if status == "prolong_wait" else max(float(days_to_expiry), 0.0)
        for k in range(min(paid, renewals), renewals):
            offset = min(first_due + k * PERIOD_DAYS[term], float(horizon_days))
            key = day(as_of + timedelta(days=offset))
            at_risk[key] += price
            loss[key] += float(service_probability or 0) * price
            if account_id not in first or offset < first[account_id][0]:
                first[account_id] = (offset, probability, level)
    first_unpaid = [(day(as_of + timedelta(days=offset)), p, level) for offset, p, level in first.values()]
    return at_risk, loss, first_unpaid


def get_churn_timeline(session: Session, days_back: int = 30) -> dict:
    """Факт удалений за days_back дней до опорной даты и прогноз непродлений на горизонт.

    Факт: переходы в deleted между снимками; удалённые услуги без истории — с оценочной датой
    expiresAt + льготный период. Прогноз: для каждой действующей услуги в день ближайшего
    списания добавляется её вероятность ухода.
    """
    days_back = min(max(_as_int(days_back, "days_back"), 0), MAX_DAYS_BACK)
    run = store.latest_run(session)
    settings = store.run_settings(session, run)
    as_of = run.as_of if run else store.resolve_as_of(session, settings)
    start = as_of - timedelta(days=days_back)
    grace = timedelta(days=settings.prolong_wait_grace_days)

    def day(ts: datetime) -> str:
        return ts.astimezone(timezone.utc).date().isoformat()

    # Одна услуга — одно удаление: берётся последний переход в deleted. Если снимки загружали
    # не по порядку (deleted → active → deleted), удаление не считается дважды; вернувшаяся услуга не считается.
    deleted_at: dict[int, tuple[datetime, int, int] | None] = {}
    for sid, old_status, new_status, changed_at, owner_id, price in session.execute(
        select(ServiceStatusChange.service_id, ServiceStatusChange.old_status, ServiceStatusChange.new_status,
               ServiceStatusChange.changed_at, ServiceStatusChange.owner_id, ServiceStatusChange.price)
        .order_by(ServiceStatusChange.snapshot_id, ServiceStatusChange.id)
    ):
        if new_status == "deleted" and old_status is not None:
            deleted_at[sid] = (changed_at, owner_id, price)
        elif new_status is not None and new_status != "deleted" and sid in deleted_at:
            deleted_at[sid] = None
    transitioned = set(deleted_at)
    deletions: dict[str, int] = defaultdict(int)
    # Клиенты и деньги факта: и по точным датам, и по оценённым. Деньги — одно неоплаченное продление услуги.
    churned: dict[str, set[int]] = defaultdict(set)
    lost: dict[str, float] = defaultdict(float)
    for deleted in deleted_at.values():
        if deleted is not None and start <= deleted[0] <= as_of:
            key = day(deleted[0])
            deletions[key] += 1
            churned[key].add(deleted[1])
            lost[key] += deleted[2]

    estimated: dict[str, int] = defaultdict(int)
    for sid, expires_at, owner_id, price in session.execute(
        select(Service.id, Service.expires_at, Service.owner_id, Service.price).where(Service.status == "deleted")
    ):
        if sid not in transitioned and start <= expires_at + grace <= as_of:
            key = day(expires_at + grace)
            estimated[key] += 1
            churned[key].add(owner_id)
            lost[key] += price

    due: dict[str, int] = defaultdict(int)
    due_at_risk: dict[str, int] = defaultdict(int)
    expected: dict[str, float] = defaultdict(float)
    for days_to_expiry, probability, score in session.execute(
        select(ServiceRisk.days_to_expiry, ServiceRisk.probability, ServiceRisk.score).where(ServiceRisk.scored)
    ):
        offset = max(float(days_to_expiry), 0.0)
        if offset <= settings.horizon_days:
            key = day(as_of + timedelta(days=offset))
            due[key] += 1
            expected[key] += float(probability or 0)
            if score is not None and float(score) >= settings.thresholds.medium:
                due_at_risk[key] += 1

    revenue_at_risk, loss, first_unpaid = _unpaid_renewals(session, as_of, settings.horizon_days, day)
    clients_at_risk: dict[str, int] = defaultdict(int)
    expected_clients: dict[str, float] = defaultdict(float)
    for key, probability, level in first_unpaid:
        expected_clients[key] += probability
        if level in ("medium", "high"):
            clients_at_risk[key] += 1

    points = []
    for i in range(days_back, -1, -1):
        key = day(as_of - timedelta(days=i))
        points.append(TimelinePoint(date=key, kind="fact", deletions=deletions[key],
                                    estimated_deletions=estimated[key], churned_clients=len(churned[key]),
                                    lost_revenue=round(lost[key], 2)))
    for i in range(settings.horizon_days + 1):
        key = day(as_of + timedelta(days=i))
        points.append(TimelinePoint(date=key, kind="forecast", due_services=due[key], due_at_risk=due_at_risk[key],
                                    expected_non_renewals=round(expected[key], 4),
                                    clients_at_risk=clients_at_risk[key],
                                    expected_churned_clients=round(expected_clients[key], 4),
                                    revenue_at_risk=round(revenue_at_risk[key], 2), expected_loss=round(loss[key], 2)))
    return _json(ChurnTimeline(as_of=as_of if run else None, horizon_days=settings.horizon_days, points=points))
