import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL не задан")
    from alembic import command
    from alembic.config import Config

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    os.environ["DATABASE_URL"] = url
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s


@pytest.fixture
def source_data() -> dict:
    return json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))
