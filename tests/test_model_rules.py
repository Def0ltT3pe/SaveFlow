"""Правила модели, которые мутационное тестирование нашло непроверенными: признаки, границы уровней,
месячная цена, агрегация, тексты сумм и сроков."""
import pytest

from app.risk.domain import TxStats
from app.risk.fmt import ago, duration, eur, in_time
from app.risk.schemas import ModelSettings
from app.risk.scoring import level_for
from app.risk.terms import PERIOD_DAYS, monthly_price
from tests.test_scoring import account, points, score, service

RICH = account(balance=10**9)


# --- признаки ----------------------------------------------------------------


@pytest.mark.parametrize("days", [0.0, -3.0])
def test_expired_service_without_autoprolong_gets_full_weight(days):
    r = score([service(auto=False, expires_in_days=days)], RICH)
    assert points(r, "expiring_no_autoprolong") == 30
    assert r.reasons[0]["text"] == "Срок истёк, автопродление выключено"


def test_expiring_signal_ends_at_horizon():
    assert points(score([service(auto=False, expires_in_days=30)], RICH), "expiring_no_autoprolong") == 0
    assert points(score([service(auto=False, expires_in_days=31)], RICH), "expiring_no_autoprolong") == 0
    assert points(score([service(auto=False, expires_in_days=15)], RICH), "expiring_no_autoprolong") == 15


def test_expiring_signal_ignores_prolong_wait_and_autoprolong():
    assert points(score([service(auto=True, expires_in_days=2)], RICH), "expiring_no_autoprolong") == 0
    waiting = score([service(status="prolong_wait", auto=False, expires_in_days=-1)], RICH)
    assert points(waiting, "expiring_no_autoprolong") == 0 and points(waiting, "prolong_wait") == 45


def test_suspended_server_adds_its_weight():
    r = score([service(status="suspended")], RICH)
    assert points(r, "suspended") == 10 and r.score == 10
    assert points(score([service()], RICH), "suspended") == 0


@pytest.mark.parametrize("age,expected", [(0, 5.0), (3, 4.5), (15, 2.5), (29.999, 0.0), (30, 0), (200, 0)])
def test_new_service_signal_fades_over_window(age, expected):
    assert points(score([service(age_days=age)], RICH), "new_service") == pytest.approx(expected, abs=0.01)


def test_prolong_wait_text_depends_on_grace_period():
    soon = score([service(status="prolong_wait", expires_in_days=-1)], RICH)
    late = score([service(status="prolong_wait", expires_in_days=-6)], RICH)
    assert "удалена через 4 дн." in soon.reasons[0]["text"]
    assert late.reasons[0]["text"] == "Ожидает оплаты дольше льготного периода: удаление в любой момент"
    assert points(late, "prolong_wait") == 45


def test_unpaid_transactions_share():
    r = score([service()], RICH, tx={1: TxStats(4, 1, 3, None)})
    assert points(r, "unpaid_transactions") == 7.5


def test_churn_history_is_account_level_reason_with_counts():
    r = score([service(1), service(2, status="deleted"), service(3, status="blocked")], RICH)
    reason = next(x for x in r.reasons if x["feature"] == "churn_history")
    assert reason["points"] == 10 and reason["serviceId"] is None and reason["services"] == 0


def test_reason_counts_services_with_same_signal():
    r = score([service(1, status="suspended"), service(2, status="suspended")], RICH)
    reason = next(x for x in r.reasons if x["feature"] == "suspended")
    assert reason["services"] == 2


def test_zero_weight_switches_feature_off():
    settings = ModelSettings(weights={"suspended": 0})
    r = score([service(status="suspended")], RICH, settings=settings)
    assert points(r, "suspended") == 0 and r.score == 0


def test_score_is_capped_at_100():
    settings = ModelSettings(weights={"prolong_wait": 100, "insufficient_funds": 100})
    r = score([service(status="prolong_wait", expires_in_days=-1)], account(balance=0), settings=settings)
    assert r.score == 100 and r.services[0].probability == 1.0


# --- уровни и агрегация ------------------------------------------------------


@pytest.mark.parametrize("value,level", [
    (None, "none"), (0, "low"), (29.99, "low"), (30, "medium"), (59.99, "medium"), (60, "high"), (100, "high"),
])
def test_level_boundaries_are_inclusive(value, level):
    assert level_for(value, ModelSettings()) == level


def test_custom_thresholds_are_used():
    settings = ModelSettings(thresholds={"high": 80, "medium": 10})
    assert [level_for(v, settings) for v in (9.99, 10, 79.99, 80)] == ["low", "medium", "medium", "high"]


def test_client_score_is_max_of_services_by_default():
    r = score([service(1, status="suspended"), service(2, status="prolong_wait", expires_in_days=-1)], RICH)
    assert r.score == 45


def test_revenue_weighted_aggregation():
    settings = ModelSettings(aggregation="revenue_weighted")
    # suspended: 10 баллов, цена 300; prolong_wait: 45 баллов, цена 100 → (10·300 + 45·100) / 400
    r = score([service(1, status="suspended", price=300),
               service(2, status="prolong_wait", price=100, expires_in_days=-1)], RICH, settings=settings)
    assert r.score == pytest.approx((10 * 300 + 45 * 100) / 400, abs=0.01)


def test_revenue_weighted_with_free_services_is_plain_average():
    settings = ModelSettings(aggregation="revenue_weighted")
    r = score([service(1, status="suspended", price=0),
               service(2, status="prolong_wait", price=0, expires_in_days=-1)], RICH, settings=settings)
    assert r.score == pytest.approx((10 + 45) / 2, abs=0.01)


# --- деньги ------------------------------------------------------------------


@pytest.mark.parametrize("term,price,expected", [
    ("month", 1000, 1000.0),
    ("week", 700, 700 * 30.4375 / 7),
    ("quarter_year", 3000, 1000.0),
    ("half_year", 6000, 1000.0),
    ("year", 12000, 1000.0),
    ("hour", 1, 30.4375 * 24),
])
def test_monthly_price(term, price, expected):
    assert monthly_price(price, term) == pytest.approx(expected)


def test_period_lengths():
    assert PERIOD_DAYS == {"hour": 1 / 24, "week": 7.0, "month": 30.4375, "quarter_year": 91.3125,
                           "half_year": 182.625, "year": 365.25}


def test_expected_monthly_loss_uses_client_probability_and_monthly_price():
    r = score([service(status="suspended", term="year", price=12000)], account(balance=10**9))
    # score 10 → вероятность 0.1; цена в месяц €10
    assert r.monthly_revenue == pytest.approx(1000)
    assert r.expected_monthly_loss == pytest.approx(100)


def test_deleted_service_carries_no_money_and_no_score():
    r = score([service(1), service(2, status="deleted", price=999)], RICH)
    gone = next(s for s in r.services if s.service.id == 2)
    assert gone.scored is False and gone.score is None and gone.probability is None
    assert (gone.horizon_revenue, gone.secured_by_balance, gone.covered_by_bonus, gone.at_risk,
            gone.expected_loss, gone.expected_monthly_loss) == (0, 0, 0, 0, 0, 0)
    assert r.live_services == 1 and r.horizon_revenue == 500


def test_service_created_exactly_at_reference_date_is_scored():
    r = score([service(age_days=0)], RICH)
    assert r.live_services == 1
    later = score([service(age_days=-1)], RICH)
    assert later.live_services == 0 and later.level == "none"


def test_account_money_is_sum_of_services():
    acc = account(balance=700)
    r = score([service(1, price=500, expires_in_days=2), service(2, price=400, expires_in_days=5),
               service(3, auto=False, price=300, expires_in_days=7)], acc)
    for field in ("horizon_revenue", "secured_by_balance", "covered_by_bonus", "at_risk", "expected_loss",
                  "expected_monthly_loss", "monthly_price"):
        total = sum(getattr(s, field) for s in r.services)
        assert getattr(r, "monthly_revenue" if field == "monthly_price" else field) == pytest.approx(total, abs=0.01)
    assert r.secured_by_balance == 500 and r.at_risk == 700


# --- тексты ------------------------------------------------------------------


def test_eur_formatting():
    nbsp = " "
    assert eur(0) == f"0,00{nbsp}€"
    assert eur(123456) == f"1{nbsp}234,56{nbsp}€"
    assert eur(123456789) == f"1{nbsp}234{nbsp}567,89{nbsp}€"


@pytest.mark.parametrize("days,text", [(0.5 / 24, "менее часа"), (5 / 24, "5 ч"), (0.99, "24 ч"), (3, "3 дн.")])
def test_duration(days, text):
    assert duration(days) == text


def test_in_time_and_ago():
    assert in_time(0.5 / 24) == "менее чем через час"
    assert in_time(2) == "через 2 дн."
    assert ago(3 / 24) == "3 ч назад"
    assert ago(10) == "10 дн. назад"
