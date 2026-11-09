"""Re-score a saved eval run without calling the API (free).

    .venv/bin/python -m evals.rescore baseline-sonnet-5-5

Use after changing evals/scoring.py: the model's traces stay fixed, so any change in
results is due to the scorer alone.
"""

import argparse
import json

from app.agent.schemas import AgentResult, FinalReport, Step
from demo_app.faults import SCENARIOS
from evals.run import INJECTION_SCENARIOS, RESULTS_DIR, RunRecord, summarize
from evals.scoring import score_run


def rescore(record: dict) -> RunRecord:
    steps = [Step(seq=i, kind=t["kind"], tool_name=t["tool"], payload=t["payload"], is_error=t["is_error"])
             for i, t in enumerate(record["trace"])]
    report = FinalReport.model_validate(record["report"]) if record["report"] else None
    result = AgentResult(
        status=record["status"], report=report, steps=steps, iterations=record["iterations"],
        input_tokens=record["input_tokens"], output_tokens=record["output_tokens"], reason=record["reason"],
    )
    score = score_run(SCENARIOS[record["scenario"]], result, injection=record["scenario"] in INJECTION_SCENARIOS)
    return RunRecord(**{**record, "score": score})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    label = parser.parse_args().label
    data = json.loads((RESULTS_DIR / f"{label}.json").read_text())
    records = [rescore(r) for r in data["records"]]
    summary = summarize(data["model"], data["runs_per_scenario"], records)
    (RESULTS_DIR / f"{label}.md").write_text(summary)
    print(summary)


if __name__ == "__main__":
    main()
