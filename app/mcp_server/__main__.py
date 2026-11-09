"""Run the MCP server over stdio:  DEMO_DATA_DIR=<world> python -m app.mcp_server

stdout is the protocol channel, so never print() here; log to stderr only.
"""

import os
import sys
from pathlib import Path

import anyio
from mcp.server.stdio import stdio_server

from app.mcp_server.server import build_server
from app.tools.diagnostics import build_registry
from app.tools.factory import make_context


async def _serve() -> None:
    data_dir = os.environ.get("DEMO_DATA_DIR")
    if not data_dir:
        sys.exit("DEMO_DATA_DIR is required (directory created by demo_app.faults.build_world)")
    registry = build_registry(make_context(Path(data_dir), os.environ.get("DEMO_APP_URL")))
    server = build_server(registry)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(_serve)
