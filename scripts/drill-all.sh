#!/usr/bin/env bash
# Run several drills detached from the ssh session, so a dropped connection neither kills nor hides the run.
#   scripts/drill-all.sh crashloop upstream-500          -> logs/drills-<timestamp>.log, prints the log path
# Watch: tail -f <log>; the run is finished when the file ends with ALL_DONE.
#
# Quiesce between drills (2026-10-14): a drill killed by its timeout does not stop the sandbox agent. After a
# "Context overflow" OpenClaw compacts and carries on in another transcript, and its ops calls landed in the next
# drills (a stray recreate_stack built the container a later drill was credited for). Before the next reset, wait
# until the ops API has been quiet for 60 s (at most 10 min) and say so in the log.
cd ~/edge-agent || exit 1
LOG="logs/drills-$(date +%Y%m%d-%H%M%S).log"
setsid nohup bash -c '
  quiesce() {
    for i in $(seq 1 60); do
      age=$(( $(date +%s) - $(stat -c %Y logs/ops-api.log 2>/dev/null || echo 0) ))
      [ $age -ge 60 ] && { echo "quiet: ops API idle ${age}s"; return; }
      sleep 10
    done
    echo "quiet: gave up after 10 min, ops API still busy"
  }
  for c in '"$*"'; do quiesce; timeout 900 bash scripts/nemoclaw-drill.sh $c; echo "########"; done; quiesce; echo ALL_DONE' \
  > "$LOG" 2>&1 < /dev/null &
echo "$LOG"
