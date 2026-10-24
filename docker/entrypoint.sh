#!/bin/sh
# Порядок старта: миграции -> сид (только в пустую БД) -> сервер.
set -e

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

if [ "${SEED_ON_START:-1}" = "1" ]; then
    echo "[entrypoint] загрузка data/data.json (если БД пуста)"
    python -m app.risk.cli load data/data.json --if-empty
fi

# Ровно один воркер: токены сессий лежат в памяти процесса (app/core/security.py),
# при нескольких воркерах вход «пропадал» бы на случайных запросах.
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
