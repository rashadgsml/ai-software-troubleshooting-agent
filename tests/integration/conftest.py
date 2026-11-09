"""Integration fixtures: a real PostgreSQL (separate `*_test` database), the real FastAPI app,
and a scripted "Claude" so no API calls are made."""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.agent.orchestrator import run_investigation
from app.agent.schemas import Guardrails
from app.api.deps import get_agent_runner, get_session_factory
from app.api.security import Limiters, get_limiters
from app.core.config import Settings, get_settings
from app.db import models  # noqa: F401
from app.db.base import Base
from app.main import app
from app.tools.diagnostics import build_registry
from app.tools.factory import make_context
from demo_app.faults import build_world
from tests.unit.test_orchestrator import VALID_REPORT, FakeClient, reply, tool_use

TEST_DB = "troubleshooter_test"
MIGRATION_DB = "troubleshooter_migration_test"


def _ensure_database(name: str) -> str:
    """Create `name` if missing; skip the whole test if Postgres isn't reachable."""
    url = make_url(get_settings().database_url)
    admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            if not conn.scalar(sa.text("select 1 from pg_database where datname = :n"), {"n": name}):
                conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    except sa.exc.OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable ({exc.orig}); start it with `docker compose up -d db`")
    finally:
        admin.dispose()
    return url.set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def engine():
    eng = sa.create_engine(_ensure_database(TEST_DB))
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE incidents, investigations, investigation_steps RESTART IDENTITY CASCADE"))
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def scripted_runner(tmp_path):
    """Factory: scripted_runner(*claude_replies) -> an AgentRunner using the REAL orchestrator
    and REAL tools against the injected scenario, but a scripted Claude."""

    def _make(*responses, guardrails=None):
        def runner(incident_text, scenario, on_step):
            world = tmp_path / f"world_{scenario}"
            build_world(world, scenario)
            registry = build_registry(make_context(world))
            return run_investigation(
                incident_text, registry, FakeClient(*responses), "test-model",
                guardrails=guardrails or Guardrails(), on_step=on_step,
            )

        return runner

    return _make


TEST_API_KEY = "test-key-0123456789abcdef"


@pytest.fixture
def test_settings():
    """Mutable per-test settings (tests may tweak limits before making requests)."""
    return Settings(api_key=TEST_API_KEY)


@pytest.fixture
def limiters(test_settings):
    """Fresh rate-limiter state per test (production uses a process-wide cached instance)."""
    return Limiters(test_settings)


@pytest.fixture
def api(session_factory, test_settings, limiters):
    """Returns (authenticated client, use_runner). Background tasks complete before TestClient returns."""
    holder = {"runner": None}
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_agent_runner] = lambda: holder["runner"]
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_limiters] = lambda: limiters
    # no `with`: lifespan (startup sweep) is tested separately
    client = TestClient(app, headers={"X-API-Key": TEST_API_KEY})

    def use_runner(runner):
        holder["runner"] = runner

    yield client, use_runner
    app.dependency_overrides.clear()


def happy_replies():
    return [
        reply(tool_use("t1", "check_resources"), tool_use("t2", "read_config")),
        reply(tool_use("t3", "submit_report", **VALID_REPORT)),
    ]
