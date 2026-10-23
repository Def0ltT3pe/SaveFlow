import copy
import json

import psycopg
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text

from app.db.models import Account, AccountRisk, ServiceStatusChange
from app.risk import service, store
from app.risk.schemas import (
    CustomerDetail,
    CustomerPage,
    ModelSettings,
    ModelSettingsOut,
    RiskSummary,
    SnapshotResult,
)


@pytest.fixture
def loaded(session, source_data):
    store.save_settings(session, ModelSettings())
    session.commit()
    return service.replace_snapshot(session, source_data)


def test_snapshot_loads_and_scores(session, loaded):
    SnapshotResult.model_validate(loaded)
    assert (loaded["accounts"], loaded["services"], loaded["transactions"]) == (10, 30, 100)
    assert session.scalar(select(func.count()).select_from(AccountRisk)) == 10
    summary = service.get_summary(session)
    RiskSummary.model_validate(summary)
    levels = summary["accountsByLevel"]
    assert sum(levels.values()) == 10 and levels["none"] == 4
    assert summary["money"]["expectedLoss"] > 0
    assert summary["run"]["asOf"].startswith("2026-10-19")


def test_snapshot_replaces_everything_and_logs_status_changes(session, loaded, source_data):
    data = copy.deepcopy(source_data)
    data["accounts"] = data["accounts"][:5]
    data["services"] = [s for s in data["services"] if s["ownerId"] <= 5]
    ids = {s["id"] for s in data["services"]}
    data["transactions"] = [t for t in data["transactions"] if t["serviceId"] in ids]
    data["services"][0]["status"] = "deleted"
    result = service.replace_snapshot(session, data)
    assert result["accounts"] == 5
    assert session.scalar(select(func.count()).select_from(Account)) == 5
    changes = session.scalars(select(ServiceStatusChange).where(ServiceStatusChange.snapshot_id == result["snapshotId"])).all()
    assert any(c.old_status == "active" and c.new_status == "deleted" for c in changes)
    assert any(c.new_status is None for c in changes)  # услуги клиентов 6..10 пропали
    # Идентификаторы продолжаются после загруженных
    next_id = session.execute(text("SELECT nextval(pg_get_serial_sequence('public.accounts', 'id'))")).scalar_one()
    session.rollback()
    assert next_id == 6


def test_failed_snapshot_keeps_previous_data(session, loaded, source_data, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("сбой посреди загрузки")

    monkeypatch.setattr(store, "write_results", boom)
    data = copy.deepcopy(source_data)
    data["accounts"] = data["accounts"][:1]
    data["services"], data["transactions"] = [], []
    with pytest.raises(RuntimeError):
        service.replace_snapshot(session, data)
    assert session.scalar(select(func.count()).select_from(Account)) == 10
    assert session.scalar(select(func.count()).select_from(AccountRisk)) == 10


def test_invalid_snapshot_is_rejected_before_db(session, loaded, source_data):
    data = copy.deepcopy(source_data)
    data["services"][0]["ownerId"] = 999
    with pytest.raises(ValidationError):
        service.replace_snapshot(session, data)
    assert session.scalar(select(func.count()).select_from(Account)) == 10


@pytest.mark.parametrize("sort,key", [("id", "id"), ("email", "email"), ("balance", "balance"),
                                      ("totalReplenished", "totalReplenished")])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_customer_sorting(session, loaded, sort, key, order):
    page = service.get_customers(session, sort=sort, order=order, limit=100)
    CustomerPage.model_validate(page)
    values = [item[key] for item in page["items"]]
    assert values == sorted(values, reverse=order == "desc") and len(values) == 10


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_sort_by_score_puts_unscored_last(session, loaded, order):
    items = service.get_customers(session, sort="score", order=order)["items"]
    scores = [i["score"] for i in items]
    scored = [s for s in scores if s is not None]
    assert scores[: len(scored)] == sorted(scored, reverse=order == "desc")
    assert all(s is None for s in scores[len(scored):])


def test_customer_filters_and_pagination(session, loaded):
    page = service.get_customers(session, levels=["none"])
    assert page["total"] == 4 and all(i["level"] == "none" and i["mainReason"] for i in page["items"])
    assert service.get_customers(session, search="0005")["total"] == 1
    assert len(service.get_customers(session, limit=3, offset=9)["items"]) == 1
    with pytest.raises(ValueError):
        service.get_customers(session, sort="password")


def test_customer_detail(session, loaded):
    detail = service.get_customer(session, 5)
    CustomerDetail.model_validate(detail)
    assert detail["level"] == "high" and len(detail["services"]) == 3
    top = detail["services"][0]
    assert top["scored"] and top["contributions"] and top["transactions"]["total"] > 0
    assert sum(c["points"] for c in top["contributions"]) == pytest.approx(top["score"], abs=0.05)
    assert service.get_customer(session, 2)["score"] is None
    assert service.get_customer(session, 12345) is None


def test_settings_change_recalculates_and_persists(engine, session, loaded):
    before = service.get_customer(session, 5)["score"]
    out = service.update_settings(session, {"weights": {"prolong_wait": 10}})
    ModelSettingsOut.model_validate(out)
    assert out["settings"]["weights"]["prolong_wait"] == 10
    assert service.get_customer(session, 5)["score"] == pytest.approx(before - 35)
    assert service.get_summary(session)["run"]["reason"] == "settings"
    # «Перезапуск»: новое подключение видит сохранённые настройки
    engine.dispose()
    with engine.connect() as conn:
        saved = conn.execute(text("SELECT data FROM model_settings")).scalar_one()
    assert saved["weights"]["prolong_wait"] == 10


def test_notify_is_sent_after_commit(engine, session, loaded):
    url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as listener:
        listener.execute("LISTEN risk_updated")
        service.recalculate(session)
        events = list(listener.notifies(timeout=5, stop_after=1))
    assert events and json.loads(events[0].payload)["event"] == "manual"


def test_metrics_match_summary(session, loaded):
    summary = service.get_summary(session)
    metrics = service.render_metrics(session)
    assert f'churn_customers{{level="high"}} {summary["accountsByLevel"]["high"]}' in metrics
    loss = round(summary["money"]["expectedLoss"] / 100, 2)
    assert f'churn_expected_loss_eur{{horizon_days="30"}} {loss}' in metrics


def test_timeline_separates_fact_and_forecast(session, loaded):
    timeline = service.get_churn_timeline(session)
    kinds = {p["kind"] for p in timeline["points"]}
    assert kinds == {"fact", "forecast"}
    assert sum(p["dueServices"] for p in timeline["points"]) > 0
