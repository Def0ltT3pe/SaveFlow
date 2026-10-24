import re
from pathlib import Path

import pytest

from app.risk import features
from app.risk.registry import FEATURES
from app.risk.schemas import ModelSettings
from app.risk.terms import monthly_price
from tests.test_scoring import account, score, service

ROOT = Path(__file__).resolve().parent.parent


def _recipe(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    section = text.split(heading, 1)[1]
    return re.search(r"```python\n(.*?)```", section, re.S).group(1)


@pytest.mark.parametrize("path,heading", [
    (ROOT / "README.md", "## Как добавить новый признак"),
    (ROOT / "docs" / "MODEL.md", "## Как добавить признак"),
])
def test_feature_recipe_from_docs_works(path, heading):
    code = _recipe(path, heading)
    namespace = dict(vars(features), monthly_price=monthly_price)
    before = set(FEATURES)
    exec(code, namespace)  # ровно то, что разработчик вставит в app/risk/features.py
    (key,) = set(FEATURES) - before
    try:
        assert ModelSettings().weights[key] == FEATURES[key].default_weight  # вес появился в настройках
        r = score([service(price=900)], account(balance=10_000))
        reason = next(x for x in r.reasons if x["feature"] == key)
        assert reason["points"] > 0 and reason["text"]
    finally:
        FEATURES.pop(key, None)
