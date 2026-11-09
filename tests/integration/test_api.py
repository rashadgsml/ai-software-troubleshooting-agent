import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.agent.schemas import Guardrails
from app.db.models import Incident, Investigation
from app.main import app
from app.services.investigations import mark_interrupted
from tests.integration.conftest import happy_replies
from tests.unit.test_orchestrator import reply, tool_use


def new_incident(client, scenario="disk_full", **extra):
    resp = client.post("/incidents", json={"title": "API returns 500s", "scenario": scenario, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_incident_defaults_description_from_scenario(api):
    client, _ = api
    incident = new_incident(client)
    assert "uploads fail" in incident["description"]
    assert client.get(f"/incidents/{incident['id']}").json()["scenario"] == "disk_full"


def test_create_incident_validation(api):
    client, _ = api
    assert client.post("/incidents", json={"title": "x", "scenario": "disk_full"}).status_code == 422
    assert client.post("/incidents", json={"title": "valid title", "scenario": "meteor"}).status_code == 422


def test_full_investigation_flow_persists_report_and_trace(api, scripted_runner):
    client, use_runner = api
    use_runner(scripted_runner(*happy_replies()))
    incident = new_incident(client)

    started = client.post(f"/incidents/{incident['id']}/investigations")
    assert started.status_code == 202 and started.json()["status"] == "pending"
    inv_id = started.json()["id"]

    inv = client.get(f"/investigations/{inv_id}").json()
    assert inv["status"] == "completed"
    assert inv["final_report"]["category"] == "disk_full"
    assert inv["iterations"] == 2 and inv["input_tokens"] == 200
    assert inv["started_at"] and inv["finished_at"]

    steps = client.get(f"/investigations/{inv_id}/steps").json()
    assert [s["seq"] for s in steps] == list(range(len(steps)))
    assert [s["kind"] for s in steps] == [
        "tool_call", "tool_result", "tool_call", "tool_result", "tool_call", "tool_result", "report",
    ]
    results = {s["tool_name"]: s for s in steps if s["kind"] == "tool_result"}
    assert "disk_used_pct" in results["check_resources"]["payload"]
    assert results["check_resources"]["duration_ms"] is not None


def test_persisted_trace_never_contains_secrets(api, scripted_runner):
    client, use_runner = api
    use_runner(scripted_runner(*happy_replies()))
    inv_id = client.post(f"/incidents/{new_incident(client)['id']}/investigations").json()["id"]
    dump = str(client.get(f"/investigations/{inv_id}/steps").json())
    assert "read_config" in dump and "S3cretPw" not in dump and "sk_live" not in dump


def test_not_found(api):
    client, _ = api
    assert client.get("/incidents/999").status_code == 404
    assert client.post("/incidents/999/investigations").status_code == 404
    assert client.get("/investigations/999").status_code == 404
    assert client.get("/investigations/999/steps").status_code == 404


def test_second_investigation_while_in_flight_is_409(api, session_factory, scripted_runner):
    client, use_runner = api
    use_runner(scripted_runner(*happy_replies()))
    incident = new_incident(client)
    with session_factory() as s:
        s.add(Investigation(incident_id=incident["id"], model="m", status="running"))
        s.commit()
    assert client.post(f"/incidents/{incident['id']}/investigations").status_code == 409


def test_new_investigation_allowed_after_previous_finished(api, scripted_runner):
    client, use_runner = api
    incident = new_incident(client)
    use_runner(scripted_runner(*happy_replies()))
    assert client.post(f"/incidents/{incident['id']}/investigations").status_code == 202
    use_runner(scripted_runner(*happy_replies()))
    assert client.post(f"/incidents/{incident['id']}/investigations").status_code == 202


def test_runner_crash_is_recorded_not_left_running(api):
    client, use_runner = api

    def exploding_runner(text, scenario, on_step):
        raise RuntimeError("boom")

    use_runner(exploding_runner)
    inv_id = client.post(f"/incidents/{new_incident(client)['id']}/investigations").json()["id"]
    inv = client.get(f"/investigations/{inv_id}").json()
    assert inv["status"] == "failed" and "RuntimeError: boom" in inv["reason"]
    assert inv["finished_at"]


def test_steps_written_before_a_crash_survive(api, scripted_runner):
    client, use_runner = api
    from app.agent.schemas import Step

    def crashing_runner(text, scenario, on_step):
        on_step(Step(seq=0, kind="tool_call", tool_name="search_logs", payload={"query": "x"}))
        raise RuntimeError("died mid-run")

    use_runner(crashing_runner)
    inv_id = client.post(f"/incidents/{new_incident(client)['id']}/investigations").json()["id"]
    steps = client.get(f"/investigations/{inv_id}/steps").json()
    assert len(steps) == 1 and steps[0]["tool_name"] == "search_logs"


def test_guardrail_stop_is_persisted(api, scripted_runner):
    client, use_runner = api
    use_runner(scripted_runner(
        *[reply(tool_use(f"t{i}", "check_resources")) for i in range(5)],
        guardrails=Guardrails(max_iterations=2),
    ))
    inv_id = client.post(f"/incidents/{new_incident(client)['id']}/investigations").json()["id"]
    inv = client.get(f"/investigations/{inv_id}").json()
    assert inv["status"] == "guardrail_stopped" and "max_iterations" in inv["reason"]
    assert inv["final_report"] is None


def test_mark_interrupted_fails_only_in_flight_runs(api, session_factory):
    client, _ = api
    incident = new_incident(client)
    with session_factory() as s:
        for status in ("pending", "running", "completed"):
            s.add(Investigation(incident_id=incident["id"], model="m", status=status))
        s.commit()
    assert mark_interrupted(session_factory) == 2
    with session_factory() as s:
        statuses = sorted(s.scalars(sa.select(Investigation.status)))
    assert statuses == ["completed", "failed", "failed"]


def test_startup_lifespan_runs_the_sweep(api, session_factory):
    client, _ = api
    incident = new_incident(client)
    with session_factory() as s:
        s.add(Investigation(incident_id=incident["id"], model="m", status="running"))
        s.commit()
    with TestClient(app):  # entering the context runs lifespan startup
        pass
    with session_factory() as s:
        inv = s.scalars(sa.select(Investigation)).one()
    assert inv.status == "failed" and inv.reason == "interrupted by server restart"
