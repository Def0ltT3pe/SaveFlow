# Бэкенд SaveFlow (FastAPI + SQLAlchemy + Alembic). Контекст сборки — корень репозитория.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY data/data.json ./data/data.json
COPY docker/entrypoint.sh /entrypoint.sh

# sed убирает CRLF, если файл правили на Windows
RUN sed -i 's/\r$//' /entrypoint.sh \
    && useradd --system --uid 10001 app \
    && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

ENTRYPOINT ["sh", "/entrypoint.sh"]
