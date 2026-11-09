"""The agent loop.

Django analogy: a service-layer function that keeps calling collaborators until a
terminal condition is met. It is the ONLY code that talks to Claude, and it takes the
client as a parameter, so tests can pass a scripted fake (no network, no cost).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

import anthropic
from pydantic import ValidationError

from app.agent.prompts import (
    LAST_ITERATION_NUDGE,
    NO_REPORT_NUDGE,
    SUBMIT_REPORT_DESCRIPTION,
    SUBMIT_REPORT_TOOL_NAME,
    SYSTEM_PROMPT,
)
from app.agent.schemas import AgentResult, FinalReport, Guardrails, Step
from app.tools.registry import ToolRegistry


class MessagesClient(Protocol):
    """The slice of the Anthropic client we use (a fake can implement just this)."""

    messages: Any


def _submit_report_tool() -> dict:
    schema = FinalReport.model_json_schema()
    schema.pop("title", None)
    return {"name": SUBMIT_REPORT_TOOL_NAME, "description": SUBMIT_REPORT_DESCRIPTION, "input_schema": schema}


def run_investigation(
    incident_text: str,
    registry: ToolRegistry,
    client: MessagesClient,
    model: str,
    guardrails: Guardrails | None = None,
    on_step: Callable[[Step], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AgentResult:
    guardrails = guardrails or Guardrails()
    result = AgentResult(status="failed")
    tools = registry.to_anthropic_tools() + [_submit_report_tool()]
    messages: list[dict] = [{"role": "user", "content": f"Incident report from an engineer:\n\n{incident_text}"}]
    started = clock()
    nudged_for_report = False

    def record(kind: str, **kw: Any) -> None:
        step = Step(seq=len(result.steps), kind=kind, **kw)  # type: ignore[arg-type]
        result.steps.append(step)
        if on_step:
            on_step(step)

    def stop(status: str, reason: str) -> AgentResult:
        result.status = status  # type: ignore[assignment]
        result.reason = reason
        record("guardrail" if status == "guardrail_stopped" else "error", payload=reason)
        return result

    while True:
        # ---- guardrails checked before every model call ----
        if result.iterations >= guardrails.max_iterations:
            return stop("guardrail_stopped", f"max_iterations={guardrails.max_iterations} reached")
        if result.input_tokens + result.output_tokens >= guardrails.max_total_tokens:
            return stop("guardrail_stopped", f"max_total_tokens={guardrails.max_total_tokens} reached")
        if clock() - started >= guardrails.max_seconds:
            return stop("guardrail_stopped", f"max_seconds={guardrails.max_seconds} reached")

        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
                output_config={"effort": "medium"},
            )
        except anthropic.APIError as exc:  # SDK already retried 429/5xx with backoff
            return stop("failed", f"{type(exc).__name__}: {exc}")

        result.iterations += 1
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens

        if response.stop_reason == "refusal":
            return stop("refused", "model refused the request")
        if response.stop_reason == "max_tokens":
            return stop("failed", "model response hit max_tokens")

        # Replay the assistant turn UNCHANGED (thinking blocks must be passed back as-is).
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        for block in response.content:
            if block.type == "text" and block.text.strip():
                record("assistant_text", payload=block.text)

        if not tool_uses:
            if nudged_for_report:
                return stop("no_report", "model ended its turn without calling submit_report")
            nudged_for_report = True
            messages.append({"role": "user", "content": NO_REPORT_NUDGE})
            continue

        tool_results: list[dict] = []
        report: FinalReport | None = None
        for block in tool_uses:
            record("tool_call", tool_name=block.name, payload=block.input)
            began = clock()
            if block.name == SUBMIT_REPORT_TOOL_NAME:
                try:
                    candidate = FinalReport.model_validate(block.input)
                    out, is_error = "Report accepted.", False
                    report = candidate
                except ValidationError as exc:
                    out, is_error = f"Invalid report, fix and resubmit: {exc.errors(include_url=False)}", True
            else:
                res = registry.call(block.name, dict(block.input))
                out, is_error = res.content, res.is_error
            record(
                "tool_result", tool_name=block.name, payload=out, is_error=is_error,
                duration_ms=int((clock() - began) * 1000),
            )
            item: dict = {"type": "tool_result", "tool_use_id": block.id, "content": out}
            if is_error:
                item["is_error"] = True
            tool_results.append(item)

        if report is not None:
            result.report, result.status = report, "completed"
            record("report", payload=report.model_dump())
            return result

        # All results for this turn go back in ONE user message (tool_results first).
        content: list[dict] = list(tool_results)
        if result.iterations == guardrails.max_iterations - 1:
            content.append({"type": "text", "text": LAST_ITERATION_NUDGE})
        messages.append({"role": "user", "content": content})
