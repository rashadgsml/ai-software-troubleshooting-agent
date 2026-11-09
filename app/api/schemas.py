from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from demo_app.faults import SCENARIOS

ScenarioName = Literal[tuple(SCENARIOS)]  # type: ignore[valid-type]


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(None, max_length=5000, description="Defaults to the scenario's sample ticket.")
    scenario: ScenarioName = Field(description="Which demo-world fault to inject for this incident.")


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str
    scenario: str
    created_at: datetime


class InvestigationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    incident_id: int
    model: str
    status: str
    reason: str | None
    iterations: int
    input_tokens: int
    output_tokens: int
    final_report: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    kind: str
    tool_name: str | None
    payload: Any
    is_error: bool
    duration_ms: int | None
    created_at: datetime
