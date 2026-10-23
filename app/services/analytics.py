"""Агрегаты по рискам и деньгам.

Единственный источник цифр для /api/v1/analytics/summary и /metrics —
иначе панель и метрики разойдутся.
"""
from app.schemas import RiskLevel, Summary
from app.services import state


def aggregate() -> Summary:
    # TODO: считать из risk_scores
    return Summary(
        customers_by_level={level: 0 for level in RiskLevel},
        money_at_risk=0.0,
        expected_loss=0.0,
        data_version=state.data_version,
        model_version=state.model_settings.version,
    )
