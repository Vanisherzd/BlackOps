#!/usr/bin/env bash
# One command to bring the Thor stage up from a cold or half-dead state (2026-10-22 final recipe):
#   scripts/thor-start.sh            start everything, print the dashboard URL
#   scripts/thor-start.sh --demo     same, then run the demo loop (bad-deploy cascade / backend 500 / log injection)
#   scripts/thor-start.sh --check    only report what is up or down; change nothing
# Order: repo -> stale containers -> rerank sidecar -> stage-up (vLLM, shim, guard, ops_api, watchdog, dashboard, power)
#        -> NemoClaw sandbox -> preflight. Every step is idempotent; a healthy piece is left alone.
set -u
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
export RERANK_URL="${RERANK_URL:-http://127.0.0.1:8020/rerank}" REVIEW_THINK="${REVIEW_THINK:-0}" GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.25}"
MODE="${1:-start}"
log() { echo "=== $(date +%H:%M:%S) $*"; }
up() { curl -sf -m 3 "$1" >/dev/null 2>&1; }

log "repo: $(git log --oneline -1 | cut -c1-60) ($(git status --short | wc -l | tr -d ' ') local changes); fully offline: nothing here needs the network"
if [ "$MODE" = "--check" ]; then
  for u in "vLLM http://127.0.0.1:8000/health" "shim http://127.0.0.1:8001/health" "guard http://127.0.0.1:8792/health" "ops_api http://127.0.0.1:8790/tools" "dashboard http://127.0.0.1:8097/" "rerank http://127.0.0.1:8020/health"; do
    set -- $u; printf "%-10s %s\n" "$1" "$(up "$2" && echo up || echo DOWN)"
  done
  pgrep -f "scripts/watchdog.py" >/dev/null && echo "watchdog   up" || echo "watchdog   DOWN"
  printf "victim     / %s /api/ %s\n" "$(curl -s -m 4 -o /dev/null -w %{http_code} http://127.0.0.1:8880/)" "$(curl -s -m 4 -o /dev/null -w %{http_code} http://127.0.0.1:8880/api/)"
  S=$(timeout 60 nemoclaw edge-agent status 2>/dev/null | grep -i -E "Inference:|Failure layer" | head -2)
  echo "sandbox    ${S:-(status unavailable: an agent turn holds the nemoclaw lock, or the CLI is slow; try again when idle)}"
  exit 0
fi

# OpenShell refuses a group-writable ~/.local; a stale exited vllm-edge blocks the name
chmod g-w,o-w ~/.local ~/.local/share ~/.local/bin 2>/dev/null
for c in vllm-edge vllm-rerank; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = "false" ]; then docker rm "$c" >/dev/null && log "removed stale exited container $c"; fi
done

log "rerank sidecar"; GPU_MEMORY_UTILIZATION=0.03 READY_TIMEOUT=300 scripts/serve-rerank.sh 2>&1 | tail -1 || true
up http://127.0.0.1:8020/health || { log "rerank sidecar not up: continuing without it (BM25 + judge)"; unset RERANK_URL; }

log "stage-up (vLLM, shim, guard, ops_api, watchdog, dashboard, power)"
timeout 1200 scripts/stage-up.sh 2>&1 | grep -E "ready|up |PASS|FAIL|WARN|PREFLIGHT" | tail -16

log "NemoClaw sandbox"
S=$(timeout 60 nemoclaw edge-agent status 2>/dev/null)
if echo "$S" | grep -qi "Inference: healthy"; then echo "  sandbox healthy"
else
  timeout 240 nemoclaw edge-agent start </dev/null 2>&1 | grep -v -E "ExperimentalWarning|trace-warnings|^\s*$" | tail -2
  sleep 5; S=$(timeout 60 nemoclaw edge-agent status 2>/dev/null); echo "$S" | grep -i -E "Inference:|Failure layer" | head -2
  if echo "$S" | grep -qi "Error state\|Failure layer" || timeout 30 openshell sandbox list 2>/dev/null | grep -q "edge-agent.*Error"; then
    # a hard power-off leaves the sandbox in OpenShell phase Error (exit 141 on 2026-10-22): start/stop are refused and
    # only a rebuild helps. Everything it needs is local (sandbox image, model endpoint, policies, skill files).
    log "sandbox in Error: rebuilding (about 3 minutes, all local)"
    timeout 120 openshell sandbox delete edge-agent --yes 2>&1 | tail -1
    python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.nemoclaw/sandboxes.json")
try:
    d = json.load(open(p)); d.get("sandboxes", {}).pop("edge-agent", None); d["defaultSandbox"] = None; json.dump(d, open(p, "w"), indent=2)
except Exception as e:
    print("registry:", e)
PY
    rm -rf ~/.nemoclaw/state/*edge-agent* 2>/dev/null
    NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=http://127.0.0.1:8001/v1 NEMOCLAW_MODEL=edge-agent NEMOCLAW_PROVIDER_KEY=none \
      NEMOCLAW_COMPATIBLE_AUTH_MODE=none NEMOCLAW_REASONING=true NEMOCLAW_SANDBOX_NAME=edge-agent NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
      NEMOCLAW_SANDBOX_GPU=0 COMPATIBLE_API_KEY=none timeout 600 nemoclaw onboard --non-interactive --fresh --yes-i-accept-third-party-software \
      </dev/null 2>&1 | grep -v -E "ExperimentalWarning|trace-warnings|^\s*$" | tail -3
    NEMOCLAW_SANDBOX_GPU=0 COMPATIBLE_API_KEY=none timeout 900 scripts/bootstrap-spark.sh nemoclaw </dev/null 2>&1 | grep -E "Inference|Policies|Policy version|Applied" | tail -4
    timeout 60 nemoclaw edge-agent status 2>/dev/null | grep -i -E "Inference:|Failure layer" | head -2
  fi
fi

log "preflight"; timeout 120 scripts/preflight.sh 2>&1 | grep -E "FAIL|PREFLIGHT" | tail -4
IP=$(ip -4 addr show 2>/dev/null | grep -oE "inet (172\.20\.|192\.168\.)[0-9.]+" | awk '{print $2}' | head -2 | tr '\n' ' ')
log "dashboard: http://127.0.0.1:8097/   from another machine: ${IP:+http://}${IP% *}${IP:+:8097/}"

if [ "$MODE" = "--demo" ]; then
  log "demo loop: compound-bad-deploy-cascade / upstream-500 / log-injection (stop with: touch logs/demo-loop.stop)"
  CASES="${CASES:-compound-bad-deploy-cascade upstream-500 log-injection}" CYCLES="${CYCLES:-8}" GAP="${GAP:-40}" \
    setsid nohup bash scripts/demo-loop.sh > logs/demo-loop.out 2>&1 < /dev/null &
fi
log THOR_START_DONE
