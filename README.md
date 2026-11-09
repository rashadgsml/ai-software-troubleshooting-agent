# AI-Powered Software Troubleshooting Agent

An autonomous incident-investigation agent. You describe a production problem in plain language ("checkout is returning 500s"); a Claude-powered agent investigates by calling **read-only diagnostic tools** (logs, health checks, deploy history, config, DB stats, runbooks), reasons over the evidence, and returns a **structured root-cause report** with cited evidence and recommended next steps.

**Stack:** Python 3.13 · Anthropic Claude API (tool use) · FastAPI · PostgreSQL · MCP (Model Context Protocol) · pytest · Docker

> **Status: work in progress, built phase by phase.** See the [roadmap](#roadmap) for exactly what is done and what is not. Nothing below is claimed unless it is checked off.

---

## Why this project

Incident triage is mostly repetitive evidence gathering: read the report, form a hypothesis, check logs/config/DB, refine, conclude. An LLM that can *call tools* can do the legwork, and the interesting engineering problem is making that **safe, observable and measurable**, not just making it work once.

This repo focuses on the parts that matter in real agent systems:

| Concern | Approach |
|---|---|
| **Autonomy without risk** | Every tool is read-only and allow-listed. No shell, no free-form SQL, no file paths chosen by the model. |
| **Prompt injection** | Logs and config are untrusted text the model reads. Capability limits (not prompt wording) are the defense; an eval scenario will plant an injection in a log line. |
| **Secrets** | Redaction runs on every tool output *before* it reaches the model or the database. |
| **Reliability** | Argument validation, errors returned to the model as data (`is_error`), output truncation, iteration/time/token guardrails. |
| **Auditability** | Every message, tool call and result is persisted so any investigation can be replayed. |
| **Measurability** | Fault-injection scenarios with known root causes, scored by an eval harness (not just unit tests). |

## Architecture

```
Client ──POST /incidents──▶ FastAPI ──▶ PostgreSQL
Client ──POST /incidents/{id}/investigations──▶ background run
                                   │
                        ┌──────────▼──────────┐
                        │  Agent orchestrator  │  hand-written tool-use loop
                        └───┬───────────────┬──┘
              messages+tools│               │tool_use requests
                      ┌─────▼─────┐   ┌─────▼─────────────┐
                      │ Claude API│   │ MCP client        │
                      └───────────┘   └─────┬─────────────┘
                                            │ MCP
                                      ┌─────▼─────────────┐
                                      │ MCP server        │──▶ logs, health, deploys,
                                      │ (tool registry)   │    config, DB stats, runbooks
                                      └───────────────────┘
Every step ──▶ PostgreSQL (investigation_steps)
Client ──GET /investigations/{id}──▶ status, trace, final report
```

**The agent loop in one paragraph.** Claude never executes anything. It *requests* a tool (`stop_reason: tool_use`); our code validates the arguments, runs the function, and returns a `tool_result`. This repeats until Claude concludes or a guardrail trips. See [`scripts/phase1_tool_calling.py`](scripts/phase1_tool_calling.py) for the protocol shown step by step, including Claude issuing several tool calls in parallel on its own.

### Key design decisions

| Decision | Choice | Why |
|---|---|---|
| Agent loop | Hand-written on the Anthropic SDK | Full control and visibility; frameworks hide exactly what is worth understanding. |
| Target system | Sandboxed fault-injection "world" | Reproducible incidents with known answers, which is what makes evaluation possible. |
| Tool boundary | A registry (name, description, Pydantic model, handler) | One place for validation, redaction, truncation and error handling. MCP will wrap this same registry. |
| Tool arguments | Enums and bounded strings only | The model cannot request arbitrary paths or queries. |
| Persistence | PostgreSQL, step-level trace | Audit log of everything the agent did and saw. |

## Example run (spot check, not an eval)

`python -m app.agent.cli pool_exhausted` against the real API. The agent checked health, error logs, deploys and DB stats, probed a dependency to rule it out, looked up the runbook, then reported:

```
-> check_http_health(demo-app)          503 degraded
-> search_logs(level=ERROR)             QueuePool limit of size 10 reached ...
-> get_recent_deployments()             v2.15.0 "single transaction" refactor
-> query_db_stats()                     10/10 active, 8 idle in transaction, 37 waiting
-> check_http_health(payment-service)   healthy  (rules out upstream)
-> lookup_runbook("connection pool exhausted")
-> submit_report(...)                   ERROR: invalid report, fix and resubmit
-> submit_report(...)                   Report accepted.
status=completed  iterations=4  category=db_connection_pool_exhausted  confidence=medium
```

Two things worth noting. The model's first `submit_report` was malformed; schema validation rejected it, the error went back as a tool result, and the model corrected itself on the next try. And on the `healthy` scenario it reported `category: none` rather than inventing a problem. Two runs prove the loop works; measuring *how often* it is right is the job of the Phase 6 evals.

## Fault scenarios (ground truth for evals)

| Scenario | Planted root cause | Key evidence the agent must find |
|---|---|---|
| `pool_exhausted` | Transactions held open, DB pool exhausted | `QueuePool limit` errors, 8 connections *idle in transaction* |
| `bad_config` | Typo in a payment URL shipped in a deploy | Latest deploy's config diff, `Name or service not known` |
| `disk_full` | Disk at 99.8% | `Errno 28 No space left on device`, resource stats |
| `upstream_timeout` | Slow inventory dependency, no deploy | `ReadTimeout`, 9.4 s dependency latency |
| `healthy` | Nothing wrong (false-alarm check) | Agent should not invent a problem |

## Diagnostic tools

`search_logs` · `check_http_health` · `get_recent_deployments` · `read_config` (secrets redacted) · `check_resources` · `query_db_stats` · `lookup_runbook`

Tool descriptions state *when* to use each tool, because that text is what the model reads when deciding what to call.

## Roadmap

- [x] **Phase 0 – Foundations:** FastAPI skeleton, env-based settings, Dockerfile, docker-compose (API + Postgres 16), pytest
- [x] **Phase 1 – Tool-calling basics:** hand-driven `tool_use` / `tool_result` round trip against the real API
- [x] **Phase 2 – Tools & demo target:** fault injection (5 scenarios), 7 read-only tools, registry with validation / redaction / truncation, 23 unit tests
- [x] **Phase 3 – Agent orchestrator:** the loop, system prompt, guardrails (iterations / tokens / wall-clock), validated structured report via a `submit_report` tool, 13 fake-client tests (36 total)
- [ ] **Phase 4 – MCP:** tools served over MCP, agent consumes them via an MCP client
- [ ] **Phase 5 – Persistence + API:** SQLAlchemy/Alembic models, endpoints, background runs, stored trace
- [ ] **Phase 6 – Evals:** scenario scoring (root-cause correctness, evidence grounding, tool efficiency, injection resistance)
- [ ] **Phase 7 – Hardening:** auth, rate limiting, redaction audit, timeouts, retries
- [ ] **Phase 8 – Polish:** results table, demo transcript

## Testing strategy

1. **Unit tests** (deterministic): every tool against every fault scenario, argument validation, path/enum rejection, secret redaction, truncation.
2. **Orchestrator tests with a fake Claude client** (no network, no cost): scripted responses exercise the loop, guardrails and error handling.
3. **Agent evals** (opt-in, real API): run each scenario several times and score root-cause correctness, evidence grounding, tool efficiency and safety. Unit tests answer "is the code correct?"; evals answer "is the agent good?".

## Running it

```bash
cp .env.example .env            # add your ANTHROPIC_API_KEY
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                 # unit tests (no API calls)
docker compose up --build        # API on :8000, Postgres on :5432
.venv/bin/python -m app.agent.cli disk_full       # run the agent on a scenario (a few cents of API cost)
.venv/bin/python scripts/phase1_tool_calling.py   # the raw tool-calling protocol, step by step
```

## Repository layout

```
app/
  agent/                             # orchestrator loop, prompts, report schema, CLI
  api/  mcp_server/  db/             # FastAPI layer, MCP, persistence (upcoming phases)
  core/                              # settings, secret redaction
  tools/                             # tool registry + read-only diagnostic tools
demo_app/                            # fault-injectable target system + runbooks
scripts/                             # learning/demo scripts
tests/                               # pytest suites
evals/                               # scenario scoring (Phase 6)
```

## Out of scope (deliberately)

Real production integrations, auto-remediation (the agent only *suggests* fixes), multi-tenancy, multi-agent orchestration, fine-tuning.
