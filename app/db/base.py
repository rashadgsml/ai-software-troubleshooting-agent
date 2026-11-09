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
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)
