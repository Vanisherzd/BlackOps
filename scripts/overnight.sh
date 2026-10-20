#!/usr/bin/env bash
# Overnight A/B for the data slide: the same cases, alternating A = Tier-2 throughout (TIER_MODE=always2, the
# baseline) and B = adaptive Tier routing (TIER_MODE=adaptive), human approval auto-granted and recorded as such.
# Every run is archived by scripts/run-archive.sh with RUN_LABEL=A|B; scripts/score-runs.py makes the table.
#   scripts/overnight.sh [rounds] [cases...]        default 3 rounds of the five stage cases
#   env: UPSTREAM_T1 (required for arm B to mean anything), UPSTREAM_T2, NVP_T1/NVP_T2 + TIER_NVPMODEL=1 on the Thor
# Runs detached; logs/overnight-<time>.log ends with ALL_DONE. Uses the shared victim: nothing else may drill meanwhile.
# The ops API and the shim are restarted per arm by pid file (as ab-drill.sh and logs/shim-env.sh do), because the
# tier and approval switches live in those processes' environments. power.py is started if it is not running.
cd "$(dirname "$0")/.." || exit 1
ROUNDS="${1:-3}"; shift
CASES="${*:-nginx-stopped container-removed bad-config upstream-paused upstream-500}"
[ -z "$UPSTREAM_T1" ] && echo "warning: UPSTREAM_T1 unset, arm B routes every turn to Tier-2 as well" >&2
LOG="logs/overnight-$(date +%Y%m%d-%H%M%S).log"
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
setsid nohup env ROUNDS="$ROUNDS" CASES="$CASES" bash -c '
  quiesce() { local i; for i in $(seq 1 60); do
      age=$(( $(date +%s) - $(stat -c %Y logs/ops-api.log 2>/dev/null || echo 0) )); [ $age -ge 60 ] && return; sleep 10; done; }
  restart() {   # $1: env for this arm
    [ -f logs/ops-api.pid ] && kill "$(cat logs/ops-api.pid)" 2>/dev/null
    [ -f logs/oai-shim.pid ] && kill "$(cat logs/oai-shim.pid)" 2>/dev/null
    pkill -f "scripts/oai_shim[.]py" 2>/dev/null; sleep 2
    env $1 setsid nohup .venv/bin/python scripts/oai_shim.py > logs/oai-shim.out 2>&1 < /dev/null & echo $! > logs/oai-shim.pid
    env $1 setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null & echo $! > logs/ops-api.pid
    sleep 4
  }
  pgrep -f "scripts/power[.]py log" >/dev/null || { setsid nohup .venv/bin/python scripts/power.py log > /dev/null 2>&1 < /dev/null & }
  rm -f logs/tier-state.json
  echo "overnight rounds=$ROUNDS cases=[$CASES] A=[TIER_MODE=always2] B=[TIER_MODE=adaptive] APPROVAL=auto"
  for r in $(seq 1 "$ROUNDS"); do
    for c in $CASES; do
      for arm in A B; do
        [ $arm = A ] && T=always2 || T=adaptive
        E="TIER_MODE=$T APPROVAL=auto UPSTREAM_T1=${UPSTREAM_T1:-} UPSTREAM_T2=${UPSTREAM_T2:-http://127.0.0.1:8000}"
        quiesce; rm -f logs/tier-state.json; restart "$E"; echo "round $r $c arm $arm $(date +%T)"
        RUN_LABEL=$arm TIER_MODE=$T APPROVAL=auto timeout 900 bash scripts/run-archive.sh "$c" > /dev/null 2>&1
        if [ $? -eq 124 ]; then echo "timeout: gateway restart"; nemoclaw edge-agent gateway restart </dev/null >/dev/null 2>&1; sleep 20; fi
      done
    done
  done
  restart ""                                   # leave the services as deployed: both switches off
  echo ALL_DONE' > "$LOG" 2>&1 < /dev/null &
echo "$LOG"
