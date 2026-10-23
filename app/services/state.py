"""Состояние приложения.

Заглушка в памяти процесса. TODO: перенести в Postgres
(model_settings, customers/services/transactions, risk_scores, meta.data_version) —
по ТЗ настройки обязаны переживать перезапуск.
"""
from app.schemas import ModelSettings, Thresholds

DEFAULT_WEIGHTS: dict[str, float] = {
    "expiry_soon": 25,
    "balance_deficit": 25,
    "prolong_wait": 20,
    "blocked_service": 10,
    "unpaid_orders": 10,
    "withdraw_ratio": 5,
    "no_autoprolong": 5,
}

model_settings = ModelSettings(version=1, weights=dict(DEFAULT_WEIGHTS), thresholds=Thresholds())
snapshot: dict[str, list[dict]] = {"customers": [], "services": [], "transactions": []}
data_version = 0


def recompute_all() -> int:
    """Пересчитать score и деньги всех клиентов. Вызывается после PATCH settings и PUT snapshot."""
    global data_version
    # TODO: признаки -> risk_scores
    data_version += 1
    return data_version
