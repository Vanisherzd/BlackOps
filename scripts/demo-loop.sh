#!/usr/bin/env bash
# Stage demo loop: inject the listed cases through the dashboard API so the patrol detects, dispatches and closes
# them like real incidents; wait for each to close, pause, next. Stop with `touch logs/demo-loop.stop`.
#   CASES="compound-bad-deploy-cascade upstream-500 log-injection" CYCLES=8 GAP=40 scripts/demo-loop.sh
cd "$(dirname "$0")/.." || exit 1
CASES="${CASES:-compound-bad-deploy-cascade upstream-500 log-injection}"; CYCLES="${CYCLES:-6}"; GAP="${GAP:-40}"
log() { echo "=== $(date +%H:%M:%S) $*"; }
state() { curl -s -m 3 http://127.0.0.1:8097/api/state; }
healthy() { state | python3 -c "import sys,json; s=json.load(sys.stdin); sys.exit(0 if s.get(\"healthy\") and not s.get(\"incident\") else 1)" 2>/dev/null; }
inject() { curl -s -m 120 -X POST http://127.0.0.1:8097/api/action -H "content-type: application/json" -d "{\"action\":\"inject\",\"case\":\"$1\"}" | cut -c1-120; echo; }
rm -f logs/demo-loop.stop
for i in $(seq 1 "$CYCLES"); do
  for c in $CASES; do
    [ -f logs/demo-loop.stop ] && { log "stop file seen"; exit 0; }
    log "cycle $i: inject $c"; inject "$c"
    t0=$(date +%s); sleep 12
    until healthy; do sleep 3; [ $(( $(date +%s) - t0 )) -gt 600 ] && { log "not resolved in 600 s; reset"; .venv/bin/python scripts/faults.py reset | tail -1; break; }; done
    log "cycle $i: $c closed after $(( $(date +%s) - t0 )) s; pause $GAP s"; sleep "$GAP"
  done
done
log DEMO_LOOP_DONE
