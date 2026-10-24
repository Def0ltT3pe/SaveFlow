import ast
import copy
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, inspect, select

from app.db.models import Account
from app.risk import service

ROOT = Path(__file__).resolve().parent.parent
SQL_CALLS = {"text", "execute", "exec_driver_sql"}
PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE accounts; --",
    "%",
    "_",
    "\\",
    "user-000_",
]


def _dynamic_sql(node: ast.expr) -> bool:
    """f-строка, .format(), склейка или %-форматирование вместо готовой строки SQL."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format")


@pytest.mark.parametrize("path", sorted(p.relative_to(ROOT) for d in ("app/db", "app/risk", "migrations")
                                        for p in (ROOT / d).rglob("*.py")), ids=str)
def test_sql_is_never_built_from_strings(path):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if name in SQL_CALLS:
            assert not _dynamic_sql(node.args[0]), f"{path}:{node.lineno}: SQL собирается из строк"


@pytest.mark.parametrize("payload", PAYLOADS)
def test_search_treats_input_literally(session, loaded, payload):
    page = service.get_customers(session, search=payload)
    assert page["total"] == 0
    assert session.scalar(select(func.count()).select_from(Account)) == 10


def test_search_still_finds_real_matches(session, loaded):
    assert service.get_customers(session, search="USER-0005")["total"] == 1


@pytest.mark.parametrize("search", ["7", "#7", " 7 "])
def test_search_finds_customer_by_id(session, loaded, search):
    ids = [c["id"] for c in service.get_customers(session, search=search)["items"]]
    assert 7 in ids


def test_search_by_id_is_exact_and_combines_with_email(session, loaded):
    ids = {c["id"] for c in service.get_customers(session, search="1")["items"]}
    # ID 1 точно, плюс все, у кого «1» есть в email (user-0001, user-0010)
    assert ids == {1, 10}


@pytest.mark.parametrize("search", ["99999999999999999999", "#", "٣", "7; DROP TABLE accounts"])
def test_search_text_that_is_not_an_id(session, loaded, search):
    page = service.get_customers(session, search=search)
    assert page["total"] == 0
    assert session.scalar(select(func.count()).select_from(Account)) == 10


@pytest.mark.parametrize("kwargs", [
    {"sort": "id; DROP TABLE accounts"},
    {"sort": "score DESC, (SELECT 1)"},
    {"order": "desc; --"},
    {"levels": ["high' OR 1=1 --"]},
])
def test_query_parameters_are_whitelisted(session, loaded, kwargs):
    with pytest.raises(ValueError):
        service.get_customers(session, **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"limit": "10; DROP TABLE accounts"},
    {"offset": "0 OR 1=1"},
    {"limit": 1.5},
    {"limit": True},
])
def test_paging_rejects_garbage(session, loaded, kwargs):
    with pytest.raises(ValueError):
        service.get_customers(session, **kwargs)


@pytest.mark.parametrize("search", ["a\x00b", "a" * 321])
def test_impossible_search_finds_nobody_without_error(session, loaded, search):
    page = service.get_customers(session, search=search)
    assert page["total"] == 0 and page["items"] == []


def test_paging_bounds_never_reach_db_errors(session, loaded):
    assert service.get_customers(session, offset=10**30)["items"] == []
    assert service.get_customers(session, limit="3", offset="1")["limit"] == 3
    assert service.get_customers(session, levels="none")["total"] == 4


@pytest.mark.parametrize("customer_id", ["1 OR 1=1", "1; DROP TABLE accounts", "abc", "", 2**63, -1, 0, True, None, 1.5])
def test_customer_id_garbage_means_not_found(session, loaded, customer_id):
    assert service.get_customer(session, customer_id) is None
    assert session.scalar(select(func.count()).select_from(Account)) == 10


def test_customer_id_digits_string_is_accepted(session, loaded):
    assert service.get_customer(session, "5")["id"] == 5


@pytest.mark.parametrize("days_back", ["7; DROP TABLE accounts", None, 1.5])
def test_timeline_rejects_garbage(session, loaded, days_back):
    with pytest.raises(ValueError):
        service.get_churn_timeline(session, days_back=days_back)


def test_timeline_days_back_is_bounded(session, loaded):
    points = service.get_churn_timeline(session, days_back=10**7)["points"]
    assert sum(p["kind"] == "fact" for p in points) == 366


@pytest.mark.parametrize("email", ["<script>alert(1)</script>@x.test", "a\x00b@x.test", 'a"b@x.test',
                                   "a`b@x.test", "a\nb@x.test"])
def test_snapshot_rejects_dangerous_emails(session, loaded, source_data, email):
    data = copy.deepcopy(source_data)
    data["accounts"][0]["email"] = email
    with pytest.raises(ValidationError):
        service.replace_snapshot(session, data)
    assert session.scalar(select(func.count()).select_from(Account)) == 10


def test_snapshot_strings_are_stored_as_data(session, loaded, source_data):
    data = copy.deepcopy(source_data)
    evil = "x');DROP/**/TABLE/**/accounts;--@evil.test"
    data["accounts"][0]["email"] = evil
    service.replace_snapshot(session, data)
    assert "accounts" in inspect(session.get_bind()).get_table_names()
    page = service.get_customers(session, search=evil)
    assert page["total"] == 1 and page["items"][0]["email"] == evil


def test_snapshot_rejects_sql_in_constrained_fields(session, loaded, source_data):
    data = copy.deepcopy(source_data)
    data["services"][0]["name"] = "srv'; DROP TABLE services; --"
    with pytest.raises(ValidationError):
        service.replace_snapshot(session, data)
    data = copy.deepcopy(source_data)
    data["services"][0]["status"] = "active' OR '1'='1"
    with pytest.raises(ValidationError):
        service.replace_snapshot(session, data)


def test_settings_reject_unknown_keys(session, loaded):
    with pytest.raises(ValidationError):
        service.update_settings(session, {"weights": {"prolong_wait'; DROP TABLE model_settings; --": 1}})
    with pytest.raises(ValidationError):
        service.update_settings(session, {"aggregation": "max'; --"})
    assert service.get_settings(session)["version"] >= 1
