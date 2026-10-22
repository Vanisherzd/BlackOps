#!/usr/bin/env bash
# Drill against the VM victim (scripts/vm-victim.sh): ops_api in VM mode, inject, the sandbox agent repairs over ssh
# through the vm_* tools, verify. Run it when nothing else uses the victim or ops_api (no regression, watchdog stopped):
#   scripts/vm-drill.sh vm-nginx-stopped | vm-bad-config | vm-unit-masked | vm-config-deleted
# Afterwards `scripts/stage-up.sh` puts ops_api back in container mode.
cd ~/BlackOps 2>/dev/null || cd "$(dirname "$0")/.."; export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"; source .venv/bin/activate
CASE="${1:-vm-nginx-stopped}"
if pgrep -f "regression.sh|nemoclaw-drill.sh" >/dev/null; then echo "a container drill is running; not touching ops_api" >&2; exit 1; fi
[ -f logs/watchdog.pid ] && kill "$(cat logs/watchdog.pid)" 2>/dev/null && echo "watchdog stopped for the drill"
for pid in $(pgrep -f "python scripts/ops_api.py"); do kill "$pid"; done; sleep 1
env VM_VICTIM=1 GUARDRAILS=1 APPROVAL=auto KB_RETRIEVAL=v4 REVIEW_THINK=0 setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null &
for i in $(seq 1 30); do curl -sf -m 2 http://127.0.0.1:8790/tools >/dev/null && break; sleep 1; done
curl -s -m 3 http://127.0.0.1:8790/tools | python3 -c "import sys,json; print('ops_api tools:', ' '.join(t['name'] for t in json.load(sys.stdin)['tools']))"
echo "=== $(date +%T) reset + inject $CASE"; python scripts/vm_victim.py reset | tail -1; python scripts/vm_victim.py inject "$CASE"
curl -s -m 8 -X POST http://127.0.0.1:8790/call -H 'content-type: application/json' -d '{"name":"check_health","args":{}}' >/dev/null 2>&1
PROMPT='Alert: the web service on host victim-vm is degraded. That host is reachable only over ssh through your vm_* tools (vm_logs first, vm_exec for read-only checks, vm_service / vm_write_config / vm_restore_config to repair); its health URLs are http://127.0.0.1:8881/ and http://127.0.0.1:8881/api/. Diagnose and repair it, then verify both health URLs return 200. Finish with exactly three lines and nothing else, each under 40 words: Root cause: ... / Actions: ... / Final health: ...'
: > logs/ops-api.log
T0=$(date +%s)
echo "=== $(date +%T) NemoClaw agent turn"
timeout 900 nemoclaw edge-agent agent --agent main --session-id "vmdrill-$(date +%s)" -m "$PROMPT" 2>&1 | grep -vE "UNDICI|trace-warnings|^\s*$|Active gateway|^\[gateway\]" | cut -c1-500 | tee logs/drill-agent.out
echo "=== $(date +%T) verify ($(( $(date +%s) - T0 )) s)"; python scripts/vm_victim.py verify; VERIFY_RC=$?
if [ "$VERIFY_RC" = 0 ] && [ "${DRILL_MEMORY:-1}" = 1 ]; then python scripts/incident_memory.py record-drill --case "$CASE" --log logs/ops-api.log --seconds $(( $(date +%s) - T0 )) --report logs/drill-agent.out | tail -1; fi
echo "=== ops-api.log (what the sandbox agent called)"
python3 - <<'PY'
import json
for l in open('logs/ops-api.log'):
    d = json.loads(l)
    if d.get('from') == 'tool-layer':
        print('  [tool-layer]', d['ts'], d['tool'], json.dumps(d['args'])[:90], '->', str(d['result'])[:200].replace('\n', ' ')); continue
    print(d['ts'], d['from'], d['tool'], json.dumps(d['args'])[:90], '->', str(d['result'])[:400].replace('\n', ' '))
PY
echo "VM_DRILL_DONE $(date +%T) rc=$VERIFY_RC"
