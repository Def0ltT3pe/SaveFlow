#!/bin/sh
# Порядок старта: миграции -> сид (только в пустую БД) -> сервер.
set -e

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

if [ "${SEED_ON_START:-1}" = "1" ]; then
    echo "[entrypoint] загрузка data/data.json (если БД пуста)"
    python -m app.risk.cli load data/data.json --if-empty
fi

exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
