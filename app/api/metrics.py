"""GET /metrics — Prometheus, без авторизации (скрейпер должен иметь доступ)."""
from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

from app.services.analytics import aggregate

router = APIRouter(tags=["metrics"])


class SaveFlowCollector:
    """Значения считаются на каждом скрейпе из той же aggregate(), что и панель."""

    def collect(self):
        s = aggregate()
        customers = GaugeMetricFamily(
            "saveflow_customers", "Клиенты по уровню риска", labels=["risk_level"]
        )
        for level, count in s.customers_by_level.items():
            customers.add_metric([level.value], count)
        yield customers
        yield GaugeMetricFamily("saveflow_money_at_risk_eur", "Выручка под риском, EUR", value=s.money_at_risk)
        yield GaugeMetricFamily("saveflow_expected_loss_eur", "Ожидаемые потери, EUR", value=s.expected_loss)
        yield GaugeMetricFamily("saveflow_data_version", "Версия данных", value=s.data_version)
        yield GaugeMetricFamily("saveflow_model_version", "Версия настроек модели", value=s.model_version)


registry = CollectorRegistry()
registry.register(SaveFlowCollector())


@router.get("/metrics")
def metrics() -> Response:
    """Получение агрегированных метрик в формате Prometheus."""
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
