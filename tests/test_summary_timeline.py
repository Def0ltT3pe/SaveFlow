"""Сводка и график оттока: каждое число пересчитывается независимо из данных клиентов и услуг."""
import copy
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.risk import service


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
