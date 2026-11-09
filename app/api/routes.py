from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runner import AgentRunner
from app.api.deps import get_agent_runner, get_session, get_session_factory
from app.api.security import investigation_rate_limit, rate_limit
from app.api.schemas import IncidentCreate, IncidentOut, InvestigationOut, StepOut
from app.core.config import Settings, get_settings
from app.core.redaction import redact
from app.db.models import IN_FLIGHT, Incident, Investigation, InvestigationStep
from app.services.investigations import (
    InvestigationAlreadyRunning,
    create_investigation,
    run_investigation_job,
)
from demo_app.faults import SCENARIOS

# Every route here requires a valid X-API-Key and counts against the caller's rate limit.
router = APIRouter(dependencies=[Depends(rate_limit)])


@router.post("/incidents", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(body: IncidentCreate, session: Session = Depends(get_session)):
    # Users paste logs and configs into tickets: scrub secrets before storing or sending to the model.
    incident = Incident(
        title=redact(body.title),
        description=redact(body.description or SCENARIOS[body.scenario].incident),
        scenario=body.scenario,
    )
    session.add(incident)
    session.commit()
    return incident


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
def get_incident(incident_id: int, session: Session = Depends(get_session)):
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    return incident


@router.post(
    "/incidents/{incident_id}/investigations",
    response_model=InvestigationOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(investigation_rate_limit)],
)
def start_investigation(
    incident_id: int,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    factory: sessionmaker[Session] = Depends(get_session_factory),
    runner: AgentRunner = Depends(get_agent_runner),
    settings: Settings = Depends(get_settings),
):
    """Returns immediately (202); poll GET /investigations/{id} for progress and the report."""
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    running = session.scalar(select(func.count()).select_from(Investigation).where(Investigation.status.in_(IN_FLIGHT)))
    if running >= settings.max_concurrent_investigations:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many investigations running; retry shortly",
            headers={"Retry-After": "30"},
        )
    try:
        investigation = create_investigation(session, incident, settings.anthropic_model)
    except InvestigationAlreadyRunning as exc:
        raise HTTPException(409, str(exc)) from exc
    background.add_task(run_investigation_job, investigation.id, factory, runner)
    return investigation


@router.get("/investigations/{investigation_id}", response_model=InvestigationOut)
def get_investigation(investigation_id: int, session: Session = Depends(get_session)):
    investigation = session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(404, "investigation not found")
    return investigation


@router.get("/investigations/{investigation_id}/steps", response_model=list[StepOut])
def get_steps(investigation_id: int, session: Session = Depends(get_session)):
    if session.get(Investigation, investigation_id) is None:
        raise HTTPException(404, "investigation not found")
    return session.scalars(
        select(InvestigationStep)
        .where(InvestigationStep.investigation_id == investigation_id)
        .order_by(InvestigationStep.seq)
    ).all()
