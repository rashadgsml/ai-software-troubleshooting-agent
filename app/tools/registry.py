"""Tool registry: the single gateway between the agent and diagnostic functions.

Django analogy: a URL router + serializer layer. It
  * advertises tools to Claude (name, description, JSON schema),
  * validates the model's arguments (Pydantic),
  * runs the handler, converting exceptions into error results,
  * redacts secrets and truncates oversized output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from app.core.redaction import redact
from app.tools.jsonschema import inline_refs

MAX_OUTPUT_CHARS = 8000


@dataclass
class ToolContext:
    """Everything a tool may touch. Passed in, never global (easy to fake in tests)."""

    data_dir: Path
    http: httpx.Client
    runbooks_dir: Path


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Any]


class ToolProvider(Protocol):
    """What the agent needs from a tool source: a local ToolRegistry or an MCP client."""

    def to_anthropic_tools(self) -> list[dict]: ...

    def call(self, name: str, arguments: dict) -> ToolResult: ...


class ToolRegistry:
    def __init__(self, context: ToolContext):
        self.context = context
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool '{tool.name}'")
        self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def to_anthropic_tools(self) -> list[dict]:
        """Tool definitions in the shape the Anthropic Messages API expects."""
        defs = []
        for t in self._tools.values():
            schema = inline_refs(t.input_model.model_json_schema())
            schema.pop("title", None)
            defs.append({"name": t.name, "description": t.description, "input_schema": schema})
        return defs

    def call(self, name: str, arguments: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"Unknown tool '{name}'. Available: {', '.join(self.names)}", True)
        try:
            args = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(f"Invalid arguments for {name}: {exc.errors(include_url=False)}", True)
        try:
            output = tool.handler(args, self.context)
        except Exception as exc:  # tool bugs/env problems become data for the model
            return ToolResult(f"{name} failed: {type(exc).__name__}: {exc}", True)
        text = output if isinstance(output, str) else json.dumps(output, indent=2)
        text = redact(text)
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + f"\n...[truncated, {len(text) - MAX_OUTPUT_CHARS} chars omitted]"
        return ToolResult(text)
