import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "examples"
SNAPSHOTS = ("demo/demo_day0.json", "demo/demo_day3.json")

DESCRIPTION = """Пересобирает примеры ответов API в docs/examples.

Берётся отдельная база, и она очищается: схема создаётся миграциями заново, загружаются
demo/demo_day0.json, затем demo/demo_day3.json (между ними — переходы статусов, это факт
на графике оттока), настройки модели — по умолчанию. Поэтому примеры одинаковы на любом
компьютере; различаются только id запусков и время расчёта.

  python scripts/make_examples.py postgresql+psycopg://churn:churn@localhost:5432/churn_test
"""


def _save(out: Path, name: str, data) -> None:
    (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(session, out: Path = OUT) -> None:
    """Загружает демо-снимки и пишет в out ответы API; база должна быть с актуальной схемой."""
    from app.risk import service, store
    from app.risk.schemas import ModelSettings

    store.save_settings(session, ModelSettings())
    session.commit()
    for path in SNAPSHOTS:
        snapshot = service.replace_snapshot(session, json.loads((ROOT / path).read_text(encoding="utf-8")))
    _save(out, "put_source_snapshot.response.json", snapshot)
    _save(out, "get_customers.response.json", service.get_customers(session, sort="score", order="desc", limit=3))
    customers = service.get_customers(session, limit=500)["items"]
    # Карточка с несколькими услугами нагляднее одиночной
    rich = max((c for c in customers if c["level"] == "high"), key=lambda c: (c["liveServices"], c["money"]["atRisk"]))
    _save(out, "get_customer.response.json", service.get_customer(session, rich["id"]))
    no_score = next(c["id"] for c in customers if c["level"] == "none")
    _save(out, "get_customer_no_score.response.json", service.get_customer(session, no_score))
    _save(out, "get_analytics_summary.response.json", service.get_summary(session))
    _save(out, "get_model_settings.response.json", service.get_settings(session))
    timeline = service.get_churn_timeline(session, days_back=7)
    timeline["points"] = timeline["points"][:12]  # 8 дней факта и 4 дня прогноза: видны оба вида точек
    _save(out, "get_churn_timeline.response.json", timeline)
    (out / "get_metrics.response.txt").write_text(service.render_metrics(session), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database_url", help="отдельная база для примеров — она будет очищена")
    parser.add_argument("--force", action="store_true", help="разрешить базу, в имени которой нет test или example")
    args = parser.parse_args()
    name = urlsplit(args.database_url).path.lstrip("/")
    if not args.force and not any(word in name for word in ("test", "example")):
        parser.error(f"база «{name}» будет очищена. Возьмите отдельную (…_test, …_examples) или добавьте --force")

    os.environ["DATABASE_URL"] = args.database_url
    sys.path.insert(0, str(ROOT))
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db.session import get_engine, session_factory

    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    with session_factory()() as session:
        build(session)
    print(f"Примеры пересобраны: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
