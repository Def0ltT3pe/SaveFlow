"""Сводка и график оттока: каждое число пересчитывается независимо из данных клиентов и услуг."""
import copy
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.risk import service
from app.risk.terms import PERIOD_DAYS


def _clean_history(session) -> None:
    """История статусов копится между тестами: здесь нужна только текущая загрузка data.json."""
    session.execute(text("DELETE FROM public.service_status_history"))
    session.commit()


def _customers(session) -> list[dict]:
    return [service.get_customer(session, c["id"]) for c in service.get_customers(session, limit=500)["items"]]


def _day(iso: str, shift_days: float = 0) -> str:
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00")) + timedelta(days=shift_days)
    return moment.astimezone(timezone.utc).date().isoformat()


def test_summary_counts_and_averages(session, loaded):
    s = service.get_summary(session)
    customers = _customers(session)
    assert s["accountsTotal"] == len(customers) == 10
    assert s["accountsByLevel"] == {lv: sum(c["level"] == lv for c in customers) for lv in ("high", "medium", "low", "none")}
    scores = [c["score"] for c in customers if c["score"] is not None]
    assert s["averageScore"] == pytest.approx(sum(scores) / len(scores), abs=0.01)
    services = [x for c in customers for x in c["services"]]
    assert s["servicesTotal"] == len(services) == 30
    assert s["servicesLive"] == sum(x["scored"] for x in services)
    medium = service.get_settings(session)["settings"]["thresholds"]["medium"]
    assert s["servicesAtRisk"] == sum(x["scored"] and x["score"] >= medium for x in services)


def test_summary_high_risk_block(session, loaded, source_data):
    s = service.get_summary(session)
    high = [c for c in _customers(session) if c["level"] == "high"]
    assert high, "в тестовых данных должен быть клиент с высоким риском"
    assert s["highRiskAtRisk"] == pytest.approx(sum(c["money"]["atRisk"] for c in high), abs=0.01)
    assert s["highRiskBalance"] == sum(c["balance"] for c in high)
    withdraw = {a["id"]: a["withdrawBalance"] for a in source_data["accounts"]}
    assert s["highRiskWithdrawable"] == sum(withdraw[c["id"]] for c in high)


def test_summary_money_is_sum_of_customers(session, loaded):
    s = service.get_summary(session)["money"]
    customers = service.get_customers(session, limit=500)["items"]
    for field in s:
        assert s[field] == pytest.approx(sum(c["money"][field] for c in customers), abs=0.05), field


def test_loyalty_is_inverse_of_score(session, loaded):
    for c in service.get_customers(session, limit=500)["items"]:
        assert c["loyalty"] == (None if c["score"] is None else pytest.approx(100 - c["score"]))


def test_empty_summary_has_zeros(session, loaded, source_data):
    service.replace_snapshot(session, {"referenceDate": "2026-10-19T15:00:00Z", "accounts": [], "services": [],
                                       "transactions": []})
    s = service.get_summary(session)
    assert s["accountsTotal"] == 0 and s["averageScore"] is None
    assert all(v == 0 for v in s["money"].values())
    assert s["highRiskAtRisk"] == s["highRiskBalance"] == 0


def test_forecast_puts_each_live_service_on_its_due_day(session, loaded):
    timeline = service.get_churn_timeline(session)
    as_of = timeline["asOf"]
    medium = service.get_settings(session)["settings"]["thresholds"]["medium"]
    due, at_risk, expected = Counter(), Counter(), defaultdict(float)
    for c in _customers(session):
        for x in c["services"]:
            if x["scored"] and max(x["daysToExpiry"], 0) <= timeline["horizonDays"]:
                key = _day(as_of, max(x["daysToExpiry"], 0))
                due[key] += 1
                at_risk[key] += x["score"] >= medium
                expected[key] += x["probability"] or 0
    forecast = {p["date"]: p for p in timeline["points"] if p["kind"] == "forecast"}
    assert list(forecast)[0] == _day(as_of) and len(forecast) == timeline["horizonDays"] + 1
    for day, point in forecast.items():
        assert point["dueServices"] == due[day], day
        assert point["dueAtRisk"] == at_risk[day] <= point["dueServices"], day
        assert point["expectedNonRenewals"] == pytest.approx(expected[day], abs=1e-3), day
    assert sum(due.values()) > sum(at_risk.values()) > 0  # «под риском» — не все продления


def test_due_at_risk_follows_medium_threshold(session, loaded):
    def total_at_risk():
        return sum(p["dueAtRisk"] for p in service.get_churn_timeline(session)["points"])

    default = total_at_risk()
    service.update_settings(session, {"thresholds": {"medium": 1, "high": 99}})
    looser = total_at_risk()
    service.update_settings(session, {"thresholds": {"medium": 98, "high": 99}})
    stricter = total_at_risk()
    assert stricter <= default <= looser and stricter < looser
    assert all(p["dueAtRisk"] == 0 for p in service.get_churn_timeline(session)["points"] if p["kind"] == "fact")


def test_fact_estimates_deletion_date_for_services_without_history(session, loaded, source_data):
    _clean_history(session)
    timeline = service.get_churn_timeline(session, days_back=365)
    as_of = timeline["asOf"]
    start = datetime.fromisoformat(as_of.replace("Z", "+00:00")) - timedelta(days=365)
    end = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    estimated = Counter()
    for s in source_data["services"]:
        if s["status"] == "deleted":
            moment = datetime.fromisoformat(s["expiresAt"].replace("Z", "+00:00")) + timedelta(days=5)
            if start <= moment <= end:
                estimated[moment.date().isoformat()] += 1
    fact = {p["date"]: p for p in timeline["points"] if p["kind"] == "fact"}
    assert list(fact)[-1] == _day(as_of) and len(fact) == 366
    assert {d: p["estimatedDeletions"] for d, p in fact.items() if p["estimatedDeletions"]} == dict(estimated)
    assert sum(estimated.values()) > 0
    assert all(p["deletions"] == 0 for p in fact.values())  # переходов между снимками ещё не было


def test_fact_counts_transition_on_snapshot_date_and_ignores_returned_services(session, loaded, source_data):
    _clean_history(session)
    def snapshot(day, statuses):
        data = copy.deepcopy(source_data)
        data["referenceDate"] = f"2026-10-{day:02d}T12:00:00Z"
        for s in data["services"]:
            if s["id"] in statuses:
                s["status"] = statuses[s["id"]]
        return data

    active = [s["id"] for s in source_data["services"] if s["status"] == "active"][:2]
    service.replace_snapshot(session, snapshot(20, {active[0]: "deleted", active[1]: "deleted"}))
    service.replace_snapshot(session, snapshot(21, {active[0]: "deleted", active[1]: "active"}))
    fact = {p["date"]: p for p in service.get_churn_timeline(session, days_back=5)["points"] if p["kind"] == "fact"}
    assert fact["2026-10-20"]["deletions"] == 1  # вторая услуга вернулась и не считается
    assert sum(p["deletions"] for p in fact.values()) == 1


def _unpaid(timeline, customers):
    """Непокрытые деньгами продления каждого клиента: (смещение, день, цена, вероятность непродления услуги).
    Деньги платят продления по порядку, поэтому непокрытые — последние
    renewalsInHorizon − (securedByBalance + coveredByBonus) / price."""
    as_of, horizon = timeline["asOf"], timeline["horizonDays"]
    unpaid = defaultdict(list)
    for c in customers:
        for x in c["services"]:
            if not x["scored"]:
                continue
            paid = round((x["securedByBalance"] + x["coveredByBonus"]) / x["price"]) if x["price"] else x["renewalsInHorizon"]
            first = 0 if x["status"] == "prolong_wait" else max(x["daysToExpiry"], 0)
            for k in range(paid, x["renewalsInHorizon"]):
                offset = min(first + k * PERIOD_DAYS[x["paymentTerm"]], horizon)
                unpaid[c["id"]].append((offset, _day(as_of, offset), x["price"], x["probability"]))
    return unpaid


def test_forecast_money_by_day_adds_up_to_summary(session, loaded):
    timeline = service.get_churn_timeline(session)
    customers = _customers(session)
    at_risk, loss = defaultdict(float), defaultdict(float)
    for c in customers:
        for _, day, price, probability in _unpaid(timeline, customers)[c["id"]]:
            at_risk[day] += price
            loss[day] += probability * price   # потеря — по вероятности непродления самой услуги
    forecast = {p["date"]: p for p in timeline["points"] if p["kind"] == "forecast"}
    for day, point in forecast.items():
        assert point["revenueAtRisk"] == pytest.approx(at_risk[day], abs=0.01), day
        assert point["expectedLoss"] == pytest.approx(loss[day], abs=0.01), day
    assert set(at_risk) <= set(forecast)
    # Сумма по дням — те же «Ожидаемые потери» и «Под угрозой», что в сводке
    money = service.get_summary(session)["money"]
    assert sum(p["expectedLoss"] for p in forecast.values()) == pytest.approx(money["expectedLoss"], abs=0.5)
    assert sum(p["revenueAtRisk"] for p in forecast.values()) == pytest.approx(money["atRisk"], abs=0.01)
    assert money["expectedLoss"] > 0


def test_forecast_puts_each_client_on_first_unpaid_renewal(session, loaded):
    timeline = service.get_churn_timeline(session)
    customers = _customers(session)
    unpaid = _unpaid(timeline, customers)
    at_risk, expected = Counter(), defaultdict(float)
    for c in customers:
        if unpaid[c["id"]]:
            _, day, _, _ = min(unpaid[c["id"]])
            expected[day] += c["score"] / 100
            at_risk[day] += c["level"] in ("medium", "high")
    forecast = [p for p in timeline["points"] if p["kind"] == "forecast"]
    for point in forecast:
        assert point["clientsAtRisk"] == at_risk[point["date"]], point["date"]
        assert point["expectedChurnedClients"] == pytest.approx(expected[point["date"]], abs=1e-3), point["date"]
    # Каждый клиент — один раз; клиент, чьи продления все оплачены деньгами на счёте, на графике не появляется
    risky = [c for c in customers if c["level"] in ("medium", "high")]
    assert sum(p["clientsAtRisk"] for p in forecast) == sum(bool(unpaid[c["id"]]) for c in risky) > 0
    assert all(p["clientsAtRisk"] == p["expectedChurnedClients"] == p["expectedLoss"] == 0
               for p in timeline["points"] if p["kind"] == "fact")


def test_paid_up_client_leaves_forecast(session, loaded, source_data):
    data = copy.deepcopy(source_data)
    before = service.get_churn_timeline(session)
    rich = max(_customers(session), key=lambda c: c["money"]["atRisk"])
    for a in data["accounts"]:
        if a["id"] == rich["id"]:
            a["balance"] = 10**9
    for s in data["services"]:
        if s["ownerId"] == rich["id"] and s["status"] != "deleted":
            s["autoProlong"] = True
            s["status"] = "active" if s["status"] == "prolong_wait" else s["status"]
    service.replace_snapshot(session, data)
    after = service.get_churn_timeline(session)
    total = lambda t, f: sum(p[f] for p in t["points"] if p["kind"] == "forecast")
    assert total(after, "revenueAtRisk") < total(before, "revenueAtRisk")
    assert total(after, "expectedChurnedClients") < total(before, "expectedChurnedClients")
    assert service.get_customer(session, rich["id"])["money"]["atRisk"] == 0


def test_fact_counts_churned_clients_and_lost_revenue(session, loaded, source_data):
    _clean_history(session)
    data = copy.deepcopy(source_data)
    data["referenceDate"] = "2026-10-20T12:00:00Z"
    owner = data["services"][0]["ownerId"]
    mine = [s for s in data["services"] if s["ownerId"] == owner and s["status"] != "deleted"]
    other = next(s for s in data["services"] if s["ownerId"] != owner and s["status"] != "deleted")
    for s in mine + [other]:
        s["status"] = "deleted"
    service.replace_snapshot(session, data)
    point = {p["date"]: p for p in service.get_churn_timeline(session, days_back=5)["points"]
             if p["kind"] == "fact"}["2026-10-20"]
    assert point["deletions"] == len(mine) + 1
    assert point["churnedClients"] == 2   # две услуги одного клиента — один клиент
    assert point["lostRevenue"] == pytest.approx(sum(s["price"] for s in mine) + other["price"])
