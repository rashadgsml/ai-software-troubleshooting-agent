"""MCP server exposing the diagnostic tool registry.

MCP (Model Context Protocol) is a standard way for an AI application to discover and call
tools hosted elsewhere: `tools/list` returns names + descriptions + JSON schemas, and
`tools/call` runs one. Django analogy: OpenAPI + REST for tools, so any MCP client (this
agent, Claude Desktop, an IDE) can use the same tools without custom glue.

We serve the *existing* registry rather than re-declaring tools, so validation,
redaction and truncation behave identically whichever way a tool is reached.
"""

import anyio.to_thread
from mcp import types
from mcp.server.lowlevel import Server

from app.tools.registry import ToolRegistry


def build_server(registry: ToolRegistry) -> Server:
    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=d["name"], description=d["description"], input_schema=d["input_schema"])
                for d in registry.to_anthropic_tools()
            ]
        )

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        # Tool handlers do blocking file/HTTP I/O: keep them off the event loop.
        result = await anyio.to_thread.run_sync(registry.call, params.name, params.arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.content)],
            is_error=result.is_error,
        )

    return Server(
        "troubleshooting-tools",
        version="0.1.0",
        instructions="Read-only diagnostic tools for investigating a web service incident.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
