"""Проверка входных данных: снимок источника и настройки модели. Границы — включительно."""
import pytest
from pydantic import ValidationError

from app.risk.schemas import ModelSettings, ModelSettingsPatch, SnapshotIn


def snapshot(account=None, svc=None, tx=None) -> dict:
    return {
        "accounts": [{"id": 1, "email": "a@b.c", **(account or {})}],
        "services": [{"id": 1, "name": "srv-1", "ownerId": 1, "paymentTerm": "month", "autoProlong": True,
                      "createdAt": "2026-10-01T00:00:00Z", "expiresAt": "2026-11-01T00:00:00Z",
                      "status": "active", "price": 500, **(svc or {})}],
        "transactions": [{"id": 1, "amount": 500, "status": "performed", "performedAt": "2026-10-01T00:00:00Z",
                          "createdAt": "2026-10-01T00:00:00Z", "type": "order", "serviceId": 1, **(tx or {})}],
    }


def valid(data) -> bool:
    try:
        SnapshotIn.model_validate(data)
        return True
    except ValidationError:
        return False


def test_minimal_snapshot_is_valid():
    assert valid(snapshot())


@pytest.mark.parametrize("moment,ok", [
    ("1970-01-01T00:00:00Z", True), ("1969-12-31T23:59:59Z", False),
    ("2200-01-01T00:00:00Z", True), ("2200-01-01T00:00:01Z", False),
])
def test_dates_between_1970_and_2200(moment, ok):
    assert valid(snapshot(svc={"createdAt": moment, "expiresAt": moment},
                          tx={"createdAt": moment, "performedAt": moment})) is ok


def test_expiry_not_before_creation():
    assert valid(snapshot(svc={"expiresAt": "2026-10-01T00:00:00Z"}))  # равно — можно
    assert not valid(snapshot(svc={"expiresAt": "2026-09-30T23:59:59Z"}))


def test_transaction_performed_rules():
    assert not valid(snapshot(tx={"performedAt": "2026-09-30T23:59:59Z"}))  # раньше создания
    assert not valid(snapshot(tx={"performedAt": None}))  # performed без даты
    assert not valid(snapshot(tx={"status": "not_performed"}))  # not_performed с датой
    assert valid(snapshot(tx={"status": "not_performed", "performedAt": None}))


@pytest.mark.parametrize("amount,ok", [(0, True), (1000, True), (1001, False), (-1, False)])
def test_transaction_amount_range(amount, ok):
    assert valid(snapshot(tx={"amount": amount})) is ok


@pytest.mark.parametrize("email,ok", [
    ("a@b", True), ("ab", False), ("a@", False), ("no-at-sign", False), ("a b@c.d", False),
    ("x" * 310 + "@test.test", True), ("x" * 311 + "@test.test", False), ("o'neil@example.test", True),
])
def test_email_rules(email, ok):
    assert valid(snapshot(account={"email": email})) is ok


@pytest.mark.parametrize("name,ok", [("srv-1", True), ("", False), ("srv 1", False), ("срв", False)])
def test_service_name_rules(name, ok):
    assert valid(snapshot(svc={"name": name})) is ok


@pytest.mark.parametrize("field", ["balance", "withdrawBalance", "totalReplenished", "bonusBalance"])
def test_money_fields_are_non_negative_and_default_to_zero(field):
    assert not valid(snapshot(account={field: -1}))
    parsed = SnapshotIn.model_validate(snapshot())
    assert getattr(parsed.accounts[0], {"withdrawBalance": "withdraw_balance", "totalReplenished": "total_replenished",
                                        "bonusBalance": "bonus_balance"}.get(field, field)) == 0
    assert parsed.accounts[0].bonus_state == "locked"


def test_ids_start_from_one():
    assert not valid(snapshot(account={"id": 0}))


def test_integrity_errors_list_problems():
    data = snapshot()
    data["accounts"].append({"id": 1, "email": "a@b.c"})
    data["services"].append({**data["services"][0], "id": 2, "ownerId": 99})
    data["transactions"].append({**data["transactions"][0], "id": 2, "serviceId": 77})
    with pytest.raises(ValidationError) as e:
        SnapshotIn.model_validate(data)
    text = str(e.value)
    assert "повторяющиеся id [1]" in text and "повторяющиеся email" in text
    assert "ownerId без аккаунта [99]" in text and "serviceId без услуги [77]" in text


# --- настройки модели ----------------------------------------------------------


def test_default_settings():
    s = ModelSettings()
    assert (s.thresholds.high, s.thresholds.medium, s.horizon_days, s.prolong_wait_grace_days,
            s.new_service_days, s.aggregation, s.as_of_mode) == (60, 30, 30, 5, 30, "max", "snapshot")
    assert s.weights == {"prolong_wait": 45, "expiring_no_autoprolong": 30, "insufficient_funds": 35,
                         "suspended": 10, "churn_history": 15, "unpaid_transactions": 10, "new_service": 5}


@pytest.mark.parametrize("thresholds,ok", [
    ({"high": 60, "medium": 59.99}, True), ({"high": 60, "medium": 60}, False), ({"high": 50, "medium": 60}, False),
    ({"high": 100, "medium": 0}, True), ({"high": 100.01, "medium": 30}, False), ({"high": 60, "medium": -0.01}, False),
])
def test_threshold_rules(thresholds, ok):
    try:
        ModelSettings(thresholds=thresholds)
        assert ok
    except ValidationError:
        assert not ok


@pytest.mark.parametrize("weight,ok", [(0, True), (100, True), (100.01, False), (-0.01, False)])
def test_weight_range(weight, ok):
    try:
        ModelSettings(weights={"suspended": weight})
        assert ok
    except ValidationError:
        assert not ok


@pytest.mark.parametrize("field,low,high", [("horizon_days", 1, 365), ("prolong_wait_grace_days", 0, 60),
                                            ("new_service_days", 1, 365)])
def test_integer_setting_bounds(field, low, high):
    ModelSettings(**{field: low})
    ModelSettings(**{field: high})
    for bad in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            ModelSettings(**{field: bad})


def test_fixed_as_of_needs_a_date():
    with pytest.raises(ValidationError):
        ModelSettings(as_of_mode="fixed")
    assert ModelSettings(as_of_mode="fixed", as_of_fixed="2026-10-01T00:00:00Z").as_of_fixed is not None


def test_patch_changes_only_given_fields():
    current = ModelSettings(weights={"suspended": 20}, horizon_days=14)
    new = ModelSettingsPatch.model_validate({"weights": {"new_service": 7}, "thresholds": {"high": 70}}).apply_to(current)
    assert new.weights["suspended"] == 20 and new.weights["new_service"] == 7
    assert new.thresholds.high == 70 and new.thresholds.medium == 30 and new.horizon_days == 14


def test_patch_can_clear_fixed_date():
    current = ModelSettings(as_of_mode="fixed", as_of_fixed="2026-10-01T00:00:00Z")
    new = ModelSettingsPatch.model_validate({"asOfMode": "snapshot", "asOfFixed": None}).apply_to(current)
    assert new.as_of_mode == "snapshot" and new.as_of_fixed is None
    kept = ModelSettingsPatch.model_validate({"horizonDays": 10}).apply_to(current)
    assert kept.as_of_fixed == current.as_of_fixed


def test_patch_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ModelSettingsPatch.model_validate({"foo": 1})
