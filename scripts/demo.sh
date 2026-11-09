#!/usr/bin/env bash
# End-to-end demo against a running API (docker compose up -d --build).
#   API_KEY=... scripts/demo.sh [scenario]        # default: pool_exhausted (costs a few cents)
set -euo pipefail
BASE="${BASE:-http://localhost:8000}"
SCENARIO="${1:-pool_exhausted}"
: "${API_KEY:?set API_KEY to the value in your .env}"
H=(-H "X-API-Key: $API_KEY" -H "content-type: application/json")
pj() { python3 -c "import sys,json; d=json.load(sys.stdin); $1"; }

echo "# 1. Create an incident (scenario '$SCENARIO' decides which fault is injected)"
INCIDENT=$(curl -s -X POST "$BASE/incidents" "${H[@]}" \
  -d "{\"title\":\"Production incident: $SCENARIO\",\"scenario\":\"$SCENARIO\"}")
echo "$INCIDENT" | pj "print(json.dumps(d, indent=2))"
IID=$(echo "$INCIDENT" | pj "print(d['id'])")

echo; echo "# 2. Start an investigation (returns 202 immediately; the agent runs in the background)"
INV=$(curl -s -X POST "$BASE/incidents/$IID/investigations" "${H[@]}")
echo "$INV" | pj "print({k: d[k] for k in ('id','status','model')})"
VID=$(echo "$INV" | pj "print(d['id'])")

echo; echo "# 3. Poll until it finishes"
while :; do
  STATUS=$(curl -s "$BASE/investigations/$VID" "${H[@]}" | pj "print(d['status'])")
  echo "  status: $STATUS"
  case "$STATUS" in pending|running) sleep 3 ;; *) break ;; esac
done

echo; echo "# 4. The structured report"
curl -s "$BASE/investigations/$VID" "${H[@]}" | pj "
r = d['final_report'] or {}
print('status     :', d['status'], '| iterations:', d['iterations'], '| tokens in/out:', d['input_tokens'], '/', d['output_tokens'])
print('category   :', r.get('category'), '| confidence:', r.get('confidence'))
print('summary    :', r.get('summary'))
print('root cause :', r.get('root_cause'))
print('evidence   :')
for e in r.get('evidence', []): print('   -', e['tool'] + ':', e['finding'])
print('next steps :')
for s in r.get('recommended_steps', []): print('   -', s)"

echo; echo "# 5. The audit trail: every tool call the agent made, with timings"
curl -s "$BASE/investigations/$VID/steps" "${H[@]}" | pj "
for s in d:
    if s['kind'] in ('tool_call', 'assistant_text', 'report'):
        arg = json.dumps(s['payload'])[:90] if s['kind'] == 'tool_call' else ''
        print(f\"  {s['seq']:>2} {s['kind']:<14} {s['tool_name'] or '':<24} {arg}\")
    elif s['kind'] == 'tool_result':
        print(f\"  {s['seq']:>2} {'  -> result':<14} {s['tool_name']:<24} {s['duration_ms']} ms{'  ERROR' if s['is_error'] else ''}\")"
