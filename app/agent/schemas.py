from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(description="Name of the tool whose output supports the claim.")
    finding: str = Field(description="The specific fact observed, quoting the output where possible.")


class FinalReport(BaseModel):
    """The structured conclusion Claude must submit. Also the `submit_report` tool schema."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="One or two sentences for an on-call engineer.")
    root_cause: str = Field(description="The most likely root cause, or 'no issue found'.")
    category: Literal[
        "db_connection_pool_exhausted",
        "bad_config_after_deploy",
        "disk_full",
        "upstream_dependency_slow",
        "none",
        "other",
    ] = Field(description="Closest matching category.")
    confidence: Literal["low", "medium", "high"]
    evidence: list[Evidence] = Field(min_length=1, description="Evidence from tool outputs backing the root cause.")
    recommended_steps: list[str] = Field(description="Concrete next steps for a human to take.")


@dataclass
class Step:
    """One recorded event in an investigation (persisted in Phase 5)."""

    seq: int
    kind: Literal["assistant_text", "tool_call", "tool_result", "report", "guardrail", "error"]
    tool_name: str | None = None
    payload: Any = None
    is_error: bool = False
    duration_ms: int | None = None


@dataclass
class Guardrails:
    max_iterations: int = 12  # model round-trips
    max_total_tokens: int = 120_000  # input + output, summed across calls
    max_seconds: float = 180.0


Status = Literal["completed", "guardrail_stopped", "no_report", "refused", "failed"]


@dataclass
class AgentResult:
    status: Status
    report: FinalReport | None = None
    steps: list[Step] = field(default_factory=list)
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reason: str | None = None
