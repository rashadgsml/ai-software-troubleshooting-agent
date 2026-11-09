# Demo transcript

A real run captured with [`scripts/demo.sh`](../scripts/demo.sh) against the Dockerized API on
`claude-sonnet-5-5`: scenario `pool_exhausted`, the database connection pool is exhausted by
transactions left open. Cost: about 3 cents. Nothing below is edited.

```
# 1. Create an incident (scenario 'pool_exhausted' decides which fault is injected)
{
  "id": 4,
  "title": "Production incident: pool_exhausted",
  "description": "Checkout requests are timing out and users see 500 errors since about 30 minutes ago.",
  "scenario": "pool_exhausted",
  "created_at": "2026-11-09T15:52:23.755632Z"
}

# 2. Start an investigation (returns 202 immediately; the agent runs in the background)
{'id': 3, 'status': 'pending', 'model': 'claude-sonnet-5-5'}

# 3. Poll until it finishes
  status: running
  status: running
  status: running
  status: running
  status: completed

# 4. The structured report
status     : completed | iterations: 3 | tokens in/out: 9490 / 1158
category   : db_connection_pool_exhausted | confidence: medium
summary    : Checkout 500s are caused by DB connection pool exhaustion: 8 of 10 connections are stuck idle in transaction (up to 21 minutes) and 37 requests are waiting. The likely source is the v2.15.0 single-transaction order-creation refactor, which probably holds transactions open across slow calls. That link is inferred, not confirmed.
root cause : Transactions held open (idle in transaction) exhaust the size-10 pool. This is likely a code path from v2.15.0 "Refactor order creation to use a single transaction" that keeps a transaction open during slow external calls or fails to close it.
evidence   :
   - query_db_stats: pool_size 10, active 10, idle 0, idle_in_transaction 8, waiting_requests 37, longest transaction 1260s
   - search_logs: QueuePool limit of size 10 overflow 0 reached, connection timed out; POST /checkout 500 TimeoutError: could not acquire DB connection
   - check_http_health: demo-app returns 503 degraded with 30000ms latency, which matches the 30s pool timeout
   - get_recent_deployments: v2.15.0 (2026-11-08) refactored order creation to a single transaction, with no config changes. This is the likely trigger. The config was ruled out as a cause.
   - read_config: db_pool_size 10 and timeout 30s are consistent with the logs. There is no bad URL or config error.
next steps :
   - As short-term relief, terminate the long-idle-in-transaction sessions (oldest is about 1260s) in the database.
   - Review the v2.15.0 order-creation code for transactions left open across slow external calls (payment or inventory), or not committed or rolled back on error paths.
   - Close or commit the transaction before external calls. Consider setting idle_in_transaction_session_timeout on the database.
   - Consider rolling back to v2.14.0 if the fix isn't quick.
   - Raise the pool size only after the leak is fixed.
   - Add an alert on idle_in_transaction count and waiting_requests.

# 5. The audit trail: every tool call the agent made, with timings
   0 tool_call      check_http_health        {"target": "demo-app"}
   1   -> result    check_http_health        9 ms
   2 tool_call      search_logs              {"level": "ERROR"}
   3   -> result    search_logs              1 ms
   4 tool_call      query_db_stats           {}
   5   -> result    query_db_stats           1 ms
   6 assistant_text                          
   7 tool_call      get_recent_deployments   {}
   8   -> result    get_recent_deployments   3 ms
   9 tool_call      read_config              {}
  10   -> result    read_config              2 ms
  11 tool_call      lookup_runbook           {"query": "connection pool idle in transaction"}
  12   -> result    lookup_runbook           4 ms
  13 tool_call      submit_report            {"summary": "Checkout 500s are caused by DB connection pool exhaustion: 8 of 10 connection
  14   -> result    submit_report            0 ms
  15 report                                  
```

Reading it:

- Steps 0 to 12: the agent checks app health, error logs and DB pool stats, notices a recent
  deploy, reads the config to rule out a bad setting, then looks up the runbook. Each call is
  validated, redacted and timed before the result goes back to the model.
- Step 13: `submit_report`, the structured conclusion. It is schema-validated; on a malformed
  report the error goes back to the model and it retries (this run was accepted first time).
- The report separates what was observed (evidence, each item traceable to a tool call above)
  from what was inferred ("likely", "inferred, not confirmed"), and confidence is `medium`
  rather than `high` because the agent never saw the code.
