from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db.session import session_factory


def get_db() -> Iterator[Session]:
    with session_factory()() as session:
        yield session
