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
    first_due = 0.0 if service.status == "prolong_wait" else max(days_to_expiry, 0.0)
    if first_due > horizon_days:
        return 0
    return 1 + math.floor((horizon_days - first_due) / PERIOD_DAYS[service.payment_term] + 1e-9)


def level_for(score: float | None, settings: ModelSettings) -> str:
    if score is None:
        return "none"
    if score >= settings.thresholds.high:
        return "high"
    if score >= settings.thresholds.medium:
        return "medium"
    return "low"


def _allocate(amount: int, services: list[ServiceData], cost: dict[int, int]) -> dict[int, int]:
    """Распределяет деньги по услугам в переданном порядке, пока они не кончатся."""
    allocated, remaining = {}, amount
    for s in services:
        part = min(cost[s.id], remaining)
        allocated[s.id] = part
        remaining -= part
    return allocated


def _no_score_reason(services: list[ServiceData]) -> dict:
    if not services:
        text = "У клиента нет услуг: данных для оценки недостаточно"
    else:
        text = "Нет действующих услуг (все удалены или заблокированы): оценивать нечего"
    return {"feature": "no_data", "title": "Нет оценки", "text": text, "points": None,
            "serviceId": None, "serviceName": None, "services": 0}


def _account_reasons(results: list[ServiceResult]) -> list[dict]:
    """Причины клиента: по каждому признаку берётся самый сильный вклад среди его услуг."""
    best: dict[str, tuple[ContributionResult, ServiceData]] = {}
    count: dict[str, int] = defaultdict(int)
    for r in results:
        for c in r.contributions:
            count[c.feature] += 1
            if c.feature not in best or c.points > best[c.feature][0].points:
                best[c.feature] = (c, r.service)
    reasons = [
        {"feature": c.feature, "title": c.title, "text": c.text, "points": round(c.points, 2),
         "serviceId": s.id, "serviceName": s.name, "services": count[c.feature]}
        for c, s in best.values()
    ]
    return sorted(reasons, key=lambda r: -r["points"])


def _aggregate(scored: list[ServiceResult], settings: ModelSettings) -> float:
    if settings.aggregation == "max":
        return max(r.score for r in scored)
    total = sum(r.monthly_price for r in scored)
    if total == 0:
        return sum(r.score for r in scored) / len(scored)
    return sum(r.score * r.monthly_price for r in scored) / total


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
    account_cost = sum(cost[s.id] for s in live)

    by_due = sorted(live, key=lambda s: (0.0 if s.status == "prolong_wait" else max(days[s.id], 0.0), s.id))
    available = account.balance + account.usable_bonus
    funded = _allocate(available, by_due, cost)
    # Выручку гарантирует только списание реальных денег, поэтому здесь без бонусов. Услуга в prolong_wait
    # не гарантирована даже при автопродлении: сам статус означает, что автосписание уже не прошло.
    secured = _allocate(account.balance, [s for s in by_due if s.auto_prolong and s.status != "prolong_wait"], cost)

    results: list[ServiceResult] = []
    for s in services:
        mp = monthly_price(s.price, s.payment_term)
        if s.status not in LIVE_STATUSES:
            results.append(ServiceResult(s, False, None, None, [], days[s.id], 0, mp, 0, 0, 0, 0, 0))
            continue
        ctx = ServiceContext(
            service=s, account=account, account_services=tuple(services),
            tx=tx_stats.get(s.id, TxStats()), as_of=as_of, settings=settings,
            age_days=days_between(as_of, s.created_at), days_to_expiry=days[s.id],
            renewals_in_horizon=renewals[s.id], horizon_cost=cost[s.id],
            account_horizon_cost=account_cost, available_funds=available,
            funded_share=funded[s.id] / cost[s.id] if cost[s.id] else 1.0,
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
        score = round(min(100.0, sum(c.points for c in contributions)), 2)
        probability = score / 100
        at_risk = cost[s.id] - secured.get(s.id, 0)
        results.append(ServiceResult(
            s, True, score, round(probability, 4), contributions, days[s.id], renewals[s.id], mp,
            cost[s.id], secured.get(s.id, 0), at_risk, probability * at_risk, probability * mp,
        ))

    scored = [r for r in results if r.scored]
    if scored:
        score = round(_aggregate(scored, settings), 2)
        reasons = _account_reasons(scored)
    else:
        score, reasons = None, [_no_score_reason(services)]

    return AccountResult(
        account=account, score=score, level=level_for(score, settings), reasons=reasons,
        live_services=len(scored),
        monthly_revenue=round(sum(r.monthly_price for r in scored), 2),
        horizon_revenue=round(sum(r.horizon_revenue for r in scored), 2),
        secured_by_balance=round(sum(r.secured_by_balance for r in scored), 2),
        at_risk=round(sum(r.at_risk for r in scored), 2),
        expected_loss=round(sum(r.expected_loss for r in scored), 2),
        expected_monthly_loss=round(sum(r.expected_monthly_loss for r in scored), 2),
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
