from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.risk.domain import AccountData, ServiceData, TxStats
from app.risk.schemas import ModelSettings, ModelSettingsPatch, SnapshotIn
from app.risk.scoring import renewals_in_horizon, score_account

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)


def account(balance=0, bonus=0, bonus_state="locked", account_id=1) -> AccountData:
    return AccountData(account_id, "a@example.test", balance, 0, balance, bonus, bonus_state)


def service(service_id=1, *, status="active", term="month", auto=True, price=500,
            expires_in_days=20.0, age_days=200.0) -> ServiceData:
    return ServiceData(service_id, f"s-{service_id}", 1, term, auto, NOW - timedelta(days=age_days),
                       NOW + timedelta(days=expires_in_days), status, price)


def score(services, acc=None, tx=None, settings=None):
    return score_account(acc or account(), services, tx or {}, settings or ModelSettings(), NOW)


def points(result, feature):
    return {c.feature: c.points for r in result.services for c in r.contributions}.get(feature, 0)


def test_safe_customer_has_low_risk():
    r = score([service(auto=True)], account(balance=10_000))
    assert r.score == 0 and r.level == "low"
    assert r.secured_by_balance == r.horizon_revenue == 500 and r.expected_loss == 0


def test_prolong_wait_without_money_is_high_risk():
    r = score([service(status="prolong_wait", expires_in_days=-1)], account(balance=0))
    assert points(r, "prolong_wait") == 45
    assert points(r, "insufficient_funds") == 25
    assert r.level == "high"
    assert "удалена через 4 дн." in r.reasons[0]["text"]


def test_expiring_without_autoprolong_grows_closer_to_expiry():
    far = score([service(auto=False, expires_in_days=24)], account(balance=10_000))
    near = score([service(auto=False, expires_in_days=3)], account(balance=10_000))
    assert 0 < points(far, "expiring_no_autoprolong") < points(near, "expiring_no_autoprolong") <= 30


def test_no_live_services_means_no_score():
    r = score([service(status="deleted"), service(2, status="blocked")])
    assert r.score is None and r.level == "none" and r.reasons[0]["feature"] == "no_data"
    assert score([]).level == "none"


def test_money_uses_balance_only_for_autoprolong():
    acc = account(balance=300)
    r = score([service(1, auto=True, price=500, expires_in_days=5), service(2, auto=False, price=200, expires_in_days=6)], acc)
    by_id = {s.service.id: s for s in r.services}
    assert by_id[1].secured_by_balance == 300 and by_id[1].at_risk == 200
    assert by_id[2].secured_by_balance == 0 and by_id[2].at_risk == 200
    assert r.expected_loss == pytest.approx(sum(s.probability * s.at_risk for s in r.services))


def test_unlocked_bonus_counts_for_funds_but_not_for_secured_revenue():
    locked = score([service(price=1000)], account(balance=0, bonus=2000, bonus_state="locked"))
    unlocked = score([service(price=1000)], account(balance=0, bonus=2000, bonus_state="unlocked"))
    assert points(locked, "insufficient_funds") == 25 and points(unlocked, "insufficient_funds") == 0
    assert unlocked.secured_by_balance == 0


def test_renewals_in_horizon():
    assert renewals_in_horizon(service(term="hour", expires_in_days=0.5 / 24), 0.5 / 24, 30) == 720
    assert renewals_in_horizon(service(term="month"), 20, 30) == 1
    assert renewals_in_horizon(service(term="year"), 200, 30) == 0
    assert renewals_in_horizon(service(status="prolong_wait"), -3, 30) == 1
    assert renewals_in_horizon(service(status="deleted"), 5, 30) == 0


def test_unpaid_transactions_and_history():
    services = [service(1), service(2, status="deleted")]
    r = score(services, account(balance=10_000), tx={1: TxStats(4, 2, 2)})
    assert points(r, "unpaid_transactions") == 5
    assert points(r, "churn_history") == 7.5


def test_weight_zero_disables_feature_and_thresholds_change_level():
    s = ModelSettingsPatch(weights={"prolong_wait": 0}).apply_to(ModelSettings())
    r = score([service(status="prolong_wait", expires_in_days=-1)], account(balance=10_000), settings=s)
    assert points(r, "prolong_wait") == 0
    strict = ModelSettingsPatch(thresholds={"high": 20, "medium": 10}).apply_to(ModelSettings())
    r = score([service(auto=False, expires_in_days=3)], account(balance=10_000), settings=strict)
    assert r.level == "high"


def test_revenue_weighted_aggregation():
    s = ModelSettingsPatch(aggregation="revenue_weighted").apply_to(ModelSettings())
    services = [service(1, price=900), service(2, status="prolong_wait", price=100, expires_in_days=-1)]
    weighted = score(services, account(balance=100_000), settings=s)
    maximum = score(services, account(balance=100_000))
    assert weighted.score < maximum.score


def test_settings_validation():
    with pytest.raises(ValidationError):
        ModelSettingsPatch(weights={"unknown_feature": 1}).apply_to(ModelSettings())
    with pytest.raises(ValidationError):
        ModelSettingsPatch(thresholds={"medium": 70}).apply_to(ModelSettings())
    with pytest.raises(ValidationError):
        ModelSettingsPatch(as_of_mode="fixed").apply_to(ModelSettings())
    with pytest.raises(ValidationError):
        ModelSettingsPatch.model_validate({"horizonDayz": 10})
    s = ModelSettingsPatch.model_validate({"weights": {"suspended": 50}, "horizonDays": 14}).apply_to(ModelSettings())
    assert s.weights["suspended"] == 50 and s.weights["prolong_wait"] == 45 and s.horizon_days == 14


def test_snapshot_validation_reports_broken_references():
    data = {
        "accounts": [{"id": 1, "email": "a@x.test"}, {"id": 1, "email": "a@x.test"}],
        "services": [{"id": 1, "name": "s", "ownerId": 9, "paymentTerm": "month", "createdAt": "2026-01-01T00:00:00Z",
                      "expiresAt": "2026-02-01T00:00:00Z", "status": "active", "price": 1}],
        "transactions": [{"id": 1, "amount": 1, "createdAt": "2026-01-01T00:00:00Z", "type": "order", "serviceId": 5}],
    }
    with pytest.raises(ValidationError) as err:
        SnapshotIn.model_validate(data)
    text = str(err.value)
    assert "повторяющиеся id" in text and "ownerId без аккаунта" in text and "serviceId без услуги" in text
