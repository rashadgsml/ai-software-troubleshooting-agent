import logging

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.agent.runner import default_agent_runner
from app.agent.schemas import AgentResult, Step
from app.api.deps import get_session_factory
from app.core.logging import configure_logging
from app.db.models import Investigation
from app.main import app
from tests.integration.conftest import TEST_API_KEY, happy_replies
from tests.unit.test_orchestrator import VALID_REPORT, reply, tool_use


def new_incident(client, **extra):
    resp = client.post("/incidents", json={"title": "API returns 500s", "scenario": "disk_full", **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---- authentication ------------------------------------------------------------------
@pytest.mark.parametrize("method,path", [
    ("post", "/incidents"), ("get", "/incidents/1"), ("post", "/incidents/1/investigations"),
    ("get", "/investigations/1"), ("get", "/investigations/1/steps"),
])
def test_every_route_requires_an_api_key(api, method, path):
    client, _ = api
    resp = getattr(client, method)(path, headers={"X-API-Key": ""}, **({"json": {}} if method == "post" else {}))
    assert resp.status_code == 401 and resp.headers["www-authenticate"] == "ApiKey"


def test_wrong_keys_are_rejected_including_different_lengths(api):
    client, _ = api
    for bad in ["wrong", TEST_API_KEY + "x", TEST_API_KEY[:-1], "A" * 500]:
        assert client.get("/incidents/1", headers={"X-API-Key": bad}).status_code == 401


def test_health_and_ready_are_public(api):
    client, _ = api
    assert client.get("/health", headers={"X-API-Key": ""}).status_code == 200
    assert client.get("/ready", headers={"X-API-Key": ""}).json() == {"status": "ready"}


def test_ready_reports_503_without_leaking_details(api):
    client, _ = api

    def broken_factory():
        raise RuntimeError("password=hunter2 host=db")

    app.dependency_overrides[get_session_factory] = lambda: broken_factory
    resp = client.get("/ready")
    assert resp.status_code == 503 and "hunter2" not in resp.text


def test_repeated_failed_auth_is_throttled_per_ip(api, limiters):
    client, _ = api
    limiters.failed_auth.limit = 3
    bad = {"X-API-Key": "nope"}
    assert [client.get("/incidents/1", headers=bad).status_code for _ in range(3)] == [401, 401, 401]
    blocked = client.get("/incidents/1", headers=bad)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    assert client.get("/incidents/999").status_code == 404  # a valid key still gets through


# ---- rate limits and cost controls --------------------------------------------------------
def test_general_rate_limit(api, limiters):
    client, _ = api
    limiters.general.limit = 3
    assert [client.get("/incidents/999").status_code for _ in range(3)] == [404, 404, 404]
    limited = client.get("/incidents/999")
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1


def test_starting_investigations_has_its_own_tighter_limit(api, limiters, scripted_runner):
    client, use_runner = api
    limiters.investigations.limit = 1
    use_runner(scripted_runner(*happy_replies()))
    first, second = new_incident(client), new_incident(client)
    assert client.post(f"/incidents/{first['id']}/investigations").status_code == 202
    assert client.post(f"/incidents/{second['id']}/investigations").status_code == 429
    assert client.get(f"/incidents/{second['id']}").status_code == 200  # other routes unaffected


def test_concurrent_investigation_cap(api, session_factory, test_settings, scripted_runner):
    client, use_runner = api
    use_runner(scripted_runner(*happy_replies()))
    test_settings.max_concurrent_investigations = 2
    ids = [new_incident(client)["id"] for _ in range(3)]
    with session_factory() as s:
        for incident_id in ids[:2]:
            s.add(Investigation(incident_id=incident_id, model="m", status="running"))
        s.commit()
    resp = client.post(f"/incidents/{ids[2]}/investigations")
    assert resp.status_code == 429 and resp.headers["retry-after"] == "30"
    with session_factory() as s:  # nothing was created or started
        assert s.scalar(sa.select(sa.func.count()).select_from(Investigation)) == 2


# ---- secrets never reach storage -----------------------------------------------------------
def test_incident_text_is_redacted_before_storage(api):
    client, _ = api
    incident = new_incident(
        client, title="DB down postgresql://shop:S3cretPw!@db/x",
        description="config: api_key=sk_live_abc123456 AWS AKIAIOSFODNN7EXAMPLE",
    )
    fetched = client.get(f"/incidents/{incident['id']}").json()
    blob = str(fetched)
    for secret in ("S3cretPw", "sk_live_abc123456", "AKIAIOSFODNN7EXAMPLE"):
        assert secret not in blob


def test_secrets_echoed_by_the_model_are_redacted_in_storage(api, scripted_runner):
    client, use_runner = api
    leaky = dict(VALID_REPORT, summary="Leaked: postgresql://shop:S3cretPw!@db/x",
                 recommended_steps=["rotate sk_live_abc123456789"])
    use_runner(scripted_runner(reply(tool_use("t1", "submit_report", **leaky))))
    inv_id = client.post(f"/incidents/{new_incident(client)['id']}/investigations").json()["id"]
    stored = str(client.get(f"/investigations/{inv_id}").json()) + str(client.get(f"/investigations/{inv_id}/steps").json())
    assert "S3cretPw" not in stored and "sk_live_abc123456789" not in stored


def test_failure_reasons_are_redacted(api):
    client, use_runner = api

    def runner(text, scenario, on_step):
        raise RuntimeError("could not connect postgresql://shop:S3cretPw!@db/x")

    use_runner(runner)
    inv_id = client.post(f"/incidents/{new_incident(client)['id']}/investigations").json()["id"]
    reason = client.get(f"/investigations/{inv_id}").json()["reason"]
    assert "S3cretPw" not in reason and "could not connect" in reason


# ---- logging, timeouts ------------------------------------------------------------------------
def test_job_logs_carry_the_investigation_id(api, scripted_runner, caplog):
    configure_logging()
    client, use_runner = api
    use_runner(scripted_runner(*happy_replies()))
    with caplog.at_level(logging.INFO, logger="app.services.investigations"):
        inv_id = client.post(f"/incidents/{new_incident(client)['id']}/investigations").json()["id"]
    records = [r for r in caplog.records if r.name == "app.services.investigations"]
    assert {r.investigation_id for r in records} == {str(inv_id)}
    assert any("finished status=completed" in r.getMessage() for r in records)


def test_database_statement_timeout_is_configured():
    get_session_factory.cache_clear() if hasattr(get_session_factory, "cache_clear") else None
    from app.db.base import get_session_factory as real_factory

    real_factory.cache_clear()
    with real_factory()() as session:
        assert session.scalar(sa.text("show statement_timeout")) == "15s"
    real_factory.cache_clear()


def test_claude_client_gets_explicit_timeout_and_retries(monkeypatch, test_settings):
    import app.agent.runner as runner_module

    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeTools:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(runner_module.anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(runner_module, "McpToolProvider", lambda params: FakeTools())
    monkeypatch.setattr(runner_module, "run_investigation", lambda *a, **k: AgentResult(status="completed"))
    monkeypatch.setattr(runner_module, "get_settings", lambda: test_settings)
    default_agent_runner("text", "healthy", lambda step: None)
    assert captured["timeout"] == test_settings.anthropic_timeout_s
    assert captured["max_retries"] == test_settings.anthropic_max_retries
