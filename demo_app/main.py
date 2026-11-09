"""Tiny demo target app. Its health endpoints reflect the injected world on disk.

Run:  DEMO_DATA_DIR=./.demo_world uvicorn demo_app.main:app --port 9000
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from demo_app.faults import SCENARIOS, build_world


def create_app(data_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Demo shop API (troubleshooting target)")
    root = Path(data_dir or os.environ.get("DEMO_DATA_DIR", ".demo_world"))

    def _health() -> dict:
        path = root / "health.json"
        if not path.exists():
            build_world(root, "healthy")
        return json.loads(path.read_text())

    @app.get("/health")
    def health():
        info = _health()["demo-app"]
        return JSONResponse(info, status_code=info["http_status"])

    @app.get("/dependencies/{name}")
    def dependency(name: str):
        data = _health()
        if name not in data or name == "demo-app":
            raise HTTPException(404, f"unknown dependency '{name}'")
        return data[name]

    @app.post("/_faults/{scenario}")
    def inject(scenario: str):
        if scenario not in SCENARIOS:
            raise HTTPException(404, f"unknown scenario '{scenario}'")
        build_world(root, scenario)
        return {"injected": scenario}

    return app


app = create_app()
