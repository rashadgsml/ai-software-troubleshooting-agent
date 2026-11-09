from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.tools.diagnostics import build_registry
from app.tools.registry import ToolContext
from demo_app.faults import build_world
from demo_app.main import create_app

RUNBOOKS = Path(__file__).resolve().parent.parent / "demo_app" / "runbooks"


@pytest.fixture
def make_registry(tmp_path):
    """Factory: make_registry("disk_full") -> ToolRegistry over a freshly injected world."""

    def _make(scenario: str):
        build_world(tmp_path, scenario)
        client = TestClient(create_app(tmp_path))  # a real httpx.Client talking to the demo app
        return build_registry(ToolContext(data_dir=tmp_path, http=client, runbooks_dir=RUNBOOKS))

    return _make
