"""HTTP-уровень: эндпоинты ТЗ, /metrics, живое обновление и обвязка main.py."""
import asyncio
import json

import pytest
from sqlalchemy.exc import OperationalError

from app.api import events
from app.risk import service
from tests.test_auth_sessions import api, login  # noqa: F401  (фикстура api)

PROTECTED = [
    ("GET", "/api/v1/customers"), ("GET", "/api/v1/customers/1"), ("GET", "/api/v1/analytics/summary"),
    ("GET", "/api/v1/analytics/churn-timeline"), ("GET", "/api/v1/model/settings"),
    ("PATCH", "/api/v1/model/settings"), ("PUT", "/api/v1/source/snapshot"), ("GET", "/api/v1/events/version"),
    ("GET", "/api/v1/auth/me"), ("POST", "/api/v1/auth/logout"),
]


@pytest.fixture
def client(api, loaded):
    token = login(api)
    api.headers["Authorization"] = f"Bearer {token}"
    yield api
    api.headers.pop("Authorization", None)


@pytest.mark.parametrize("method,path", PROTECTED)
def test_protected_endpoints_need_token(api, method, path):
    assert api.request(method, path).status_code == 401


@pytest.mark.parametrize("path", ["/metrics", "/healthz", "/api/v1/docs", "/api/v1/openapi.json"])
def test_public_endpoints(api, loaded, path):
    assert api.get(path).status_code == 200


# --- клиенты -----------------------------------------------------------------


def test_customers_list_shape_and_sorting(client):
    r = client.get("/api/v1/customers", params={"sort": "email", "order": "desc", "limit": 500})
    assert r.status_code == 200
    page = r.json()
    emails = [c["email"] for c in page["items"]]
    assert page["total"] == 10 and emails == sorted(emails, reverse=True)
    assert {"id", "email", "balance", "totalReplenished", "score", "level", "mainReason", "money"} <= set(page["items"][0])


def test_customers_filters(client):
    none = client.get("/api/v1/customers", params={"level": "none"}).json()
    assert none["total"] == 4 and {c["level"] for c in none["items"]} == {"none"}
    two = client.get("/api/v1/customers", params=[("level", "high"), ("level", "medium")]).json()
    assert {c["level"] for c in two["items"]} <= {"high", "medium"}
    assert [c["id"] for c in client.get("/api/v1/customers", params={"search": "#5"}).json()["items"]] == [5]
    paged = client.get("/api/v1/customers", params={"sort": "id", "order": "asc", "limit": 3, "offset": 3}).json()
    assert [c["id"] for c in paged["items"]] == [4, 5, 6]


@pytest.mark.parametrize("params", [{"sort": "nope"}, {"order": "up"}, {"level": "extreme"}, {"limit": 0},
                                    {"limit": 501}, {"offset": -1}])
def test_customers_rejects_bad_query(client, params):
    assert client.get("/api/v1/customers", params=params).status_code == 422


def test_customer_detail(client):
    r = client.get("/api/v1/customers/5")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == 5 and d["services"] and d["reasons"]
    assert all({"contributions", "transactions", "expectedLoss"} <= set(s) for s in d["services"])
    if d["score"] is not None:
        assert d["loyalty"] == pytest.approx(100 - d["score"])
    assert client.get("/api/v1/customers/99999").status_code == 404
    assert client.get("/api/v1/customers/abc").status_code == 422


# --- сводка и график ---------------------------------------------------------


def test_summary_endpoint_matches_service(client, session):
    r = client.get("/api/v1/analytics/summary")
    assert r.status_code == 200
    assert r.json() == service.get_summary(session)


@pytest.mark.parametrize("days_back", [1, 7, 30])
def test_churn_timeline_endpoint(client, days_back):
    r = client.get("/api/v1/analytics/churn-timeline", params={"days_back": days_back})
    assert r.status_code == 200
    points = r.json()["points"]
    assert sum(p["kind"] == "fact" for p in points) == days_back + 1
    assert sum(p["kind"] == "forecast" for p in points) == r.json()["horizonDays"] + 1


def test_churn_timeline_rejects_bad_range(client):
    for days_back, status in ((0, 422), (1, 200), (365, 200), (366, 422)):
        assert client.get("/api/v1/analytics/churn-timeline", params={"days_back": days_back}).status_code == status


# --- настройки и данные ------------------------------------------------------


def test_settings_patch_recalculates(client):
    before = client.get("/api/v1/model/settings").json()
    v0 = client.get("/api/v1/events/version").json()
    loss0 = client.get("/api/v1/analytics/summary").json()["money"]["expectedLoss"]
    r = client.patch("/api/v1/model/settings", json={"weights": {"prolong_wait": 90}})
    assert r.status_code == 200
    after = r.json()
    assert after["version"] == before["version"] + 1 and after["settings"]["weights"]["prolong_wait"] == 90
    v1 = client.get("/api/v1/events/version").json()
    assert v1["runId"] > v0["runId"] and v1["settingsVersion"] == after["version"]
    assert client.get("/api/v1/analytics/summary").json()["money"]["expectedLoss"] != loss0


@pytest.mark.parametrize("body", [{"weights": {"prolong_wait": 101}}, {"weights": {"nope": 1}},
                                  {"thresholds": {"medium": 70}}, {"horizonDays": 0}, {"unknown": 1}])
def test_settings_patch_rejects_bad_values(client, body):
    version = client.get("/api/v1/model/settings").json()["version"]
    assert client.patch("/api/v1/model/settings", json=body).status_code == 422
    assert client.get("/api/v1/model/settings").json()["version"] == version


def test_snapshot_put(client, source_data):
    data = json.loads(json.dumps(source_data))
    data["accounts"] = data["accounts"][:3]
    data["services"] = [s for s in data["services"] if s["ownerId"] <= 3]
    ids = {s["id"] for s in data["services"]}
    data["transactions"] = [t for t in data["transactions"] if t["serviceId"] in ids]
    r = client.put("/api/v1/source/snapshot", json=data)
    assert r.status_code == 200 and r.json()["accounts"] == 3
    assert client.get("/api/v1/customers").json()["total"] == 3
    assert client.put("/api/v1/source/snapshot", json={"accounts": [{"id": 1}]}).status_code == 422


# --- /metrics ----------------------------------------------------------------


def _metrics(api) -> tuple[str, dict[str, float]]:
    r = api.get("/metrics")
    assert r.status_code == 200
    values = {}
    for line in r.text.splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            values[name] = float(value)
    return r.headers["content-type"], values


def test_metrics_match_summary(client):
    s = client.get("/api/v1/analytics/summary").json()
    content_type, m = _metrics(client)
    assert content_type.startswith("text/plain") and "version=0.0.4" in content_type
    for level, count in s["accountsByLevel"].items():
        assert m[f'churn_customers{{level="{level}"}}'] == count
    assert m["churn_average_score"] == s["averageScore"]
    assert m['churn_services{state="total"}'] == s["servicesTotal"]
    assert m['churn_services{state="live"}'] == s["servicesLive"]
    assert m['churn_services{state="at_risk"}'] == s["servicesAtRisk"]
    h = f'{{horizon_days="{s["horizonDays"]}"}}'
    money = s["money"]
    for metric, field in (("churn_expected_loss_eur" + h, "expectedLoss"), ("churn_revenue_at_risk_eur" + h, "atRisk"),
                          ("churn_horizon_revenue_eur" + h, "horizonRevenue"),
                          ("churn_covered_by_bonus_eur" + h, "coveredByBonus"),
                          ("churn_monthly_revenue_eur", "monthlyRevenue"),
                          ("churn_expected_monthly_loss_eur", "expectedMonthlyLoss")):
        assert m[metric] == pytest.approx(round(money[field] / 100, 2)), metric
    assert m["churn_model_settings_version"] == s["run"]["settingsVersion"]


# --- живое обновление (SSE) ---------------------------------------------------


class _Request:
    def __init__(self, iterations: int):
        self.left = iterations

    async def is_disconnected(self) -> bool:
        self.left -= 1
        return self.left < 0


def _collect(iterations: int) -> list[str]:
    async def run():
        response = await events.stream(_Request(iterations))
        return [chunk async for chunk in response.body_iterator]

    return asyncio.run(run())


def test_sse_sends_current_version_then_keepalive(api, loaded, monkeypatch):
    monkeypatch.setattr(events, "POLL_INTERVAL_SECONDS", 0)
    chunks = _collect(2)
    assert chunks[0].startswith("event: update\ndata: ")
    payload = json.loads(chunks[0].split("data: ", 1)[1])
    assert payload == api.get("/api/v1/events/version", headers={"Authorization": f"Bearer {login(api)}"}).json()
    assert chunks[1] == ": keep-alive\n\n"


def test_sse_reports_unavailable_database(monkeypatch):
    def down():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(events, "_read_versions", down)
    monkeypatch.setattr(events, "DB_RETRY_SECONDS", 0)
    assert _collect(1) == ["event: db_unavailable\ndata: {}\n\n"]


def test_sse_needs_valid_query_token(api, loaded):
    assert api.get("/api/v1/events/stream").status_code == 422
    assert api.get("/api/v1/events/stream", params={"token": "bad"}).status_code == 401


# --- main.py -----------------------------------------------------------------


def test_root_redirects_to_swagger(api):
    r = api.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/api/v1/docs"


def test_database_errors_become_503(client, monkeypatch):
    def down(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(service, "get_summary", down)
    r = client.get("/api/v1/analytics/summary")
    assert r.status_code == 503 and "База данных недоступна" in r.json()["detail"]
