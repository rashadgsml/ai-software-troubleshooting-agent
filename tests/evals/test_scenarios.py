"""Opt-in agent evals against the REAL API (costs a few cents per scenario).

    .venv/bin/pytest -m eval -v

Excluded from the default run (see addopts in pyproject.toml). For repeated runs, a results
table and model comparisons use `python -m evals.run` instead.
"""

import anthropic
import pytest

from app.core.config import get_settings
from demo_app.faults import SCENARIOS
from evals.run import run_one

pytestmark = pytest.mark.eval


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_agent_solves_scenario(client, scenario):
    record = run_one(client, get_settings().anthropic_model, scenario, 0)
    assert record.score.passed, (
        f"status={record.status} category={(record.report or {}).get('category')} "
        f"ungrounded={record.score.ungrounded} reason={record.reason}"
    )
