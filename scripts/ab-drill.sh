#!/usr/bin/env bash
# A/B drills: the same case, alternating two ops-API environments, one log per arm (score each with scripts/score.py).
#   scripts/ab-drill.sh <case> <runs-per-arm> "<env A>" "<env B>"
#   scripts/ab-drill.sh inode-exhausted 5 "OPS_INODES=on-request" "OPS_INODES=on-request KB_RETRIEVAL=v1"
# Runs detached; logs/ab-<timestamp>-A.log and -B.log, ends with ALL_DONE in logs/ab-<timestamp>.log.
# The ops API is restarted by pid file before every drill (logs/ops-api.pid), so each arm runs with its own env.
cd ~/edge-agent || exit 1
CASE="$1"; N="$2"; ENV_A="$3"; ENV_B="$4"
TS=$(date +%Y%m%d-%H%M%S); BASE="logs/ab-$TS"
setsid nohup bash -c '
  restart_ops() {
    [ -f logs/ops-api.pid ] && kill "$(cat logs/ops-api.pid)" 2>/dev/null; sleep 2
    env $1 setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null &
    echo $! > logs/ops-api.pid; sleep 3
  }
  echo "case='"$CASE"' runs/arm='"$N"' A=['"$ENV_A"'] B=['"$ENV_B"']"
  for i in $(seq 1 '"$N"'); do
    for arm in A B; do
      [ $arm = A ] && E="'"$ENV_A"'" || E="'"$ENV_B"'"
      restart_ops "$E"; echo "run $i arm $arm env [$E] $(date +%T)"
      timeout 600 bash scripts/nemoclaw-drill.sh '"$CASE"' >> '"$BASE"'-$arm.log 2>&1; echo "########" >> '"$BASE"'-$arm.log
    done
  done
  echo ALL_DONE' > "$BASE.log" 2>&1 < /dev/null &
echo "$BASE"
