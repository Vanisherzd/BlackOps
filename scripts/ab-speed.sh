#!/usr/bin/env bash
# Speed A/B, 3 rounds (scripts/ab-speed.sh): A = thinking on + measured-health verifier (as deployed); B = A + adaptive thinking
# (SHIM_ADAPTIVE=1) + system triage in the alert (TRIAGE=1). Before every drill both the ops API and the shim are
# restarted with the arm's env, so each drill runs the code on disk: the ops API had served code older than the last
# commit for a day, its in-memory modules loaded before the edits (2026-10-20).
cd ~/edge-agent || exit 1
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
BASE="logs/ab-$(date +%Y%m%d-%H%M%S)"
CASES="nginx-stopped bad-config upstream-down bad-upstream-name container-removed crashloop upstream-paused upstream-detached
       upstream-500 inode-exhausted upstream-perm-denied upstream-sigstop upstream-429 front-mime-missing
       upstream-config-crash upstream-pids-limit front-worker-connections upstream-redirect"
quiesce() { local i; for i in $(seq 1 60); do age=$(( $(date +%s) - $(stat -c %Y logs/ops-api.log 2>/dev/null || echo 0) )); [ $age -ge 60 ] && return; sleep 10; done; }
restart_ops() { [ -f logs/ops-api.pid ] && kill "$(cat logs/ops-api.pid)" 2>/dev/null; pkill -f "scripts/ops_api[.]py"; sleep 2
  setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null & echo $! > logs/ops-api.pid; sleep 3; }
{
echo "speed A=[think+verifier] B=[think+verifier+SHIM_ADAPTIVE=1+TRIAGE=1] base=$BASE code=$(git rev-parse --short HEAD) dirty=$(git status --short | wc -l)"
for round in 1 2 3; do
  for c in $CASES; do
    for arm in A B; do
      if [ $arm = A ]; then SE="SHIM_THINK=1"; TR=0; else SE="SHIM_THINK=1 SHIM_ADAPTIVE=1"; TR=1; fi
      quiesce; restart_ops; bash scripts/shim-env.sh "$SE" > /dev/null; echo "round $round $c arm $arm $(date +%T)"
      TRIAGE=$TR timeout 600 bash scripts/nemoclaw-drill.sh $c >> $BASE-$arm.log 2>&1
      if [ $? -eq 124 ]; then echo "timeout: gateway restart"; nemoclaw edge-agent gateway restart </dev/null >/dev/null 2>&1; fi
      echo "########" >> $BASE-$arm.log
    done
  done
done
bash scripts/shim-env.sh "SHIM_THINK=1"; restart_ops
echo ALL_DONE
} > $BASE.log 2>&1
