#!/usr/bin/env bash
# drill: inject a fault on the victim containers, ask the NemoClaw sandbox agent to fix it through the ops API, verify.
cd ~/edge-agent; export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"; source .venv/bin/activate
CASE="${1:-bad-config}"
echo "=== $(date +%T) reset + inject $CASE"; python scripts/faults.py reset | tail -1; python scripts/faults.py inject "$CASE"
# No hand-holding: the agent has every ops tool as a first-class tool, so the prompt is what an on-call human would get.
PROMPT='Alert: the web stack is degraded. Diagnose it and repair it with your ops tools, then verify both health URLs return 200. Finish with exactly three lines and nothing else, each under 40 words: Root cause: ... / Actions: ... / Final health: ...'
# TRIAGE=1: the system takes the first look (scripts/triage.py) and the alert carries it; those calls are made before
# the log is cleared, so the scorecard counts only what the agent itself called.
# TRIAGE defaults on since 2026-10-22: the stage's watchdog alert carries the triage (WATCH_TRIAGE=1 in stage-up), and a
# drill should measure the same path; measured 1.2 s of triage against 2-3 fewer model turns (5-7 s). TRIAGE=0 restores the bare alert.
if [ "${TRIAGE:-1}" = "1" ]; then
  TRI=$(python scripts/triage.py)
  echo "=== $(date +%T) triage ($(printf %s "$TRI" | wc -c) chars)"
  PROMPT="$PROMPT

$TRI"
fi
: > logs/ops-api.log
echo "=== $(date +%T) NemoClaw agent turn"
timeout 900 nemoclaw edge-agent agent --agent main --session-id "drill-$(date +%s)" -m "$PROMPT" 2>&1 | grep -vE "UNDICI|trace-warnings|^\s*$|Active gateway|^\[gateway\]" | cut -c1-500
echo "=== $(date +%T) verify"; python scripts/faults.py verify
echo "=== ops-api.log (what the sandbox agent called)"
python3 - <<'PY'
import json
for l in open('logs/ops-api.log'):
    d = json.loads(l)
    print(d['ts'], d['from'], d['tool'], json.dumps(d['args'])[:90], '->', str(d['result'])[:400].replace('\n', ' '))
PY
echo "DRILL_DONE $(date +%T)"
