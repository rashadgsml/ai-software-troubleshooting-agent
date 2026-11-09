from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Investigation.status values. pending/running are in-flight; the rest are terminal and
# mirror AgentResult.status (completed, guardrail_stopped, no_report, refused, failed).
IN_FLIGHT = ("pending", "running")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    # Which demo-world state to inject when investigating (see demo_app.faults.SCENARIOS).
    scenario: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    investigations: Mapped[list[Investigation]] = relationship(back_populates="incident")


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), index=True, default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    final_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    incident: Mapped[Incident] = relationship(back_populates="investigations")
    steps: Mapped[list[InvestigationStep]] = relationship(
        back_populates="investigation", order_by="InvestigationStep.seq", cascade="all, delete-orphan"
    )


class InvestigationStep(Base):
    """One event in the agent's run: the audit trail."""

    __tablename__ = "investigation_steps"
    __table_args__ = (Index("ix_steps_investigation_seq", "investigation_id", "seq", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    tool_name: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[Any | None] = mapped_column(JSONB)
    is_error: Mapped[bool] = mapped_column(default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    investigation: Mapped[Investigation] = relationship(back_populates="steps")
