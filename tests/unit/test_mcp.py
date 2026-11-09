"""MCP layer: server exposure, client adapter, and the agent running through MCP."""

import json
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from app.agent.mcp_client import McpToolProvider
from app.agent.orchestrator import run_investigation
from app.mcp_server.server import build_server
from demo_app.faults import build_world
from tests.unit.test_orchestrator import VALID_REPORT, FakeClient, reply, tool_use

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend():
    return "asyncio"  # anyio's pytest plugin runs the async tests below


pytestmark = pytest.mark.anyio


async def test_server_lists_exactly_the_registry_tools(make_registry):
    registry = make_registry("healthy")
    async with Client(build_server(registry)) as client:
        listed = (await client.list_tools()).tools
    assert sorted(t.name for t in listed) == sorted(registry.names)
    expected = {d["name"]: d for d in registry.to_anthropic_tools()}
    for tool in listed:
        assert tool.description == expected[tool.name]["description"]
        assert tool.input_schema == expected[tool.name]["input_schema"]


async def test_server_call_returns_registry_output(make_registry):
    async with Client(build_server(make_registry("disk_full"))) as client:
        result = await client.call_tool("check_resources", {})
    assert not result.is_error
    assert json.loads(result.content[0].text)["disk_used_pct"] == 99.8


async def test_server_validation_and_redaction_still_apply(make_registry):
    async with Client(build_server(make_registry("healthy"))) as client:
        bad = await client.call_tool("check_http_health", {"target": "../../etc/passwd"})
        unknown = await client.call_tool("rm_rf", {})
        config = await client.call_tool("read_config", {})
    assert bad.is_error and "Invalid arguments" in bad.content[0].text
    assert unknown.is_error
    assert "S3cretPw" not in config.content[0].text and "sk_live" not in config.content[0].text


def test_provider_matches_registry_definitions(make_registry):
    registry = make_registry("healthy")
    with McpToolProvider(build_server(registry)) as provider:
        defs = {d["name"]: d for d in provider.to_anthropic_tools()}
    assert defs.keys() == set(registry.names)
    assert defs["check_http_health"]["input_schema"]["properties"]["target"]["enum"]


def test_provider_call_and_error_flag(make_registry):
    with McpToolProvider(build_server(make_registry("pool_exhausted"))) as provider:
        ok = provider.call("query_db_stats", {})
        bad = provider.call("search_logs", {"limit": 10_000})
    assert not ok.is_error and json.loads(ok.content)["idle_in_transaction"] == 8
    assert bad.is_error


def test_agent_runs_through_mcp(make_registry):
    client = FakeClient(
        reply(tool_use("t1", "query_db_stats")),
        reply(tool_use("t2", "submit_report", **VALID_REPORT)),
    )
    with McpToolProvider(build_server(make_registry("pool_exhausted"))) as provider:
        result = run_investigation("checkout broken", provider, client, "test-model")
    assert result.status == "completed"
    assert "idle_in_transaction" in client.requests[1]["messages"][-1]["content"][0]["content"]
    assert "submit_report" in {t["name"] for t in client.requests[0]["tools"]}


def test_real_stdio_subprocess_round_trip(tmp_path):
    """Spawns `python -m app.mcp_server` and talks to it over stdin/stdout."""
    build_world(tmp_path, "bad_config")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_server"],
        env={"DEMO_DATA_DIR": str(tmp_path)}, cwd=PROJECT_ROOT,
    )
    with McpToolProvider(params) as provider:
        assert "get_recent_deployments" in {d["name"] for d in provider.to_anthropic_tools()}
        deploys = json.loads(provider.call("get_recent_deployments", {"limit": 1}).content)
        health = json.loads(provider.call("check_http_health", {"target": "demo-app"}).content)
        config = provider.call("read_config", {}).content
    assert "payment_service_url" in deploys[0]["config_changes"]
    assert health["http_status"] == 500
    assert "S3cretPw" not in config


def test_provider_surfaces_startup_failure(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-c", "import sys; sys.exit(1)"])
    with pytest.raises(RuntimeError, match="could not start MCP client"):
        McpToolProvider(params).__enter__()
