"""Build a ToolContext for the demo world (shared by the CLI and the MCP server)."""

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.tools.registry import ToolContext
from demo_app.main import create_app

RUNBOOKS_DIR = Path(__file__).resolve().parents[2] / "demo_app" / "runbooks"


def make_context(data_dir: Path, demo_app_url: str | None = None) -> ToolContext:
    """With a URL, probe the real demo app over HTTP; otherwise run it in-process
    (same ASGI app, no server needed)."""
    if demo_app_url:
        http: httpx.Client = httpx.Client(base_url=demo_app_url, timeout=5.0)
    else:
        http = TestClient(create_app(data_dir))
    return ToolContext(data_dir=Path(data_dir), http=http, runbooks_dir=RUNBOOKS_DIR)
