from typing import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runner import AgentRunner, default_agent_runner
from app.db.base import get_session_factory as _get_session_factory


def get_session_factory() -> sessionmaker[Session]:
    return _get_session_factory()


def get_session(factory: sessionmaker[Session] = Depends(get_session_factory)) -> Iterator[Session]:
    """Per-request DB session (Django analogy: the request-scoped connection)."""
    with factory() as session:
        yield session


def get_agent_runner() -> AgentRunner:
    return default_agent_runner
