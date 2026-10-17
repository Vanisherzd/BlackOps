#!/usr/bin/env bash
# Run several drills detached from the ssh session, so a dropped connection neither kills nor hides the run.
#   scripts/drill-all.sh crashloop upstream-500          -> logs/drills-<timestamp>.log, prints the log path
# Watch: tail -f <log>; the run is finished when the file ends with ALL_DONE.
#
# Quiesce between drills (2026-10-14): a drill killed by its timeout does not stop the sandbox agent. After a
# "Context overflow" OpenClaw compacts and carries on in another transcript, and its ops calls landed in the next
# drills (a stray recreate_stack built the container a later drill was credited for). Before the next reset, wait
# until the ops API has been quiet for 60 s (at most 10 min) and say so in the log.
# A quiet ops API is not proof (2026-10-17): a killed run sat 73 s in thought, passed the quiet check, and its calls
# landed in the next two drills, one stray recreate_stack building the container a drill was credited for. When the
# timeout kills a drill (exit 124), restart the sandbox gateway, which ends every in-flight agent run, then quiesce.
cd ~/edge-agent || exit 1
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
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
  for c in '"$*"'; do
    quiesce
    timeout 900 bash scripts/nemoclaw-drill.sh $c
    if [ $? -eq 124 ]; then
      echo "killed by the timeout: restarting the sandbox gateway to end its agent run"
      nemoclaw edge-agent gateway restart </dev/null >/dev/null 2>&1
      sleep 20
    fi
    echo "########"
  done; quiesce; echo ALL_DONE' \
  > "$LOG" 2>&1 < /dev/null &
echo "$LOG"
