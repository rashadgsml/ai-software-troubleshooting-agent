"""Investigation lifecycle: create, run in the background, persist every step."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runner import AgentRunner
from app.agent.schemas import Step
from app.core.logging import investigation_id_var
from app.core.redaction import redact, redact_json
from app.db.models import IN_FLIGHT, Incident, Investigation, InvestigationStep

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InvestigationAlreadyRunning(Exception):
    pass


def create_investigation(session: Session, incident: Incident, model: str) -> Investigation:
    """One in-flight investigation per incident (prevents double-click cost spikes)."""
    busy = session.scalar(
        select(Investigation.id).where(
            Investigation.incident_id == incident.id, Investigation.status.in_(IN_FLIGHT)
        )
    )
    if busy:
        raise InvestigationAlreadyRunning(f"investigation {busy} is still {IN_FLIGHT}")
    investigation = Investigation(incident_id=incident.id, model=model, status="pending")
    session.add(investigation)
    session.commit()
    return investigation


def _json_safe(value: object) -> object:
    """JSON-serialisable AND redacted: the model may echo a secret into its own text/report."""
    return redact_json(json.loads(json.dumps(value, default=str)))


def run_investigation_job(
    investigation_id: int, session_factory: sessionmaker[Session], runner: AgentRunner
) -> None:
    """Background task body. Opens its own sessions (the request's session is long closed)
    and commits each step immediately, so a crash still leaves a usable trace."""
    investigation_id_var.set(str(investigation_id))
    began = time.monotonic()
    with session_factory() as session:
        investigation = session.get(Investigation, investigation_id)
        if investigation is None:
            return
        incident = investigation.incident
        incident_text, scenario = f"{incident.title}\n\n{incident.description}", incident.scenario
        investigation.status, investigation.started_at = "running", _now()
        session.commit()
    log.info("investigation started (scenario=%s)", scenario)

    def on_step(step: Step) -> None:
        with session_factory() as s:
            s.add(InvestigationStep(
                investigation_id=investigation_id, seq=step.seq, kind=step.kind,
                tool_name=step.tool_name, payload=_json_safe(step.payload),
                is_error=step.is_error, duration_ms=step.duration_ms,
            ))
            s.commit()

    try:
        result = runner(incident_text, scenario, on_step)
        status, reason, report = result.status, result.reason, result.report
        usage = (result.iterations, result.input_tokens, result.output_tokens)
    except Exception as exc:  # never leave a run stuck in "running"
        log.exception("investigation %s crashed", investigation_id)
        status, reason, report, usage = "failed", redact(f"{type(exc).__name__}: {exc}"), None, (0, 0, 0)

    with session_factory() as session:
        investigation = session.get(Investigation, investigation_id)
        investigation.status, investigation.reason = status, redact(reason) if reason else None
        investigation.final_report = _json_safe(report.model_dump()) if report else None
        investigation.iterations, investigation.input_tokens, investigation.output_tokens = usage
        investigation.finished_at = _now()
        session.commit()
    log.info("investigation finished status=%s iterations=%s tokens_in=%s tokens_out=%s seconds=%.1f",
             status, usage[0], usage[1], usage[2], time.monotonic() - began)


def mark_interrupted(session_factory: sessionmaker[Session]) -> int:
    """Startup sweep: runs that were in flight when the process died can never finish.

    Single-instance assumption: with several API replicas this would also fail runs that
    belong to a live peer. A heartbeat/lease column would fix that (see README).
    """
    with session_factory() as session:
        result = session.execute(
            update(Investigation)
            .where(Investigation.status.in_(IN_FLIGHT))
            .values(status="failed", reason="interrupted by server restart", finished_at=_now())
        )
        session.commit()
        return result.rowcount or 0
