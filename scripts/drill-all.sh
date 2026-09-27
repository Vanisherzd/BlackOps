#!/usr/bin/env bash
# Run several drills detached from the ssh session, so a dropped connection neither kills nor hides the run.
#   scripts/drill-all.sh crashloop upstream-500          -> logs/drills-<timestamp>.log, prints the log path
# Watch: tail -f <log>; the run is finished when the file ends with ALL_DONE.
cd ~/edge-agent || exit 1
LOG="logs/drills-$(date +%Y%m%d-%H%M%S).log"
setsid nohup bash -c "for c in $*; do timeout 900 bash scripts/nemoclaw-drill.sh \$c; echo '########'; done; echo ALL_DONE" \
  > "$LOG" 2>&1 < /dev/null &
echo "$LOG"
