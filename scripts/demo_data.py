import argparse
import calendar
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_REFERENCE = "2026-10-19T15:00:00.000Z"
GRACE_DAYS = 5
TERM_WEIGHTS = {"hour": 15, "week": 10, "month": 45, "quarter_year": 12, "half_year": 8, "year": 10}
TERM_PRICE = {"hour": (1, 4), "week": (60, 250), "month": (200, 900), "quarter_year": (600, 1000),
              "half_year": (800, 1000), "year": (900, 1000)}
TERM_HOURS = {"hour": 1, "week": 168}
TERM_MONTHS = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}
PERIOD_DAYS = {"hour": 1 / 24, "week": 7, "month": 30.4375, "quarter_year": 91.3125,
               "half_year": 182.625, "year": 365.25}

PROFILES = {
    #           доля, статусы услуг (вес),                                                автопродление, баланс в месяцах
    "loyal":    (45, {"active": 85, "suspended": 10, "deleted": 5}, 0.85, (1.5, 6.0)),
    "drifting": (30, {"active": 60, "suspended": 20, "prolong_wait": 10, "deleted": 10}, 0.5, (0.0, 1.2)),
    "leaving":  (15, {"active": 25, "prolong_wait": 45, "suspended": 10, "deleted": 20}, 0.2, (0.0, 0.3)),
    "gone":     (10, {"deleted": 80, "blocked": 20}, 0.3, (0.0, 0.0)),
}


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def add_terms(value: datetime, term: str, count: int = 1) -> datetime:
    if term in TERM_HOURS:
        return value + timedelta(hours=TERM_HOURS[term] * count)
    index = value.year * 12 + value.month - 1 + TERM_MONTHS[term] * count
    year, month = divmod(index, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def generate(accounts_count: int, seed: int, reference: datetime) -> dict:
    rng = random.Random(seed)
    accounts, services, transactions = [], [], []

    def add_tx(service: dict, created: datetime, tx_type: str, performed: bool) -> None:
        created = min(created, reference)
        transactions.append({
            "id": len(transactions) + 1, "amount": min(service["price"], 1000),
            "status": "performed" if performed else "not_performed",
            "performedAt": iso(created + timedelta(seconds=rng.randint(1, 120))) if performed else None,
            "createdAt": iso(created), "type": tx_type, "serviceId": service["id"],
        })

    for account_id in range(1, accounts_count + 1):
        profile = pick(rng, {name: p[0] for name, p in PROFILES.items()})
        _, status_weights, autoprolong_share, balance_months = PROFILES[profile]
        monthly_cost = 0.0
        own = []
        for _ in range(rng.choices([1, 2, 3, 4], weights=[35, 35, 20, 10])[0]):
            term = pick(rng, TERM_WEIGHTS)
            status = pick(rng, status_weights)
            price = rng.randint(*TERM_PRICE[term])
            period = timedelta(days=PERIOD_DAYS[term])
            if status in ("active", "suspended"):
                expires = reference + period * rng.random()
            elif status == "prolong_wait":
                expires = reference - timedelta(days=rng.uniform(0, GRACE_DAYS))
            elif status == "deleted":
                expires = reference - timedelta(days=rng.uniform(GRACE_DAYS, 120))
            else:
                expires = reference + timedelta(days=rng.uniform(-60, 60))
            # Сколько периодов услуга уже прожила: до ~полугода для коротких тарифов.
            max_periods = max(1, min(int(180 / PERIOD_DAYS[term]), 4320))
            periods = rng.randint(1, max_periods)
            created = add_terms(expires, term, -periods) if term in TERM_MONTHS else expires - period * periods
            created = min(created, reference - timedelta(minutes=5))
            expires = max(expires, created)
            service = {
                "id": len(services) + 1, "name": f"srv-{account_id:04d}-{len(own) + 1}",
                "ownerId": account_id, "paymentTerm": term,
                "autoProlong": rng.random() < autoprolong_share,
                "createdAt": iso(created), "expiresAt": iso(expires), "status": status, "price": price,
            }
            services.append(service)
            own.append(service)
            if status in ("active", "suspended", "prolong_wait"):
                monthly_cost += price * 30.4375 / PERIOD_DAYS[term]

            unpaid_share = {"loyal": 0.02, "drifting": 0.15, "leaving": 0.35, "gone": 0.2}[profile]
            add_tx(service, created, "order", rng.random() > unpaid_share / 2)
            # Последние продления (не больше 6, чтобы почасовые услуги не раздували набор).
            for j in range(max(1, periods - 6), periods):
                when = created + (expires - created) * j / periods
                add_tx(service, when, "prolong", rng.random() > unpaid_share)
            if status == "prolong_wait":
                add_tx(service, expires, "prolong", False)

        low, high = balance_months
        balance = int(monthly_cost * rng.uniform(low, high)) if profile != "gone" else rng.randint(0, 500)
        spent = sum(t["amount"] for t in transactions
                    if t["status"] == "performed" and t["serviceId"] in {s["id"] for s in own})
        accounts.append({
            "id": account_id, "email": f"client-{account_id:04d}@example.test", "balance": balance,
            "withdrawBalance": rng.randint(0, balance // 3) if balance and rng.random() < 0.15 else 0,
            "totalReplenished": balance + spent + rng.randint(0, 5000),
            "bonusBalance": rng.randint(0, 2000) if rng.random() < 0.5 else 0,
            "bonusState": "unlocked" if rng.random() < 0.4 else "locked",
        })

    return {"referenceDate": iso(reference), "accounts": accounts, "services": services,
            "transactions": transactions}


def advance(data: dict, days: float, seed: int) -> dict:
    rng = random.Random(seed)
    data = json.loads(json.dumps(data))
    old_reference = parse(data.get("referenceDate") or DEFAULT_REFERENCE)
    reference = old_reference + timedelta(days=days)
    accounts = {a["id"]: a for a in data["accounts"]}
    transactions = data["transactions"]
    next_tx = max((t["id"] for t in transactions), default=0) + 1

    def add_tx(service: dict, when: datetime, performed: bool) -> None:
        nonlocal next_tx
        transactions.append({
            "id": next_tx, "amount": min(service["price"], 1000),
            "status": "performed" if performed else "not_performed",
            "performedAt": iso(when + timedelta(seconds=30)) if performed else None,
            "createdAt": iso(when), "type": "prolong", "serviceId": service["id"],
        })
        next_tx += 1

    def charge(account: dict, price: int) -> bool:
        bonus = account["bonusBalance"] if account["bonusState"] == "unlocked" else 0
        if account["balance"] + bonus < price:
            return False
        from_balance = min(account["balance"], price)
        account["balance"] -= from_balance
        account["withdrawBalance"] = min(account["withdrawBalance"], account["balance"])
        account["bonusBalance"] -= price - from_balance
        return True

    # Часть клиентов пополняет баланс.
    for account in accounts.values():
        if rng.random() < 0.08:
            top_up = rng.randint(500, 5000)
            account["balance"] += top_up
            account["totalReplenished"] += top_up

    for service in sorted(data["services"], key=lambda s: s["expiresAt"]):
        account = accounts[service["ownerId"]]
        if service["status"] in ("active", "suspended") and rng.random() < 0.05:
            service["autoProlong"] = False
        expires = parse(service["expiresAt"])
        if service["status"] in ("active", "suspended"):
            while expires <= reference:
                if service["autoProlong"] and charge(account, service["price"]):
                    add_tx(service, expires, True)
                    expires = add_terms(expires, service["paymentTerm"])
                else:
                    add_tx(service, expires, False)
                    service["status"] = "prolong_wait"
                    break
        if service["status"] == "prolong_wait":
            if rng.random() < 0.25 and charge(account, service["price"]):
                add_tx(service, max(expires, old_reference), True)
                service["status"] = "active"
                while expires <= reference:
                    expires = add_terms(expires, service["paymentTerm"])
            elif reference > expires + timedelta(days=GRACE_DAYS):
                service["status"] = "deleted"
        service["expiresAt"] = iso(expires)

    data["referenceDate"] = iso(reference)
    return data


DESCRIPTION = """Демо-данные в формате data.json, расширенные для наглядности (ТЗ это разрешает).

В исходном наборе у каждого клиента все услуги в одном статусе, а балансы в сотни раз больше цен,
поэтому часть сигналов там не проявляется. Здесь клиенты разных профилей:
  loyal    — большой баланс, автопродление включено;
  drifting — баланса хватает на месяц или меньше, часть серверов выключена;
  leaving  — денег почти нет, услуги в prolong_wait, были удаления;
  gone     — все услуги уже удалены или заблокированы (оценки нет).

Команды (только стандартная библиотека Python 3.10+):
  python scripts/demo_data.py generate --accounts 200 --seed 7 --out demo_day0.json
  python scripts/demo_data.py advance demo_day0.json --days 3 --seed 1 --out demo_day3.json

advance прокручивает время: автопродление списывает деньги с баланса (затем с доступного бонуса),
без денег услуга уходит в prolong_wait, через 5 дней prolong_wait — в deleted; часть клиентов
пополняет баланс или выключает автопродление. Так видно обновление оценок и факт удалений.

Цены не выше 1000 евроцентов: в схеме организаторов amount транзакции ограничен 0..1000.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--accounts", type=int, default=200)
    gen.add_argument("--seed", type=int, default=7)
    gen.add_argument("--reference", default=DEFAULT_REFERENCE)
    gen.add_argument("--out", type=Path, required=True)
    adv = sub.add_parser("advance")
    adv.add_argument("source", type=Path)
    adv.add_argument("--days", type=float, required=True)
    adv.add_argument("--seed", type=int, default=1)
    adv.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "generate":
        if args.accounts < 1:
            parser.error("--accounts должно быть >= 1")
        data = generate(args.accounts, args.seed, parse(args.reference))
    else:
        data = advance(json.loads(args.source.read_text(encoding="utf-8")), args.days, args.seed)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(data['accounts'])} аккаунтов, {len(data['services'])} услуг, "
          f"{len(data['transactions'])} транзакций, referenceDate {data['referenceDate']}")


if __name__ == "__main__":
    main()
