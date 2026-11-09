"""Adapter: lets the (synchronous) agent loop use tools served over MCP.

The MCP client is async. Rather than rewrite the orchestrator, we run the client inside
ONE long-lived task on a private event-loop thread and expose a blocking interface with
the same shape as ToolRegistry (`to_anthropic_tools()` / `call()`).

Why one long-lived task: the client's transports use anyio task groups, which must be
entered and exited from the same task. Entering in one `run_coroutine_threadsafe` call
and exiting in another would raise "exit cancel scope in a different task".
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from mcp import Client

from app.tools.registry import ToolResult


class McpToolProvider:
    def __init__(self, target: Any, call_timeout: float = 30.0):
        """`target`: StdioServerParameters (subprocess), a URL string, or an in-process Server."""
        self._target = target
        self._timeout = call_timeout
        self._defs: list[dict] = []
        self._client: Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._stop: asyncio.Event | None = None
        self._main_future = None

    # ---- lifecycle ---------------------------------------------------------------
    def __enter__(self) -> McpToolProvider:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-client", daemon=True)
        self._thread.start()
        self._main_future = asyncio.run_coroutine_threadsafe(self._main(), self._loop)
        if not self._ready.wait(timeout=30) or self._startup_error:
            self.__exit__(None, None, None)
            raise RuntimeError(f"could not start MCP client: {self._startup_error!r}")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._main_future:
            try:
                self._main_future.result(timeout=10)
            except Exception:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)

    async def _main(self) -> None:
        self._stop = asyncio.Event()
        try:
            async with Client(self._target, read_timeout_seconds=self._timeout) as client:
                self._client = client
                listing = await client.list_tools()
                self._defs = [
                    {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                    for t in listing.tools
                ]
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:  # surface startup failures to __enter__
            self._startup_error = exc
            self._ready.set()

    # ---- ToolProvider interface (same shape as ToolRegistry) -------------------------
    def to_anthropic_tools(self) -> list[dict]:
        return list(self._defs)

    def call(self, name: str, arguments: dict) -> ToolResult:
        assert self._client and self._loop, "use McpToolProvider as a context manager"
        future = asyncio.run_coroutine_threadsafe(self._client.call_tool(name, arguments), self._loop)
        try:
            result = future.result(timeout=self._timeout + 5)
        except Exception as exc:  # transport failure / timeout becomes data for the model
            return ToolResult(f"MCP call to {name} failed: {type(exc).__name__}: {exc}", True)
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        return ToolResult(text, bool(result.is_error))
