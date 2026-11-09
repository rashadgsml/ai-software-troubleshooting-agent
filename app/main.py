from contextlib import asynccontextmanager

import logging

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session_factory
from app.api.routes import router
from app.api.security import validate_settings_for_startup
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.investigations import mark_interrupted


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs on real server start (not in unit tests that skip lifespan).
    configure_logging()
    settings = get_settings()
    validate_settings_for_startup(settings)
    if settings.api_key in ("", "change-me"):
        logging.getLogger(__name__).warning("API_KEY is the default 'change-me'; set a real key before deploying")
    app.state.interrupted_on_startup = mark_interrupted(app.dependency_overrides.get(
        get_session_factory, get_session_factory)())
    yield


app = FastAPI(title="Troubleshooting Agent", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(factory: sessionmaker[Session] = Depends(get_session_factory)) -> dict[str, str]:
    """Readiness: can we reach the database? (No details are returned to unauthenticated callers.)"""
    try:
        with factory() as session:
            session.execute(text("select 1"))
    except Exception:
        raise HTTPException(503, "database unavailable") from None
    return {"status": "ready"}
