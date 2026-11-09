"""Default agent runner: one self-contained investigation of a demo scenario.

A *runner* is `(incident_text, scenario, on_step) -> AgentResult`. The API depends on this
signature rather than on Anthropic/MCP directly, so tests can swap in a scripted runner.
"""

import sys
import tempfile
from pathlib import Path
from typing import Callable

import anthropic
from mcp import StdioServerParameters

from app.agent.mcp_client import McpToolProvider
from app.agent.orchestrator import run_investigation
from app.agent.schemas import AgentResult, Guardrails, Step
from app.core.config import get_settings
from demo_app.faults import build_world

PROJECT_ROOT = Path(__file__).resolve().parents[2]

AgentRunner = Callable[[str, str, Callable[[Step], None]], AgentResult]


def default_agent_runner(incident_text: str, scenario: str, on_step: Callable[[Step], None]) -> AgentResult:
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="demo_world_") as tmp:
        build_world(Path(tmp), scenario)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp_server"],
            env={"DEMO_DATA_DIR": tmp}, cwd=PROJECT_ROOT,
        )
        with McpToolProvider(params) as tools:
            return run_investigation(
                incident_text, tools, anthropic.Anthropic(api_key=settings.anthropic_api_key),
                settings.anthropic_model, guardrails=Guardrails(), on_step=on_step,
            )
