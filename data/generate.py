#!/usr/bin/env python3
"""Generate repeatable PostgreSQL fixtures; Python 3.10+, no dependencies."""

import argparse
import calendar
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

TERMS = ('hour', 'week', 'month', 'quarter_year', 'half_year', 'year')
STATES = ('active', 'blocked', 'deleted', 'suspended', 'prolong_wait')


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def add_term(value, term):
    if term == 'hour':
        return value + timedelta(hours=1)
    if term == 'week':
        return value + timedelta(weeks=1)
    months = {'month': 1, 'quarter_year': 3, 'half_year': 6, 'year': 12}[term]
    month_index = value.year * 12 + value.month - 1 + months
    year, month = divmod(month_index, 12)
    month += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def generate(accounts_count, services_count, transactions_count, seed, reference):
    rng = random.Random(seed)
    accounts = []
    for i in range(1, accounts_count + 1):
        balance = rng.randint(0, 100_000)
        accounts.append({
            'id': i, 'email': f'user-{i:04d}@example.test', 'balance': balance,
            'withdrawBalance': rng.randint(0, balance),
            'totalReplenished': balance + rng.randint(0, 500_000),
            'bonusBalance': rng.randint(0, 10_000),
            'bonusState': ('locked', 'unlocked')[(i - 1) % 2],
        })
    services = []
    for i in range(1, services_count + 1):
        term = TERMS[(i - 1) % len(TERMS)]
        status = STATES[(i - 1) % len(STATES)]
        if status == 'active':
            created = reference - timedelta(minutes=30)
        elif status in ('deleted', 'prolong_wait'):
            created = reference - timedelta(days=400)
        else:
            created = reference - timedelta(days=rng.randint(1, 90))
        services.append({
            'id': i, 'name': f'service-{i:04d}',
            'ownerId': (i - 1) % accounts_count + 1,
            'paymentTerm': term, 'autoProlong': bool(rng.getrandbits(1)),
            'createdAt': iso(created), 'expiresAt': iso(add_term(created, term)),
            'status': status, 'price': rng.randint(1, 1000),
        })
    transactions = []
    for i in range(1, transactions_count + 1):
        service = services[(i - 1) % services_count]
        service_created = datetime.fromisoformat(service['createdAt'].replace('Z', '+00:00'))
        created = service_created + timedelta(minutes=rng.randint(0, 5))
        performed = i % 3 != 0
        amount = 0 if i == 1 else 1000 if i == 2 else rng.randint(0, 1000)
        transactions.append({
            'id': i, 'amount': amount,
            'status': 'performed' if performed else 'not_performed',
            'performedAt': iso(created + timedelta(seconds=rng.randint(1, 60))) if performed else None,
            'createdAt': iso(created),
            'type': 'order' if i <= services_count else 'prolong',
            'serviceId': service['id'],
        })
    return {'accounts': accounts, 'services': services, 'transactions': transactions}


def seed_sql(data):
    parts = [
        '-- Generated from data.json. Run only on empty fixture tables.',
        'BEGIN;',
        'LOCK TABLE public.accounts, public.services, public.transactions IN EXCLUSIVE MODE;',
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.accounts) OR EXISTS (SELECT 1 FROM public.services) OR EXISTS (SELECT 1 FROM public.transactions) THEN RAISE EXCEPTION 'Seed requires empty accounts, services and transactions tables'; END IF; END $$;",
    ]
    for table, rows in data.items():
        if not rows:
            continue
        columns = ', '.join('"' + name + '"' for name in rows[0])
        tag = f'$fixture_{table}$'
        payload = json.dumps(rows, ensure_ascii=True, indent=2)
        parts.append(f'INSERT INTO public.{table} ({columns})\nSELECT {columns} FROM jsonb_populate_recordset(NULL::public.{table}, {tag}{payload}{tag}::jsonb);')
        parts.append(f"SELECT setval(pg_get_serial_sequence('public.{table}', 'id'), (SELECT max(id) FROM public.{table}), true);")
    parts.append('COMMIT;')
    return '\n\n'.join(parts) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accounts', type=int, default=10)
    parser.add_argument('--services', type=int, default=30)
    parser.add_argument('--transactions', type=int, default=100)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--reference', default='2026-10-19T15:00:00.000Z', help='ISO 8601 timestamp with timezone')
    parser.add_argument('--out', type=Path, default=Path('.'))
    args = parser.parse_args()
    if args.accounts < 1 or args.services < 1 or args.transactions < 0:
        parser.error('accounts and services must be >= 1; transactions must be >= 0')
    try:
        reference = datetime.fromisoformat(args.reference.replace('Z', '+00:00'))
        if reference.tzinfo is None:
            raise ValueError('timezone is required')
        reference = reference.astimezone(timezone.utc)
        data = generate(args.accounts, args.services, args.transactions, args.seed, reference)
    except (ValueError, OverflowError) as exc:
        parser.error(str(exc))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.out / '002_seed.sql').write_text(seed_sql(data), encoding='utf-8')
    print(f'Created data.json and 002_seed.sql: {args.accounts} accounts, {args.services} services, {args.transactions} transactions')


if __name__ == '__main__':
    main()
