import importlib.util
import json
import re

import pytest

from tests.conftest import ROOT

EXAMPLES = ROOT / "docs" / "examples"
REBUILD = "пересоберите: python scripts/make_examples.py <отдельная база, например …/churn_test>"


def _load_script():
    spec = importlib.util.spec_from_file_location("make_examples", ROOT / "scripts" / "make_examples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paths(value, prefix: str = "") -> set[str]:
    """Состав полей ответа без значений: points[].dueServices, money.atRisk, …"""
    if isinstance(value, dict):
        found = set()
        for key, item in value.items():
            found |= {f"{prefix}{key}"} | _paths(item, f"{prefix}{key}.")
        return found
    if isinstance(value, list):
        return set().union(*(_paths(item, f"{prefix[:-1]}[].") for item in value)) if value else set()
    return set()


def _metric_names(text: str) -> set[str]:
    return {re.split(r"[{ ]", line, maxsplit=1)[0] for line in text.splitlines() if line and not line.startswith("#")}


@pytest.fixture(scope="module")
def fresh(engine, tmp_path_factory):
    """Примеры, собранные текущим кодом тем же способом, что и docs/examples."""
    from sqlalchemy.orm import sessionmaker

    out = tmp_path_factory.mktemp("examples")
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        _load_script().build(session, out)
    return out


@pytest.mark.parametrize("name", sorted(p.name for p in EXAMPLES.glob("*.response.json")))
def test_example_has_the_same_fields_as_the_api(fresh, name):
    committed = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    current = json.loads((fresh / name).read_text(encoding="utf-8"))
    missing, extra = _paths(current) - _paths(committed), _paths(committed) - _paths(current)
    assert not missing and not extra, f"docs/examples/{name} устарел (нет полей {sorted(missing)}, лишние {sorted(extra)}); {REBUILD}"


def test_metrics_example_has_the_same_metrics(fresh):
    committed = _metric_names((EXAMPLES / "get_metrics.response.txt").read_text(encoding="utf-8"))
    current = _metric_names((fresh / "get_metrics.response.txt").read_text(encoding="utf-8"))
    assert committed == current, f"docs/examples/get_metrics.response.txt устарел; {REBUILD}"


def test_every_example_is_rebuilt_by_the_script(fresh):
    built = {p.name for p in fresh.iterdir()}
    committed = {p.name for p in EXAMPLES.iterdir()} - {"patch_model_settings.request.json"}  # запрос пишется руками
    assert built == committed
