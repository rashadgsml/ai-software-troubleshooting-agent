import json

import pytest

from app.core.redaction import redact
from app.tools.registry import MAX_OUTPUT_CHARS, Tool, ToolContext, ToolRegistry
from demo_app.faults import SCENARIOS


def call(registry, name, **args):
    result = registry.call(name, args)
    assert not result.is_error, result.content
    return result.content


# --- registry behaviour ----------------------------------------------------------
def test_anthropic_tool_definitions_have_required_shape(make_registry):
    defs = make_registry("healthy").to_anthropic_tools()
    assert {d["name"] for d in defs} == {
        "search_logs", "check_http_health", "get_recent_deployments",
        "read_config", "check_resources", "query_db_stats", "lookup_runbook",
    }
    for d in defs:
        assert d["description"]
        assert d["input_schema"]["type"] == "object"


def test_unknown_tool_is_error_result_not_exception(make_registry):
    result = make_registry("healthy").call("rm_rf", {})
    assert result.is_error and "Unknown tool" in result.content


@pytest.mark.parametrize("name,args", [
    ("check_http_health", {"target": "../../etc/passwd"}),  # not in enum
    ("search_logs", {"limit": 10_000}),  # out of range
    ("search_logs", {"path": "/etc/passwd"}),  # extra field forbidden
    ("lookup_runbook", {}),  # missing required
])
def test_invalid_arguments_are_rejected(make_registry, name, args):
    result = make_registry("healthy").call(name, args)
    assert result.is_error and "Invalid arguments" in result.content


def test_handler_exception_becomes_error_result(tmp_path):
    from pydantic import BaseModel

    class Empty(BaseModel):
        pass

    registry = ToolRegistry(ToolContext(tmp_path, None, tmp_path))  # type: ignore[arg-type]
    registry.register(Tool("boom", "x", Empty, lambda a, c: 1 / 0))
    result = registry.call("boom", {})
    assert result.is_error and "ZeroDivisionError" in result.content


def test_output_is_truncated(tmp_path):
    from pydantic import BaseModel

    class Empty(BaseModel):
        pass

    registry = ToolRegistry(ToolContext(tmp_path, None, tmp_path))  # type: ignore[arg-type]
    registry.register(Tool("big", "x", Empty, lambda a, c: "a" * (MAX_OUTPUT_CHARS * 2)))
    result = registry.call("big", {})
    assert "truncated" in result.content and len(result.content) < MAX_OUTPUT_CHARS + 100


# --- redaction -----------------------------------------------------------------------
def test_redact_url_password_and_keys():
    text = "postgresql://shop:S3cretPw!@db:5432/shop key sk_live_abc123 Authorization: Bearer abc.def"
    out = redact(text)
    assert "S3cretPw" not in out and "sk_live_abc123" not in out and "abc.def" not in out
    assert "db:5432/shop" in out  # non-secret parts survive


def test_read_config_never_leaks_secrets(make_registry):
    out = call(make_registry("healthy"), "read_config")
    assert "S3cretPw" not in out and "sk_live" not in out
    assert "payments.internal" in out  # useful content is still there


# --- tools against each fault scenario ----------------------------------------------------
def test_search_logs_filters_by_level_and_query(make_registry):
    registry = make_registry("disk_full")
    errors = json.loads(call(registry, "search_logs", level="ERROR"))
    assert errors["total_matches"] == 3
    assert all(" ERROR " in ln for ln in errors["returned"])
    hits = json.loads(call(registry, "search_logs", query="no space left"))
    assert hits["total_matches"] == 3


def test_pool_exhausted_evidence(make_registry):
    registry = make_registry("pool_exhausted")
    stats = json.loads(call(registry, "query_db_stats"))
    assert stats["active_connections"] == stats["pool_size"] and stats["idle_in_transaction"] > 0
    assert "QueuePool" in call(registry, "search_logs", query="queuepool")


def test_bad_config_evidence_is_in_deployments(make_registry):
    registry = make_registry("bad_config")
    latest = json.loads(call(registry, "get_recent_deployments", limit=1))[0]
    assert "payment_service_url" in latest["config_changes"]
    assert "internall" in call(registry, "read_config")


def test_disk_full_evidence(make_registry):
    assert json.loads(call(make_registry("disk_full"), "check_resources"))["disk_used_pct"] > 99


def test_upstream_timeout_health_probe(make_registry):
    registry = make_registry("upstream_timeout")
    body = json.loads(call(registry, "check_http_health", target="inventory-service"))["body"]
    assert body["latency_ms"] > 5000
    assert json.loads(call(registry, "check_http_health", target="payment-service"))["body"]["status"] == "healthy"


def test_demo_app_health_reflects_fault(make_registry):
    result = json.loads(call(make_registry("pool_exhausted"), "check_http_health", target="demo-app"))
    assert result["http_status"] == 503


def test_healthy_world_has_no_errors(make_registry):
    registry = make_registry("healthy")
    assert json.loads(call(registry, "search_logs", level="ERROR"))["total_matches"] == 0
    assert json.loads(call(registry, "check_http_health", target="demo-app"))["http_status"] == 200


@pytest.mark.parametrize("scenario,expected", [
    ("pool_exhausted", "db-connection-pool"),
    ("bad_config", "bad-config-after-deploy"),
    ("disk_full", "disk-full"),
    ("upstream_timeout", "slow-upstream-dependency"),
])
def test_runbook_lookup_finds_matching_runbook(make_registry, scenario, expected):
    symptoms = {
        "pool_exhausted": "connection pool timed out idle in transaction",
        "bad_config": "config wrong url after deploy",
        "disk_full": "no space left on device",
        "upstream_timeout": "ReadTimeout dependency slow",
    }[scenario]
    matches = json.loads(call(make_registry(scenario), "lookup_runbook", query=symptoms))["matches"]
    assert matches and matches[0]["runbook"] == expected


def test_every_scenario_builds(make_registry):
    for name in SCENARIOS:
        assert make_registry(name).names
