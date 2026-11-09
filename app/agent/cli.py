"""Run the agent on a fault scenario from the terminal.

    .venv/bin/python -m app.agent.cli pool_exhausted            # tools via MCP (stdio subprocess)
    .venv/bin/python -m app.agent.cli bad_config --direct       # tools called in-process, no MCP
"""

import argparse
import contextlib
import json
import sys
import tempfile
from pathlib import Path

import anthropic
from mcp import StdioServerParameters

from app.agent.mcp_client import McpToolProvider
from app.agent.orchestrator import run_investigation
from app.agent.schemas import Step
from app.core.config import get_settings
from app.tools.diagnostics import build_registry
from app.tools.factory import make_context
from demo_app.faults import SCENARIOS, build_world

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _print_step(step: Step) -> None:
    if step.kind == "tool_call":
        print(f"  -> {step.tool_name}({json.dumps(step.payload)})")
    elif step.kind == "tool_result":
        preview = str(step.payload).replace("\n", " ")[:110]
        print(f"     {'ERROR ' if step.is_error else ''}{preview}{'...' if len(str(step.payload)) > 110 else ''}")
    elif step.kind == "assistant_text":
        print(f"  [claude] {step.payload}")
    elif step.kind in ("guardrail", "error"):
        print(f"  !! {step.payload}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--incident", help="Override the incident text.")
    parser.add_argument("--direct", action="store_true", help="Skip MCP; call tools in-process.")
    args = parser.parse_args()

    settings = get_settings()
    scenario = SCENARIOS[args.scenario]
    incident = args.incident or scenario.incident

    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
        data_dir = Path(tmp)
        build_world(data_dir, args.scenario)
        if args.direct:
            tools = build_registry(make_context(data_dir))
            transport = "direct"
        else:
            tools = stack.enter_context(McpToolProvider(StdioServerParameters(
                command=sys.executable, args=["-m", "app.mcp_server"],
                env={"DEMO_DATA_DIR": str(data_dir)}, cwd=PROJECT_ROOT,
            )))
            transport = "MCP/stdio"

        print(f"Scenario: {args.scenario}   (hidden truth: {scenario.root_cause})   tools: {transport}")
        print(f"Incident: {incident}\n")
        result = run_investigation(
            incident, tools, anthropic.Anthropic(api_key=settings.anthropic_api_key),
            settings.anthropic_model, on_step=_print_step,
        )

    print(f"\nstatus={result.status} iterations={result.iterations} "
          f"tokens in/out={result.input_tokens}/{result.output_tokens}")
    if result.report:
        print(json.dumps(result.report.model_dump(), indent=2))
    elif result.reason:
        print(f"reason: {result.reason}")


if __name__ == "__main__":
    main()
