from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import get_session_factory
from app.api.routes import router
from app.services.investigations import mark_interrupted


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs on real server start (not in unit tests that skip lifespan).
    app.state.interrupted_on_startup = mark_interrupted(app.dependency_overrides.get(
        get_session_factory, get_session_factory)())
    yield


app = FastAPI(title="Troubleshooting Agent", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
