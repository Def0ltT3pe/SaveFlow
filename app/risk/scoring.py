import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.risk.context import ServiceContext
from app.risk.domain import AccountData, ServiceData, TxStats
from app.risk.registry import all_features
from app.risk.schemas import ModelSettings
from app.risk.terms import LIVE_STATUSES, PERIOD_DAYS, monthly_price

SECONDS_PER_DAY = 86400


@dataclass
class ContributionResult:
    feature: str
    title: str
    value: float
    weight: float
    points: float
    text: str | None


@dataclass
class ServiceResult:
    service: ServiceData
    scored: bool
    score: float | None
    probability: float | None
    contributions: list[ContributionResult]
    days_to_expiry: float
    renewals_in_horizon: int
    monthly_price: float
    horizon_revenue: float
    secured_by_balance: float
    covered_by_bonus: float
    at_risk: float
    expected_loss: float
    expected_monthly_loss: float


@dataclass
class AccountResult:
    account: AccountData
    score: float | None
    level: str
    reasons: list[dict]
    live_services: int
    monthly_revenue: float
    horizon_revenue: float
    secured_by_balance: float
    covered_by_bonus: float
    at_risk: float
    expected_loss: float
    expected_monthly_loss: float
    services: list[ServiceResult] = field(default_factory=list)


def days_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / SECONDS_PER_DAY


def renewals_in_horizon(service: ServiceData, days_to_expiry: float, horizon_days: int) -> int:
    """Сколько раз услугу нужно оплатить в ближайшие horizon_days.

    Услуга в prolong_wait должна быть оплачена сразу, даже если expiresAt в данных почему-то в будущем.
    """
    if service.status not in LIVE_STATUSES:
        return 0
    return _renewals_by(horizon_days, first_due_days(service, days_to_expiry), PERIOD_DAYS[service.payment_term])


def first_due_days(service: ServiceData, days_to_expiry: float) -> float:
    """Через сколько дней ближайшее списание: prolong_wait — сейчас."""
    return 0.0 if service.status == "prolong_wait" else max(days_to_expiry, 0.0)


def _renewals_by(t: float, first_due: float, period: float) -> int:
    """Сколько продлений наступит к моменту t (в днях от опорной даты)."""
    return 0 if t < first_due else 1 + math.floor((t - first_due) / period + 1e-9)


def runway_days(live: list[ServiceData], first_due: dict[int, float], available: int, horizon: int) -> float | None:
    """Через сколько дней наступит первое продление, на которое у клиента уже не хватит денег.

    None — денег хватит на все продления в горизонте. Продления оплачиваются по порядку наступления.
    """
    def spent(t: float) -> int:
        return sum(s.price * _renewals_by(t, first_due[s.id], PERIOD_DAYS[s.payment_term]) for s in live)

    if spent(float(horizon)) <= available:
        return None
    if spent(0.0) > available:
        return 0.0
    lo, hi = 0.0, float(horizon)
    for _ in range(40):  # точность ~1e-11 дня
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if spent(mid) <= available else (lo, mid)
    return hi


def _first_renewal_from(t: float, first_due: float, period: float, horizon: int) -> float | None:
    """Ближайшее продление услуги не раньше момента t (в пределах горизонта)."""
    when = first_due if first_due >= t else first_due + math.ceil((t - first_due) / period - 1e-9) * period
    return when if when <= horizon else None


def level_for(score: float | None, settings: ModelSettings) -> str:
    if score is None:
        return "none"
    if score >= settings.thresholds.high:
        return "high"
    if score >= settings.thresholds.medium:
        return "medium"
    return "low"


def _paid_before(money_ends: float | None, renewals: int, first_due: float, service: ServiceData) -> int:
    """Сколько продлений услуги успеют оплатить до момента, когда кончатся деньги (None — все)."""
    if money_ends is None:
        return renewals
    # Продление ровно в момент money_ends — то самое, на которое уже не хватило.
    return min(renewals, _renewals_by(money_ends - 1e-7, first_due, PERIOD_DAYS[service.payment_term]))


def _no_score_reason(services: list[ServiceData]) -> dict:
    if not services:
        text = "У клиента нет услуг: данных для оценки недостаточно"
    else:
        text = "Нет действующих услуг (все удалены или заблокированы): оценивать нечего"
    return {"feature": "no_data", "title": "Нет оценки", "text": text, "points": None,
            "serviceId": None, "serviceName": None, "services": 0}


def _account_reasons(scored: list[tuple[ServiceData, float, list[ContributionResult]]]) -> list[dict]:
    """Причины клиента: по каждому признаку берётся самый сильный вклад среди его услуг."""
    best: dict[str, tuple[ContributionResult, ServiceData]] = {}
    count: dict[str, int] = defaultdict(int)
    for service, _, contributions in scored:
        for c in contributions:
            count[c.feature] += 1
            if c.feature not in best or c.points > best[c.feature][0].points:
                best[c.feature] = (c, service)
    features = all_features()
    reasons = []
    for c, s in best.values():
        # Признак про клиента целиком не привязываем к услуге: иначе «Уже уходил — vps-1» читается так,
        # будто уходила эта услуга. services = 0 — «относится к клиенту, а не к услугам».
        per_account = features[c.feature].scope == "account"
        reasons.append({"feature": c.feature, "title": c.title, "text": c.text, "points": round(c.points, 2),
                        "serviceId": None if per_account else s.id,
                        "serviceName": None if per_account else s.name,
                        "services": 0 if per_account else count[c.feature]})
    return sorted(reasons, key=lambda r: -r["points"])


def _aggregate(scored: list[tuple[ServiceData, float, list[ContributionResult]]], settings: ModelSettings) -> float:
    if settings.aggregation == "max":
        return max(score for _, score, _ in scored)
    weights = [monthly_price(s.price, s.payment_term) for s, _, _ in scored]
    total = sum(weights)
    if total == 0:
        return sum(score for _, score, _ in scored) / len(scored)
    return sum(score * w for (_, score, _), w in zip(scored, weights)) / total


def score_account(
    account: AccountData,
    services: list[ServiceData],
    tx_stats: dict[int, TxStats],
    settings: ModelSettings,
    as_of: datetime,
) -> AccountResult:
    features = all_features()
    horizon = settings.horizon_days
    # Услуги, созданные позже опорной даты, на эту дату ещё не существуют: их не оцениваем и не считаем в деньгах.
    services = [s for s in services if s.created_at <= as_of]
    live = [s for s in services if s.status in LIVE_STATUSES]

    days = {s.id: days_between(s.expires_at, as_of) for s in services}
    renewals = {s.id: renewals_in_horizon(s, days[s.id], horizon) for s in services}
    cost = {s.id: s.price * renewals[s.id] for s in services}
    first_due = {s.id: first_due_days(s, days[s.id]) for s in live}
    account_cost = sum(cost[s.id] for s in live)

    available = account.balance + account.usable_bonus
    run_out = runway_days(live, first_due, available, horizon)
    # Автопродление оплачивает каждое продление в момент наступления: сначала с основного баланса, потом
    # доступными бонусами. Поэтому деньги распределяются по времени, как и в признаке нехватки средств:
    # почасовая услуга не «забирает» весь месяц вперёд у месячной, которую нужно продлить раньше.
    # Услуга в prolong_wait не покрыта даже при автопродлении: сам статус означает, что автосписание не прошло.
    auto = [s for s in live if s.auto_prolong and s.status != "prolong_wait"]
    balance_ends = runway_days(auto, first_due, account.balance, horizon)
    funds_end = runway_days(auto, first_due, account.balance + account.usable_bonus, horizon)
    secured = {s.id: s.price * _paid_before(balance_ends, renewals[s.id], first_due[s.id], s) for s in auto}
    by_bonus = {s.id: s.price * _paid_before(funds_end, renewals[s.id], first_due[s.id], s) - secured[s.id]
                for s in auto}

    scored: list[tuple[ServiceData, float, list[ContributionResult]]] = []
    for s in live:
        unfunded = None
        if run_out is not None and renewals[s.id]:
            unfunded = _first_renewal_from(run_out, first_due[s.id], PERIOD_DAYS[s.payment_term], horizon)
        ctx = ServiceContext(
            service=s, account=account, account_services=tuple(services),
            tx=tx_stats.get(s.id, TxStats()), as_of=as_of, settings=settings,
            age_days=days_between(as_of, s.created_at), days_to_expiry=days[s.id],
            renewals_in_horizon=renewals[s.id], horizon_cost=cost[s.id],
            account_horizon_cost=account_cost, available_funds=available,
            runway_days=run_out, unfunded_in_days=unfunded,
        )
        contributions = []
        for key, feat in features.items():
            weight = settings.weights.get(key, 0)
            if weight <= 0:
                continue
            res = feat.compute(ctx)
            value = min(max(res.value, 0.0), 1.0)
            if value > 0:
                contributions.append(ContributionResult(key, feat.title, round(value, 4), weight,
                                                        round(weight * value, 2), res.reason))
        contributions.sort(key=lambda c: -c.points)
        scored.append((s, round(min(100.0, sum(c.points for c in contributions)), 2), contributions))

    if scored:
        score = round(_aggregate(scored, settings), 2)
        reasons = _account_reasons(scored)
    else:
        score, reasons = None, [_no_score_reason(services)]
    # Деньги считаются по вероятности ухода клиента: уходя, он уходит со всеми услугами.
    # Так ожидаемая потеря = score клиента / 100 × сумма под угрозой, как и написано в объяснении.
    p = (score or 0) / 100

    results: list[ServiceResult] = []
    scores = {s.id: (sc, contribs) for s, sc, contribs in scored}
    for s in services:
        mp = monthly_price(s.price, s.payment_term)
        if s.id not in scores:
            results.append(ServiceResult(s, False, None, None, [], days[s.id], 0, mp, 0, 0, 0, 0, 0, 0))
            continue
        service_score, contributions = scores[s.id]
        at_risk = cost[s.id] - secured.get(s.id, 0) - by_bonus.get(s.id, 0)
        results.append(ServiceResult(
            s, True, service_score, round(service_score / 100, 4), contributions, days[s.id], renewals[s.id], mp,
            cost[s.id], secured.get(s.id, 0), by_bonus.get(s.id, 0), at_risk, p * at_risk, p * mp,
        ))

    live_results = [r for r in results if r.scored]
    return AccountResult(
        account=account, score=score, level=level_for(score, settings), reasons=reasons,
        live_services=len(live_results),
        monthly_revenue=round(sum(r.monthly_price for r in live_results), 2),
        horizon_revenue=round(sum(r.horizon_revenue for r in live_results), 2),
        secured_by_balance=round(sum(r.secured_by_balance for r in live_results), 2),
        covered_by_bonus=round(sum(r.covered_by_bonus for r in live_results), 2),
        at_risk=round(sum(r.at_risk for r in live_results), 2),
        expected_loss=round(sum(r.expected_loss for r in live_results), 2),
        expected_monthly_loss=round(sum(r.expected_monthly_loss for r in live_results), 2),
        services=results,
    )


def score_portfolio(
    accounts: list[AccountData],
    services: list[ServiceData],
    tx_stats: dict[int, TxStats],
    settings: ModelSettings,
    as_of: datetime,
) -> list[AccountResult]:
    by_owner: dict[int, list[ServiceData]] = defaultdict(list)
    for s in services:
        by_owner[s.owner_id].append(s)
    return [score_account(a, by_owner[a.id], tx_stats, settings, as_of) for a in accounts]
