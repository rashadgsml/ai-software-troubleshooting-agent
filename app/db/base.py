"""SQLAlchemy base, engine and session factory.

Django analogy: `Base` is `models.Model`; a `Session` is like a transaction-scoped
QuerySet manager. SQLAlchemy has no implicit global connection, so we pass sessions
explicitly (FastAPI dependency), which also makes tests easy to isolate.
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    engine = create_engine(
        settings.database_url, pool_pre_ping=True,
        connect_args={"options": f"-c statement_timeout={settings.db_statement_timeout_ms}"},
    )
    return sessionmaker(engine, expire_on_commit=False)
