"""Orchestrator tests with a scripted fake Claude client: no network, no cost."""

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.agent.orchestrator import run_investigation
from app.agent.schemas import Guardrails

VALID_REPORT = {
    "summary": "Disk full.",
    "root_cause": "Disk at 99.8%",
    "category": "disk_full",
    "confidence": "high",
    "evidence": [{"tool": "check_resources", "finding": "disk_used_pct 99.8"}],
    "recommended_steps": ["Rotate logs"],
}


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, name, **inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


def reply(*blocks, stop="tool_use", tokens=(100, 50)):
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop,
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


class FakeClient:
    """Returns scripted responses in order and records every request."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        # deep-ish snapshot: messages list is mutated by the orchestrator afterwards
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def run(client, registry, **kw):
    return run_investigation("checkout broken", registry, client, "test-model", **kw)


def test_happy_path_tool_then_report(make_registry):
    client = FakeClient(
        reply(text("checking"), tool_use("t1", "check_resources")),
        reply(tool_use("t2", "submit_report", **VALID_REPORT)),
    )
    result = run(client, make_registry("disk_full"))

    assert result.status == "completed" and result.report.category == "disk_full"
    assert result.iterations == 2 and result.input_tokens == 200
    assert [s.kind for s in result.steps] == [
        "assistant_text", "tool_call", "tool_result", "tool_call", "tool_result", "report",
    ]
    # second request carries the assistant turn + a tool_result tied to the right id
    last_user = client.requests[1]["messages"][-1]
    assert last_user["role"] == "user"
    assert last_user["content"][0]["tool_use_id"] == "t1"
    assert "disk_used_pct" in last_user["content"][0]["content"]


def test_request_includes_system_prompt_and_all_tools(make_registry):
    client = FakeClient(reply(tool_use("t", "submit_report", **VALID_REPORT)))
    run(client, make_registry("healthy"))
    req = client.requests[0]
    names = {t["name"] for t in req["tools"]}
    assert "submit_report" in names and "search_logs" in names
    assert "untrusted" in req["system"]
    assert "tool_choice" not in req  # forced tool use is unsupported on this model


def test_parallel_tool_calls_return_in_one_user_message(make_registry):
    client = FakeClient(
        reply(tool_use("a", "check_resources"), tool_use("b", "query_db_stats")),
        reply(tool_use("c", "submit_report", **VALID_REPORT)),
    )
    run(client, make_registry("healthy"))
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_bad_tool_call_is_fed_back_as_error_and_loop_continues(make_registry):
    client = FakeClient(
        reply(tool_use("t1", "search_logs", limit=99999), tool_use("t2", "nope")),
        reply(tool_use("t3", "submit_report", **VALID_REPORT)),
    )
    result = run(client, make_registry("healthy"))
    results = client.requests[1]["messages"][-1]["content"]
    assert all(r.get("is_error") for r in results)
    assert result.status == "completed"


def test_invalid_report_is_rejected_then_corrected(make_registry):
    bad = {**VALID_REPORT, "confidence": "certain"}
    client = FakeClient(
        reply(tool_use("t1", "submit_report", **bad)),
        reply(tool_use("t2", "submit_report", **VALID_REPORT)),
    )
    result = run(client, make_registry("healthy"))
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True
    assert result.status == "completed"


def test_text_only_answer_gets_one_nudge_then_no_report(make_registry):
    client = FakeClient(
        reply(text("It's the disk."), stop="end_turn"),
        reply(text("Really, the disk."), stop="end_turn"),
    )
    result = run(client, make_registry("healthy"))
    assert "submit_report" in client.requests[1]["messages"][-1]["content"]
    assert result.status == "no_report" and result.report is None


def test_max_iterations_guardrail_and_last_iteration_nudge(make_registry):
    client = FakeClient(*[reply(tool_use(f"t{i}", "check_resources")) for i in range(5)])
    result = run(client, make_registry("healthy"), guardrails=Guardrails(max_iterations=3))
    assert result.status == "guardrail_stopped" and result.iterations == 3
    assert "max_iterations" in result.reason
    # the turn before the cap tells the model to wrap up
    final_content = client.requests[2]["messages"][-1]["content"]
    assert any(b.get("type") == "text" and "submit_report" in b["text"] for b in final_content)


def test_token_budget_guardrail(make_registry):
    client = FakeClient(*[reply(tool_use(f"t{i}", "check_resources"), tokens=(900, 200)) for i in range(5)])
    result = run(client, make_registry("healthy"), guardrails=Guardrails(max_total_tokens=2000))
    assert result.status == "guardrail_stopped" and "max_total_tokens" in result.reason


def test_wall_clock_guardrail(make_registry):
    ticks = iter(range(0, 1000, 100))  # each clock() call advances 100s
    client = FakeClient(*[reply(tool_use(f"t{i}", "check_resources")) for i in range(5)])
    result = run(client, make_registry("healthy"), guardrails=Guardrails(max_seconds=150), clock=lambda: next(ticks))
    assert result.status == "guardrail_stopped" and "max_seconds" in result.reason


def test_api_error_ends_run_as_failed(make_registry):
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    result = run(FakeClient(err), make_registry("healthy"))
    assert result.status == "failed" and "APIConnectionError" in result.reason


def test_refusal_and_truncation_are_reported(make_registry):
    assert run(FakeClient(reply(stop="refusal")), make_registry("healthy")).status == "refused"
    assert run(FakeClient(reply(stop="max_tokens")), make_registry("healthy")).status == "failed"


def test_on_step_callback_receives_every_step(make_registry):
    seen = []
    client = FakeClient(reply(tool_use("t", "submit_report", **VALID_REPORT)))
    result = run(client, make_registry("healthy"), on_step=seen.append)
    assert seen == result.steps


def test_secrets_never_reach_the_model(make_registry):
    client = FakeClient(
        reply(tool_use("t1", "read_config")),
        reply(tool_use("t2", "submit_report", **VALID_REPORT)),
    )
    run(client, make_registry("healthy"))
    sent = str(client.requests[1]["messages"])
    assert "S3cretPw" not in sent and "sk_live" not in sent
