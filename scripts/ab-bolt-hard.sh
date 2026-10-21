#!/usr/bin/env bash
# Bolt on the eight hard cases (blind batches 3 and 4), 3 rounds, the configuration of the Nano baseline's arm A:
# thinking on, the shim's measured-health verifier, no SHIM_ADAPTIVE, no TRIAGE, KB v3; Guardrails, approval and Tier
# off. Compare with logs/ab-20261020-155259-A.log (Nano/TensorRT-LLM, same 8 cases x 3: 12/24).
#   scripts/ab-bolt-hard.sh --check     # preflight only: what would run, and whether it may
#   setsid nohup bash scripts/ab-bolt-hard.sh > /dev/null 2>&1 < /dev/null &
# Writes logs/bolt-hard-<time>.log (driver) and logs/bolt-hard-<time>-B.log (drills; score.py reads it).
# Differences from the Nano run besides the model and engine: the tree is integrate-vllm, whose drill path adds
# opt-in approval/Tier hooks to agent.py (off here), one line in runbooks/upstream-502.md, one line in
# nemoclaw/workspace/AGENTS.md (only if uploaded to the sandbox) and the board mount on edge-victim.
cd ~/edge-agent || exit 1
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
unset GUARDRAILS APPROVAL TIER_MODE TIER_NVPMODEL SHIM_ADAPTIVE TRIAGE KB_RETRIEVAL
CASES="upstream-perm-denied upstream-sigstop upstream-429 front-mime-missing upstream-config-crash upstream-pids-limit
       front-worker-connections upstream-redirect"
ROUNDS=3

preflight() {
  local bad=0
  docker ps --format '{{.Names}}' | grep -qx vllm-edge || { echo "NO: vllm-edge (Bolt) is not running"; bad=1; }
  docker ps --format '{{.Names}}' | grep -qx trtllm-edge && { echo "NO: trtllm-edge is running too"; bad=1; }
  models=$(curl -s -m 5 localhost:8000/v1/models | python3 -c 'import json,sys; print(",".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null)
  [ "$models" = "edge-agent" ] || { echo "NO: :8000 serves [$models], want edge-agent"; bad=1; }
  image=$(docker inspect -f '{{.Config.Image}}' vllm-edge 2>/dev/null)
  case "$image" in blackbox-vllm-bolt:*) ;; *) echo "NO: vllm-edge image is [$image], want blackbox-vllm-bolt:*"; bad=1;; esac
  # another drill or batch on the shared victim ([n] keeps pgrep from matching its own command line)
  pgrep -f "[n]emoclaw-drill[.]sh|[f]aults[.]py run|[v]alidate-vllm|[o]vernight[.]sh|[d]rill-all[.]sh|[a]b-speed[.]sh" >/dev/null \
    && { echo "NO: another drill or batch is running"; bad=1; }
  src=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/nginx/conf.d"}}{{.Source}}{{end}}{{end}}' edge-victim 2>/dev/null)
  [ "$src" = "$HOME/edge-agent/victim/conf.d" ] || { echo "NO: edge-victim mounts conf.d from [$src]; run .venv/bin/python scripts/faults.py setup here first"; bad=1; }
  free_gb=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  [ "$free_gb" -ge 20 ] || { echo "NO: only ${free_gb} GB available"; bad=1; }
  echo "tree $(git rev-parse --abbrev-ref HEAD)@$(git rev-parse --short HEAD), $(git status --short | grep -vc '^??') tracked changes; model edge-agent on $image; ${free_gb} GB free"
  echo "plan: $(echo $CASES | wc -w) cases x $ROUNDS rounds, thinking on + verifier, no adaptive, no triage"
  return $bad
}

quiesce() { local i; for i in $(seq 1 60); do age=$(( $(date +%s) - $(stat -c %Y logs/ops-api.log 2>/dev/null || echo 0) )); [ $age -ge 60 ] && return; sleep 10; done; }
restart_ops() { [ -f logs/ops-api.pid ] && kill "$(cat logs/ops-api.pid)" 2>/dev/null; pkill -f "scripts/[o]ps_api[.]py"; sleep 2
  setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null & echo $! > logs/ops-api.pid; sleep 3; }

if [ "${1:-}" = "--check" ]; then preflight && echo "OK to run"; exit $?; fi
preflight || { echo "preflight failed; nothing started"; exit 1; }

BASE="logs/bolt-hard-$(date +%Y%m%d-%H%M%S)"
{
echo "bolt-hard B=[Bolt, thinking on + verifier] $(preflight | head -1)"
for round in $(seq 1 $ROUNDS); do
  for c in $CASES; do
    quiesce; restart_ops; bash scripts/shim-env.sh "SHIM_THINK=1" > /dev/null; echo "round $round $c $(date +%T)"
    TRIAGE=0 timeout 600 bash scripts/nemoclaw-drill.sh $c >> $BASE-B.log 2>&1
    if [ $? -eq 124 ]; then echo "timeout: gateway restart"; nemoclaw edge-agent gateway restart </dev/null >/dev/null 2>&1; fi
    echo "########" >> $BASE-B.log
  done
done
bash scripts/shim-env.sh "SHIM_THINK=1"; restart_ops
echo ALL_DONE
} > $BASE.log 2>&1
