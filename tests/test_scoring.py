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
    assert points(r, "insufficient_funds") == 35  # не хватает уже на ближайшее продление
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
    # Автопродление не оплачивает продление частично: 3 € на балансе не покрывают продление за 5 €
    assert by_id[1].secured_by_balance == 0 and by_id[1].at_risk == 500
    assert by_id[2].secured_by_balance == 0 and by_id[2].at_risk == 200
    # Деньги — по вероятности ухода клиента, как в объяснении на панели
    assert r.expected_loss == pytest.approx(r.score / 100 * r.at_risk, abs=0.01)


def test_unlocked_bonus_pays_renewal_and_is_not_at_risk():
    locked = score([service(price=1000)], account(balance=0, bonus=2000, bonus_state="locked"))
    unlocked = score([service(price=1000)], account(balance=0, bonus=2000, bonus_state="unlocked"))
    assert points(locked, "insufficient_funds") > 0 and points(unlocked, "insufficient_funds") == 0
    assert unlocked.secured_by_balance == 0 and unlocked.covered_by_bonus == 1000
    assert unlocked.at_risk == 0 and unlocked.expected_loss == 0
    assert locked.at_risk == 1000


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


# --- Находки ревью -----------------------------------------------------------------


def test_prolong_wait_is_never_secured_by_balance():
    r = score([service(status="prolong_wait", auto=True, price=900, expires_in_days=-2)], account(balance=10_000))
    s = r.services[0]
    assert s.secured_by_balance == 0 and s.at_risk == s.horizon_revenue > 0
    assert r.expected_loss > 0


def test_services_created_after_as_of_are_ignored():
    future = ServiceData(1, "s-1", 1, "month", False, NOW + timedelta(days=10), NOW + timedelta(days=40), "active", 500)
    r = score([future], account(balance=0))
    assert r.score is None and r.level == "none" and r.horizon_revenue == 0


def test_prolong_wait_with_future_expiry_is_due_now():
    r = score([service(status="prolong_wait", price=500, expires_in_days=10)], account(balance=0))
    s = r.services[0]
    assert s.renewals_in_horizon >= 1 and s.horizon_revenue >= 500
    assert "удалена через 5 дн." in r.reasons[0]["text"]


def test_reason_text_for_less_than_an_hour():
    r = score([service(term="hour", auto=False, price=2, expires_in_days=0.25 / 24)], account(balance=10_000))
    texts = [c.text for c in r.services[0].contributions]
    assert any(t.startswith("Истекает менее чем через час") for t in texts), texts
    assert not any("через менее" in t for t in texts)


@pytest.mark.parametrize("stored,check", [
    ({"horizonDays": 0, "weights": {"suspended": 50}}, lambda s: s.horizon_days == 30 and s.weights["suspended"] == 50),
    ({"asOfMode": "fixed"}, lambda s: s.as_of_mode == "snapshot"),
    ({"asOfMode": "fixed", "asOfFixed": "9999-01-01T00:00:00Z"}, lambda s: s.as_of_mode == "snapshot"),
    ({"thresholds": {"high": 20, "medium": 50}, "horizonDays": 14}, lambda s: s.thresholds.high == 60 and s.horizon_days == 14),
    ({"weights": {"suspended": "x", "prolong_wait": 500, "new_service": True, "gone": 3, "churn_history": 1}},
     lambda s: s.weights["suspended"] == 10 and s.weights["prolong_wait"] == 45 and s.weights["churn_history"] == 1),
    ("garbage", lambda s: s == ModelSettings()),
    (None, lambda s: s == ModelSettings()),
])
def test_stored_settings_fall_back_field_by_field(stored, check):
    from app.risk.store import settings_from_stored

    assert check(settings_from_stored(stored))


# --- Деньги и нехватка средств -------------------------------------------------------


def test_expected_loss_matches_explanation_formula():
    services = [service(1, price=900, expires_in_days=3, auto=False),
                service(2, status="prolong_wait", price=200, expires_in_days=-1)]
    r = score(services, account(balance=100))
    assert r.expected_loss == pytest.approx(r.score / 100 * r.at_risk, abs=0.01)
    assert r.expected_monthly_loss == pytest.approx(r.score / 100 * r.monthly_revenue, abs=0.01)


def test_money_running_out_alone_gives_medium_risk():
    r = score([service(price=800, expires_in_days=2)], account(balance=100))  # на ближайшее продление не хватает
    assert points(r, "insufficient_funds") >= 30 and r.level == "medium"
    assert "не хватит на ближайшее продление через 2 дн." in r.reasons[0]["text"]
    now = score([service(status="prolong_wait", price=800, expires_in_days=-1)], account(balance=100))
    assert points(now, "insufficient_funds") == 35
    assert any("нужно оплатить сейчас" in x["text"] for x in now.reasons)


def test_sooner_money_runs_out_stronger_signal():
    hourly = lambda bal: score([service(term="hour", price=10, expires_in_days=0.5 / 24)], account(balance=bal))
    two_days, ten_days, enough = hourly(10 * 24 * 2), hourly(10 * 24 * 10), hourly(10 * 24 * 31)
    assert points(two_days, "insufficient_funds") > points(ten_days, "insufficient_funds") > 0
    assert points(enough, "insufficient_funds") == 0
    assert points(two_days, "insufficient_funds") == pytest.approx(35 * (1 - 0.5 * 2 / 30), abs=0.3)
    late = score([service(price=800, expires_in_days=29)], account(balance=0))
    assert points(late, "insufficient_funds") >= 35 * 0.5
    assert "кончатся через 2 дн." in two_days.reasons[0]["text"]


def test_hourly_service_running_out_has_meaningful_expected_loss():
    r = score([service(term="hour", price=10, expires_in_days=0.5 / 24)], account(balance=10 * 24 * 2))
    s = r.services[0]
    assert s.secured_by_balance == 480 and s.at_risk == s.horizon_revenue - 480
    assert r.expected_loss / r.at_risk >= 0.3  # раньше было 2–7 %


def test_funds_text_separates_balance_and_bonus():
    unlocked = score([service(price=5000, expires_in_days=2)], account(balance=200, bonus=3000, bonus_state="unlocked"))
    locked = score([service(price=5000, expires_in_days=2)], account(balance=200, bonus=3000, bonus_state="locked"))
    assert "на балансе 2,00\u00a0€ и бонусах 30,00\u00a0€" in unlocked.reasons[0]["text"]
    assert "на балансе 2,00\u00a0€ (бонусы 30,00\u00a0€ заблокированы)" in locked.reasons[0]["text"]
    assert "32,00" not in unlocked.reasons[0]["text"]


@pytest.mark.parametrize("cents,text", [
    (0, "0,00 €"),
    (544_66, "544,66 €"),
    (108_111, "1 081,11 €"),
    (123_456_789, "1 234 567,89 €"),
])
def test_money_text_matches_panel_format(cents, text):
    from app.risk.fmt import eur

    # Как Intl.NumberFormat('ru-RU', EUR) на фронте; пробелы неразрывные
    assert eur(cents) == text.replace(" ", "\u00a0")


# --- Видимые противоречия на панели ---------------------------------------------------


def test_money_and_risk_agree_for_mixed_services():
    # Как big-corp: почасовая услуга с ближайшим списанием и месячная, которую продлевать через 10 дней.
    gpu = service(1, term="hour", price=3, expires_in_days=40 / 60 / 24)
    web = service(2, term="month", price=1000, expires_in_days=10)
    r = score([gpu, web], account(balance=2500))
    by_id = {s.service.id: s for s in r.services}
    assert by_id[2].at_risk == 0, "месячную продлят с баланса: денег на неё хватает к 10-му дню"
    assert by_id[1].at_risk > 0, "почасовая останавливается, когда деньги кончатся"
    funds = {c.feature: c for c in by_id[1].contributions}.get("insufficient_funds")
    assert funds is not None and "кончатся через" in funds.text
    # Сумма «гарантировано» ровно равна тому, что успеют списать до конца денег
    assert by_id[1].secured_by_balance + by_id[2].secured_by_balance <= 2500


def test_bonus_pays_after_balance_runs_out():
    hourly = service(term="hour", price=10, expires_in_days=0.5 / 24)
    r = score([hourly], account(balance=10 * 24 * 2, bonus=10 * 24 * 3, bonus_state="unlocked"))
    s = r.services[0]
    assert s.secured_by_balance == 480 and s.covered_by_bonus == 720
    assert s.at_risk == s.horizon_revenue - 1200


def test_account_level_reason_is_not_attached_to_a_service():
    r = score([service(1, auto=False, expires_in_days=5), service(2, status="deleted")], account(balance=10_000))
    history = next(x for x in r.reasons if x["feature"] == "churn_history")
    assert history["serviceId"] is None and history["serviceName"] is None and history["services"] == 0
    expiring = next(x for x in r.reasons if x["feature"] == "expiring_no_autoprolong")
    assert expiring["serviceName"] == "s-1"


# --- Оплата продления частью с баланса, частью бонусами --------------------------------


@pytest.mark.parametrize("services,acc,secured,bonus,at_risk", [
    ([service(price=1000, expires_in_days=5)], account(balance=300, bonus=2000, bonus_state="unlocked"), 300, 700, 0),
    ([service(term="hour", price=10, expires_in_days=0.5 / 24)], account(balance=485, bonus=240, bonus_state="unlocked"),
     485, 235, 7200 - 720),
    ([service(price=1000, expires_in_days=5)], account(balance=300, bonus=500, bonus_state="unlocked"), 0, 0, 1000),
    ([service(1, price=600, expires_in_days=5), service(2, price=400, expires_in_days=5)],
     account(balance=500, bonus=1000, bonus_state="unlocked"), 500, 500, 0),
])
def test_renewal_paid_from_balance_remainder_and_bonus(services, acc, secured, bonus, at_risk):
    r = score(services, acc)
    assert (r.secured_by_balance, r.covered_by_bonus, r.at_risk) == (secured, bonus, at_risk)


# --- Продления в один и тот же момент -------------------------------------------------


SAME_MOMENT = 5.0
LATER = SAME_MOMENT + 30 / 86400  # на 30 секунд позже


@pytest.mark.parametrize("services,acc,paid", [
    # денег на одно из двух: одно оплачено, а не оба под угрозой
    ([service(1, price=500, expires_in_days=SAME_MOMENT), service(2, price=500, expires_in_days=SAME_MOMENT)],
     account(balance=500), {1: (500, 0), 2: (0, 0)}),
    # не хватает на первую по очереди — оплачивается та, на которую хватает
    ([service(1, price=600, expires_in_days=SAME_MOMENT), service(2, price=400, expires_in_days=SAME_MOMENT)],
     account(balance=400), {1: (0, 0), 2: (400, 0)}),
    # оплата остатком баланса и бонусами
    ([service(1, price=500, expires_in_days=SAME_MOMENT), service(2, price=500, expires_in_days=SAME_MOMENT)],
     account(balance=300, bonus=300, bonus_state="unlocked"), {1: (300, 200), 2: (0, 0)}),
    # две почасовые с одним сроком: 30 пар оплачены, на 31-й час денег на одну
    ([service(1, term="hour", price=10, expires_in_days=0.5 / 24), service(2, term="hour", price=10, expires_in_days=0.5 / 24)],
     account(balance=610), {1: (310, 0), 2: (300, 0)}),
])
def test_renewals_at_the_same_moment_are_paid_one_by_one(services, acc, paid):
    r = score(services, acc)
    assert {s.service.id: (s.secured_by_balance, s.covered_by_bonus) for s in r.services} == paid
    assert r.at_risk == r.horizon_revenue - sum(a + b for a, b in paid.values())


def test_same_moment_is_counted_like_a_few_seconds_apart():
    same = score([service(1, expires_in_days=SAME_MOMENT), service(2, expires_in_days=SAME_MOMENT)], account(balance=500))
    apart = score([service(1, expires_in_days=SAME_MOMENT), service(2, expires_in_days=LATER)], account(balance=500))
    for r in (same, apart):
        signal = {s.service.id: any(c.feature == "insufficient_funds" for c in s.contributions) for s in r.services}
        # «Не хватает средств» — только у неоплаченной услуги
        assert signal == {1: False, 2: True}
    assert (same.secured_by_balance, same.at_risk, same.score) == (apart.secured_by_balance, apart.at_risk, apart.score)


def test_balance_is_spent_before_bonus_for_every_demo_customer():
    import collections
    import json
    from pathlib import Path

    from app.risk.domain import AccountData, ServiceData, TxStats
    from app.risk.schemas import SnapshotIn
    from app.risk.scoring import score_portfolio

    root = Path(__file__).resolve().parent.parent
    for name in ("demo_day0.json", "demo_day3.json", "showcase.json"):
        path = root / "demo" / name
        if not path.exists():
            continue
        snap = SnapshotIn.model_validate(json.loads(path.read_text(encoding="utf-8")))
        accounts = [AccountData(a.id, a.email, a.balance, a.withdraw_balance, a.total_replenished, a.bonus_balance,
                                a.bonus_state) for a in snap.accounts]
        services = [ServiceData(s.id, s.name, s.owner_id, s.payment_term, s.auto_prolong, s.created_at, s.expires_at,
                                s.status, s.price) for s in snap.services]
        for r in score_portfolio(accounts, services, collections.defaultdict(TxStats), ModelSettings(),
                                 snap.reference_date or snap.latest_timestamp()):
            paid = r.secured_by_balance + r.covered_by_bonus
            assert r.secured_by_balance == min(r.account.balance, paid), r.account.email   # баланс — первым и до конца
            assert r.covered_by_bonus <= r.account.usable_bonus, r.account.email
            for s in r.services:
                if s.scored and s.service.price:
                    assert (s.secured_by_balance + s.covered_by_bonus) % s.service.price == 0  # только целые продления
