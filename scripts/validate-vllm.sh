#!/usr/bin/env bash
# One-shot validation of the integrated stack on vLLM: Bolt behind the agent, the same checks the TensorRT route
# passed. Run from the integrate-vllm checkout; the NemoClaw drills run where nemoclaw-drill.sh always runs
# (~/edge-agent, EDGE_HOME), against whatever ops_api and shim are up there, now pointed at vLLM on :8000.
#   scripts/validate-vllm.sh [runs-per-case]          default 1; logs/validate-vllm-<time>/ holds every step's output
# Refuses to start while another inference server or any drill is running: the box has no swap, and a second
# 0.45-of-memory server next to a running one froze it once (2026-10-17). Leaves Bolt serving on :8000 when done.
# Steps: serve-vllm.sh -> smoke.py -> nine seen cases on the host loop (this checkout, board mount) -> victim rebuilt
# from EDGE_HOME -> the five stage cases as NemoClaw drills -> scorecard -> blackbox tool_eval.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
HERE=$PWD; EDGE_HOME="${EDGE_HOME:-$HOME/edge-agent}"; PY="${PY:-$EDGE_HOME/.venv/bin/python}"; N="${1:-1}"
OUT="$HERE/logs/validate-vllm-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$OUT"
log() { echo "=== $(date +%T) $*" | tee -a "$OUT/summary.txt"; }

for c in trtllm-edge vllm-bolt vllm-tier1; do
  docker ps --format '{{.Names}}' | grep -qx "$c" && { echo "$c is running: stop it first (coordinate with its owner)" >&2; exit 1; }
done
pgrep -f "nemoclaw-drill[.]sh|faults[.]py run" >/dev/null && { echo "a drill is running; not touching the victim" >&2; exit 1; }

log "serve-vllm.sh"; scripts/serve-vllm.sh > "$OUT/serve.txt" 2>&1 || { log "FAIL: Bolt did not come up (see serve.txt)"; exit 1; }
log "smoke"; VLLM_URL=http://127.0.0.1:8000 "$PY" scripts/smoke.py > "$OUT/smoke.txt" 2>&1 && log "smoke OK" || log "smoke FAIL"

log "host loop, nine seen cases x $N (this checkout)"
"$PY" scripts/faults.py setup >> "$OUT/host-loop.txt" 2>&1
for r in $(seq 1 "$N"); do
  for c in nginx-stopped bad-config upstream-down bad-upstream-name container-removed crashloop upstream-paused upstream-detached upstream-500; do
    echo "--- round $r $c" >> "$OUT/host-loop.txt"
    timeout 900 "$PY" scripts/faults.py run "$c" >> "$OUT/host-loop.txt" 2>&1
  done
done
grep "^RESULT" "$OUT/host-loop.txt" | tee -a "$OUT/summary.txt"

log "victim rebuilt from $EDGE_HOME"; (cd "$EDGE_HOME" && "$PY" scripts/faults.py setup) >> "$OUT/summary.txt" 2>&1

log "NemoClaw drills, five stage cases x $N"
CASES=""; for r in $(seq 1 "$N"); do CASES="$CASES nginx-stopped container-removed bad-config upstream-paused upstream-500"; done
DLOG=$(cd "$EDGE_HOME" && bash scripts/drill-all.sh $CASES)
echo "drill log: $EDGE_HOME/$DLOG" | tee -a "$OUT/summary.txt"
for _ in $(seq 1 540); do grep -q ALL_DONE "$EDGE_HOME/$DLOG" 2>/dev/null && break; sleep 20; done   # at most 3 h
cp "$EDGE_HOME/$DLOG" "$OUT/"
(cd "$EDGE_HOME" && "$PY" scripts/score.py "$DLOG") > "$OUT/scorecard.md" 2>&1
grep "^headline" "$OUT/scorecard.md" | tee -a "$OUT/summary.txt"

log "tool_eval against :8000"
"$PY" -m blackbox.benchmarks.tool_eval --base-url http://127.0.0.1:8000 --model edge-agent \
  --out-json "$OUT/tool_eval.json" --out-md "$OUT/tool_eval.md" > "$OUT/tool_eval.txt" 2>&1 && log "tool_eval done" || log "tool_eval FAIL"
log "ALL_DONE ($OUT)"
