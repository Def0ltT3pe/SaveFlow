import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import Account
from app.db.session import session_factory
from app.risk import service


DESCRIPTION = """Командная строка модели.

python -m app.risk.cli load data/data.json [--if-empty] загрузить снимок (полная замена)
python -m app.risk.cli recalc                        пересчитать оценки
python -m app.risk.cli summary | customers | customer ID | settings | timeline | metrics
python -m app.risk.cli dbcheck                       куда подключается бэкенд и готова ли БД
"""


def _dbcheck() -> int:
    from app.db.check import check_database

    sys.stdout.reconfigure(encoding="utf-8")
    r = check_database()
    print(f"БД:          {r['url']}  (источник: {r['source']})")
    if r["connectSeconds"] is None:
        print(f"Подключение: ОШИБКА — {r['error']}")
        return 1
    print(f"Подключение: ок за {r['connectSeconds']} с")
    print(f"Миграции:    {r['revision'] or 'нет'} (последняя в коде: {r['head']})")
    if r["accounts"] is not None:
        print(f"Клиентов:    {r['accounts']}")
    if not r["ok"]:
        print(f"ПРОБЛЕМА:    {r['error']}")
        return 1
    print("Всё в порядке")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.risk.cli", description=DESCRIPTION,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load", help="загрузить снимок в формате data.json")
    load.add_argument("path", type=Path)
    load.add_argument("--if-empty", action="store_true", help="не загружать, если данные уже есть")
    sub.add_parser("recalc")
    sub.add_parser("summary")
    customers = sub.add_parser("customers")
    customers.add_argument("--sort", default="score")
    customers.add_argument("--order", default="desc")
    customers.add_argument("--limit", type=int, default=20)
    customer = sub.add_parser("customer")
    customer.add_argument("id", type=int)
    sub.add_parser("settings")
    sub.add_parser("timeline")
    sub.add_parser("metrics")
    sub.add_parser("dbcheck")
    args = parser.parse_args(argv)
    if args.command == "dbcheck":
        return _dbcheck()

    with session_factory()() as session:
        if args.command == "load":
            if args.if_empty and session.scalar(select(func.count()).select_from(Account)):
                print("Данные уже загружены, пропускаю", file=sys.stderr)
                return 0
            result = service.replace_snapshot(session, json.loads(args.path.read_text(encoding="utf-8")))
        elif args.command == "recalc":
            result = service.recalculate(session)
        elif args.command == "summary":
            result = service.get_summary(session)
        elif args.command == "customers":
            result = service.get_customers(session, sort=args.sort, order=args.order, limit=args.limit)
        elif args.command == "customer":
            result = service.get_customer(session, args.id)
        elif args.command == "settings":
            result = service.get_settings(session)
        elif args.command == "timeline":
            result = service.get_churn_timeline(session)
        else:
            sys.stdout.write(service.render_metrics(session))
            return 0
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
