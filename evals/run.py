"""Run the agent against every fault scenario, several times each, and score the runs.

    .venv/bin/python -m evals.run                       # all scenarios x3, default model
    .venv/bin/python -m evals.run -n 5 --scenarios disk_full log_injection
    .venv/bin/python -m evals.run --model claude-haiku-5-5 --label haiku

This calls the real API and costs money (printed at the end). Results are saved to
evals/results/<label>.json (full traces) and summarised in evals/results/<label>.md.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import anthropic

from app.agent.orchestrator import run_investigation
from app.agent.schemas import AgentResult
from app.core.config import get_settings
from app.tools.diagnostics import build_registry
from app.tools.factory import make_context
from demo_app.faults import SCENARIOS, build_world
from evals.scoring import Score, score_run

RESULTS_DIR = Path(__file__).parent / "results"
INJECTION_SCENARIOS = {"log_injection"}
# USD per million tokens (input, output). Used only for the cost estimate.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
}


@dataclass
class RunRecord:
    scenario: str
    run: int
    status: str
    reason: str | None
    iterations: int
    input_tokens: int
    output_tokens: int
    seconds: float
    score: Score
    report: dict | None
    trace: list[dict]

    @property
    def cost(self) -> float | None:
        return None


def run_one(client: anthropic.Anthropic, model: str, scenario_name: str, idx: int) -> RunRecord:
    scenario = SCENARIOS[scenario_name]
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        build_world(Path(tmp), scenario_name)
        registry = build_registry(make_context(Path(tmp)))
        try:
            result = run_investigation(scenario.incident, registry, client, model)
        except Exception as exc:  # an eval run must never take down the whole batch
            result = AgentResult(status="failed", reason=f"{type(exc).__name__}: {exc}")
    score = score_run(scenario, result, injection=scenario_name in INJECTION_SCENARIOS)
    return RunRecord(
        scenario_name, idx, result.status, result.reason, result.iterations,
        result.input_tokens, result.output_tokens, round(time.monotonic() - started, 1), score,
        result.report.model_dump() if result.report else None,
        [{"kind": s.kind, "tool": s.tool_name, "payload": s.payload, "is_error": s.is_error} for s in result.steps],
    )


def estimate_cost(model: str, records: list[RunRecord]) -> float | None:
    if model not in PRICES:
        return None
    pin, pout = PRICES[model]
    return sum(r.input_tokens * pin + r.output_tokens * pout for r in records) / 1_000_000


def summarize(model: str, n: int, records: list[RunRecord]) -> str:
    def pct(xs: list[bool]) -> str:
        return f"{100 * sum(xs) / len(xs):.0f}%" if xs else "n/a"

    lines = [
        f"# Eval results: {model}", "",
        f"{datetime.now(timezone.utc):%Y-%m-%d} - {n} runs per scenario, real API, deterministic scoring "
        "(see `evals/scoring.py`).", "",
        "| Scenario | Pass | Correct category | Grounding | Efficient | Runs with tool errors | Avg tool calls | Avg iters | Avg tokens (in/out) | Avg s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in SCENARIOS:
        rs = [r for r in records if r.scenario == name]
        if not rs:
            continue
        grounding = [r.score.grounding for r in rs if r.score.grounding is not None]
        lines.append(
            f"| `{name}` | {pct([r.score.passed for r in rs])} | {pct([r.score.correct_category for r in rs])} "
            f"| {mean(grounding):.2f} | {pct([r.score.efficient for r in rs])} "
            f"| {pct([r.score.tool_errors > 0 for r in rs])} | {mean(r.score.tool_calls for r in rs):.1f} | {mean(r.iterations for r in rs):.1f} "
            f"| {mean(r.input_tokens for r in rs):.0f}/{mean(r.output_tokens for r in rs):.0f} "
            f"| {mean(r.seconds for r in rs):.0f} |" if grounding else
            f"| `{name}` | {pct([r.score.passed for r in rs])} | {pct([r.score.correct_category for r in rs])} "
            f"| n/a | {pct([r.score.efficient for r in rs])} | {pct([r.score.tool_errors > 0 for r in rs])} "
            f"| {mean(r.score.tool_calls for r in rs):.1f} "
            f"| {mean(r.iterations for r in rs):.1f} | {mean(r.input_tokens for r in rs):.0f}/"
            f"{mean(r.output_tokens for r in rs):.0f} | {mean(r.seconds for r in rs):.0f} |"
        )
    inj = [r for r in records if r.score.injection_resisted is not None]
    cost = estimate_cost(model, records)
    lines += [
        "", f"**Overall pass rate: {pct([r.score.passed for r in records])}** "
        f"({sum(r.score.passed for r in records)}/{len(records)} runs)",
    ]
    if inj:
        lines.append(f"**Injection resisted: {pct([bool(r.score.injection_resisted) for r in inj])}** "
                     f"(agent also flagged the injection in {pct([bool(r.score.mentioned_injection) for r in inj])} of runs)")
    if cost is not None:
        lines.append(f"Estimated cost of this eval: ${cost:.2f}")
    failures = [r for r in records if not r.score.passed]
    if failures:
        lines += ["", "## Failed runs"]
        for r in failures:
            detail = r.reason or ("; ".join(r.score.ungrounded) or "wrong category/other")
            lines.append(f"- `{r.scenario}` run {r.run}: status={r.status}, category="
                         f"{(r.report or {}).get('category')}, {detail}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--runs", type=int, default=3, help="runs per scenario")
    parser.add_argument("--scenarios", nargs="*", default=list(SCENARIOS), choices=list(SCENARIOS))
    parser.add_argument("--model", default=get_settings().anthropic_model)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--label", default=None, help="result file name (default: timestamp)")
    args = parser.parse_args()

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    jobs = [(s, i) for s in args.scenarios for i in range(args.runs)]
    print(f"Running {len(jobs)} investigations on {args.model} ({args.workers} in parallel)...")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, client, args.model, s, i) for s, i in jobs]
        records = []
        for future in futures:
            r = future.result()
            records.append(r)
            print(f"  {r.scenario:17s} #{r.run} {'PASS' if r.score.passed else 'FAIL'} "
                  f"status={r.status} cat={(r.report or {}).get('category')} iters={r.iterations} {r.seconds}s")

    label = args.label or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"{label}.json").write_text(json.dumps(
        {"model": args.model, "runs_per_scenario": args.runs, "records": [asdict(r) for r in records]},
        indent=2, default=str))
    summary = summarize(args.model, args.runs, records)
    (RESULTS_DIR / f"{label}.md").write_text(summary)
    print("\n" + summary)


if __name__ == "__main__":
    main()
