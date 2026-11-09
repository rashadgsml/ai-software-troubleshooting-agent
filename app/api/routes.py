from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runner import AgentRunner
from app.api.deps import get_agent_runner, get_session, get_session_factory
from app.api.schemas import IncidentCreate, IncidentOut, InvestigationOut, StepOut
from app.core.config import get_settings
from app.db.models import Incident, Investigation, InvestigationStep
from app.services.investigations import (
    InvestigationAlreadyRunning,
    create_investigation,
    run_investigation_job,
)
from demo_app.faults import SCENARIOS

router = APIRouter()


@router.post("/incidents", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(body: IncidentCreate, session: Session = Depends(get_session)):
    incident = Incident(
        title=body.title,
        description=body.description or SCENARIOS[body.scenario].incident,
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
)
def start_investigation(
    incident_id: int,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    factory: sessionmaker[Session] = Depends(get_session_factory),
    runner: AgentRunner = Depends(get_agent_runner),
):
    """Returns immediately (202); poll GET /investigations/{id} for progress and the report."""
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    try:
        investigation = create_investigation(session, incident, get_settings().anthropic_model)
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
