"""Phase 1 (throwaway): one manual tool-calling round trip, printed step by step.

Run:  .venv/bin/python scripts/phase1_tool_calling.py

Goal: SEE the raw protocol before we hide it inside an agent loop.
  1. We send messages + tool definitions.
  2. Claude replies with stop_reason="tool_use" and a tool_use block (a *request*).
  3. WE run the function.
  4. We send back a tool_result tied to the tool_use id.
  5. Claude writes the final answer (stop_reason="end_turn").
"""

import json

import anthropic

from app.core.config import get_settings

settings = get_settings()
client = anthropic.Anthropic(api_key=settings.anthropic_api_key)


# --- A fake diagnostic tool (real ones come in Phase 2) -------------------------
def check_service_status(service: str) -> dict:
    fake_world = {
        "checkout-api": {"status": "degraded", "error_rate": 0.42, "p95_ms": 5200},
        "auth-api": {"status": "healthy", "error_rate": 0.001, "p95_ms": 90},
    }
    return fake_world.get(service, {"error": f"unknown service '{service}'"})


# --- Tool definition: this JSON schema is all Claude ever sees of the function --
TOOLS = [
    {
        "name": "check_service_status",
        "description": (
            "Get current health of a service: status, error rate and p95 latency. "
            "Use this first when an incident mentions a specific service."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name, e.g. 'checkout-api'"}
            },
            "required": ["service"],
        },
    }
]

SYSTEM = "You are a troubleshooting assistant. Use tools to gather evidence before concluding."

messages = [{"role": "user", "content": "Customers say checkout is failing. What's going on?"}]


def show(title: str, response: anthropic.types.Message) -> None:
    print(f"\n=== {title} ===")
    print(f"stop_reason: {response.stop_reason}")
    print(f"tokens in/out: {response.usage.input_tokens}/{response.usage.output_tokens}")
    for block in response.content:
        if block.type == "text":
            print(f"[text] {block.text}")
        elif block.type == "tool_use":
            print(f"[tool_use] id={block.id} name={block.name} input={json.dumps(block.input)}")
        else:
            print(f"[{block.type}]")


# --- Turn 1: Claude decides whether to call a tool -------------------------------
response = client.messages.create(
    model=settings.anthropic_model,
    max_tokens=4096,
    system=SYSTEM,
    tools=TOOLS,
    messages=messages,
)
show("Response 1", response)

if response.stop_reason != "tool_use":
    raise SystemExit("Claude answered without a tool call; nothing more to demo.")

# Append the assistant turn UNCHANGED (full content list, not just the text).
messages.append({"role": "assistant", "content": response.content})

# --- We execute every requested tool and reply with tool_result blocks -----------
results = []
for block in response.content:
    if block.type == "tool_use":
        output = check_service_status(**block.input)  # <- our code runs it, not Claude
        print(f"\n>>> We ran {block.name}({block.input}) -> {output}")
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,  # must match the request
                "content": json.dumps(output),
            }
        )
messages.append({"role": "user", "content": results})

# --- Turn 2: Claude reads the evidence and continues -----------------------------
response = client.messages.create(
    model=settings.anthropic_model,
    max_tokens=4096,
    system=SYSTEM,
    tools=TOOLS,
    messages=messages,
)
show("Response 2", response)
