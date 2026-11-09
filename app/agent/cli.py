"""Run the agent on a fault scenario from the terminal.

    .venv/bin/python -m app.agent.cli pool_exhausted
    .venv/bin/python -m app.agent.cli bad_config --incident "Payments are failing"
"""

import argparse
import json
import tempfile
from pathlib import Path

import anthropic
from fastapi.testclient import TestClient

from app.agent.orchestrator import run_investigation
from app.agent.schemas import Step
from app.core.config import get_settings
from app.tools.diagnostics import build_registry
from app.tools.registry import ToolContext
from demo_app.faults import SCENARIOS, build_world
from demo_app.main import create_app

RUNBOOKS = Path(__file__).resolve().parents[2] / "demo_app" / "runbooks"


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
    args = parser.parse_args()

    settings = get_settings()
    scenario = SCENARIOS[args.scenario]
    incident = args.incident or scenario.incident

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        build_world(data_dir, args.scenario)
        # In-process HTTP client talking to the demo app (no server needed).
        ctx = ToolContext(data_dir=data_dir, http=TestClient(create_app(data_dir)), runbooks_dir=RUNBOOKS)
        registry = build_registry(ctx)

        print(f"Scenario: {args.scenario}   (hidden truth: {scenario.root_cause})")
        print(f"Incident: {incident}\n")
        result = run_investigation(
            incident, registry, anthropic.Anthropic(api_key=settings.anthropic_api_key),
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
