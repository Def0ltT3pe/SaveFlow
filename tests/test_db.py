import copy
import json
import threading
import time

import psycopg
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Account, AccountRisk, ServiceStatusChange
from app.risk import service, store
from app.risk.schemas import CustomerDetail, CustomerPage, ModelSettingsOut, RiskSummary, SnapshotResult


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


def test_reading_does_not_wait_for_snapshot_upload(engine, session, loaded, source_data, monkeypatch):
    original = store.write_results
    in_upload = threading.Event()

    def slow_write(*args, **kwargs):
        in_upload.set()
        time.sleep(2)  # загрузка идёт, транзакция открыта
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "write_results", slow_write)
    data = copy.deepcopy(source_data)
    data["accounts"] = data["accounts"][:5]
    data["services"] = [s for s in data["services"] if s["ownerId"] <= 5]
    ids = {s["id"] for s in data["services"]}
    data["transactions"] = [t for t in data["transactions"] if t["serviceId"] in ids]
    upload = threading.Thread(target=service.replace_snapshot, args=(session, data))
    upload.start()
    assert in_upload.wait(10)

    with sessionmaker(bind=engine)() as reader:
        started = time.perf_counter()
        page = service.get_customers(reader, limit=100)
        elapsed = time.perf_counter() - started
    upload.join()
    assert elapsed < 1, f"чтение ждало загрузку снимка {elapsed:.1f} с"
    assert page["total"] == 10  # до COMMIT виден прежний снимок
    with sessionmaker(bind=engine)() as reader:
        assert service.get_customers(reader)["total"] == 5


def _slow_writes(monkeypatch, seconds=2):
    original = store.write_results
    started = threading.Event()

    def slow_write(*args, **kwargs):
        started.set()
        time.sleep(seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "write_results", slow_write)
    return started


def _final_state(engine):
    with sessionmaker(bind=engine)() as s:
        return service.get_settings(s), service.get_summary(s)["run"]


def test_settings_patch_does_not_wait_for_running_recalc(engine, session, loaded, monkeypatch):
    in_recalc = _slow_writes(monkeypatch)
    first = threading.Thread(target=service.update_settings, args=(session, {"weights": {"suspended": 20}}))
    first.start()
    assert in_recalc.wait(10)
    with sessionmaker(bind=engine)() as other:
        started = time.perf_counter()
        service.update_settings(other, {"weights": {"prolong_wait": 10}})
        elapsed = time.perf_counter() - started
    first.join()
    assert elapsed < 1, f"PATCH ждал чужой пересчёт {elapsed:.1f} с"
    settings, run = _final_state(engine)
    assert settings["settings"]["weights"]["suspended"] == 20
    assert settings["settings"]["weights"]["prolong_wait"] == 10
    assert run["settingsVersion"] == settings["version"]  # последнее изменение посчитано


def test_parallel_patches_keep_both_changes(engine, loaded):
    barrier = threading.Barrier(2)

    def patch(weights):
        with sessionmaker(bind=engine)() as s:
            barrier.wait()
            service.update_settings(s, {"weights": weights})

    threads = [threading.Thread(target=patch, args=(w,)) for w in ({"suspended": 33}, {"new_service": 7})]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    settings, run = _final_state(engine)
    assert settings["settings"]["weights"]["suspended"] == 33
    assert settings["settings"]["weights"]["new_service"] == 7
    assert run["settingsVersion"] == settings["version"]


def test_patch_during_snapshot_upload_is_not_lost(engine, session, loaded, source_data, monkeypatch):
    in_upload = _slow_writes(monkeypatch)
    upload = threading.Thread(target=service.replace_snapshot, args=(session, source_data))
    upload.start()
    assert in_upload.wait(10)
    with sessionmaker(bind=engine)() as other:
        service.update_settings(other, {"weights": {"suspended": 44}})
    upload.join()
    settings, run = _final_state(engine)
    assert settings["settings"]["weights"]["suspended"] == 44
    assert run["settingsVersion"] == settings["version"]


def test_patch_while_recalc_checks_versions_is_not_lost(engine, session, loaded, monkeypatch):
    # Пересчёт уже сверил версии (всё актуально), но ещё держит блокировку. В этот момент другой
    # запрос сохраняет настройки и не может взять блокировку — пересчитать должен тот, кто её держит.
    original = store.settings_version
    injected = []

    def settings_version_with_concurrent_patch(s):
        version = original(s)
        if not injected:
            injected.append(True)
            with sessionmaker(bind=engine)() as other:
                service.update_settings(other, {"weights": {"suspended": 55}})
        return version

    monkeypatch.setattr(store, "settings_version", settings_version_with_concurrent_patch)
    service.recalculate(session)
    assert injected
    settings, run = _final_state(engine)
    assert settings["settings"]["weights"]["suspended"] == 55
    assert run["settingsVersion"] == settings["version"]


@pytest.mark.parametrize("call", [
    lambda s: service.get_customers(s, limit=500),
    lambda s: service.get_customer(s, 5),
    lambda s: service.get_customer(s, 999),
    service.get_summary,
    service.get_settings,
    service.render_metrics,
    service.get_churn_timeline,
    lambda s: service.update_settings(s, {"weights": {"suspended": 11}}),
], ids=["customers", "customer", "customer_missing", "summary", "settings", "metrics", "timeline", "patch"])
def test_functions_return_connection_before_response_is_sent(engine, loaded, call):
    # Сессия ещё открыта, как в FastAPI, пока ответ уходит клиенту по медленной сети.
    with sessionmaker(bind=engine)() as s:
        call(s)
        assert engine.pool.checkedout() == 0


def test_expensive_hourly_service_does_not_overflow_money(session, loaded, source_data):
    data = copy.deepcopy(source_data)
    hourly = next(s for s in data["services"] if s["paymentTerm"] == "hour" and s["status"] == "active")
    hourly["price"] = 10**10
    service.replace_snapshot(session, data)
    detail = service.get_customer(session, hourly["ownerId"])
    assert detail["money"]["horizonRevenue"] >= 10**10 * 700


@pytest.mark.parametrize("field,value", [
    ("referenceDate", "9999-12-31T00:00:00Z"),
    ("referenceDate", "0001-01-02T00:00:00Z"),
])
def test_absurd_dates_are_rejected_on_input(session, loaded, source_data, field, value):
    data = copy.deepcopy(source_data)
    data[field] = value
    with pytest.raises(ValidationError):
        service.replace_snapshot(session, data)
    data = copy.deepcopy(source_data)
    data["services"][0]["expiresAt"] = "9999-12-31T00:00:00Z"
    with pytest.raises(ValidationError):
        service.replace_snapshot(session, data)
    with pytest.raises(ValidationError):
        service.update_settings(session, {"asOfMode": "fixed", "asOfFixed": value})
    assert service.get_churn_timeline(session)["points"]


def test_broken_stored_settings_do_not_brick_api(session, loaded):
    session.execute(text("UPDATE model_settings SET data = jsonb_set(data, '{horizonDays}', '0') WHERE id = 1"))
    session.commit()
    assert service.get_settings(session)["settings"]["horizonDays"] == 30
    assert service.get_summary(session)["horizonDays"] == 30
    out = service.update_settings(session, {"horizonDays": 14})  # починка через PATCH работает
    assert out["settings"]["horizonDays"] == 14


def test_failed_recalculation_reverts_patch(session, loaded, monkeypatch):
    before = service.get_settings(session)
    monkeypatch.setattr(store, "write_results", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сбой пересчёта")))
    with pytest.raises(RuntimeError):
        service.update_settings(session, {"weights": {"suspended": 77}})
    after = service.get_settings(session)
    assert after["settings"]["weights"]["suspended"] == before["settings"]["weights"]["suspended"]
    assert after["version"] > before["version"]  # откат — новая версия, клиенты видят изменение


def test_summary_and_timeline_use_settings_of_latest_run(session, loaded):
    current, _, _ = store.load_settings(session, for_update=True)
    store.save_settings(session, current.model_copy(update={"horizon_days": 7}))  # сохранено, но ещё не пересчитано
    session.commit()
    summary = service.get_summary(session)
    assert summary["horizonDays"] == 30 and "за 30 дн." in summary["explanation"]
    forecast = [p for p in service.get_churn_timeline(session)["points"] if p["kind"] == "forecast"]
    assert len(forecast) == 31


def test_timeline_counts_each_deletion_once(session, loaded, source_data):
    def snapshot(status, day):
        data = copy.deepcopy(source_data)
        data["referenceDate"] = f"2026-10-{day:02d}T12:00:00Z"
        data["services"][0]["status"] = status
        return data

    for status, day in (("deleted", 19), ("active", 20), ("deleted", 21)):
        service.replace_snapshot(session, snapshot(status, day))
    points = service.get_churn_timeline(session, days_back=5)["points"]
    sid = source_data["services"][0]["id"]
    history = session.execute(text('SELECT count(*) FROM service_status_history WHERE "serviceId" = :s '
                                   "AND \"newStatus\" = 'deleted'"), {"s": sid}).scalar_one()
    assert history >= 2  # в истории два перехода в deleted
    by_day = {p["date"]: p["deletions"] for p in points if p["kind"] == "fact"}
    assert by_day.get("2026-10-21", 0) == 1 and by_day.get("2026-10-19", 0) == 0


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
