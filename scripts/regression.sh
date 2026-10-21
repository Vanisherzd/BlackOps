#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

REPEATS=2
ENTER_DRILL_MODE=1
CASES=()

usage() {
  echo "usage: scripts/regression.sh [-n REPEATS] [--no-drill-mode] CASE [CASE...]" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -n)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      REPEATS=$2
      shift 2
      ;;
    --no-drill-mode)
      ENTER_DRILL_MODE=0
      shift
      ;;
    --)
      shift
      CASES+=("$@")
      break
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      CASES+=("$1")
      shift
      ;;
  esac
done

case "$REPEATS" in
  ''|*[!0-9]*|0) echo "REPEATS must be a positive integer" >&2; exit 2 ;;
esac
[ "${#CASES[@]}" -gt 0 ] || { usage; exit 2; }

python3 - "${CASES[@]}" <<'PY'
import sys

sys.path.insert(0, "scripts")
from faults import CASES

unknown = [case for case in sys.argv[1:] if case not in CASES]
if unknown:
    raise SystemExit(f"unknown case(s): {', '.join(unknown)}")
PY

STAMP=$(date +%Y%m%d-%H%M%S)
RUN_DIR="logs/regression-$STAMP"
mkdir -p "$RUN_DIR"
RUN_DATE=$(date -Iseconds)
GIT_HEAD=$(git rev-parse HEAD)
export RUN_DATE GIT_HEAD
export ENTER_DRILL_MODE
python3 - "$RUN_DIR/run.json" <<'PY'
import json
import os
import pathlib
import sys

knobs = ("SPEC", "RERANK_URL", "KB_RETRIEVAL", "COMMUNITY_BACKEND", "AGENT_MODE")
run = {
    "git_head": os.environ["GIT_HEAD"],
    "date": os.environ["RUN_DATE"],
    "env": {
        name: "v4" if name == "KB_RETRIEVAL" and os.environ["ENTER_DRILL_MODE"] == "1"
        else os.environ.get(name)
        for name in knobs
    },
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(run, indent=2) + "\n")
PY

MEM_ACTIVE=0
PRINT_RESTORE=1

restore_command() {
  local name
  printf 'Run afterwards to restore the stage:'
  for name in SPEC RERANK_URL KB_RETRIEVAL COMMUNITY_BACKEND AGENT_MODE; do
    if [ -v "$name" ]; then
      printf ' %s=%q' "$name" "${!name}"
    fi
  done
  printf ' scripts/stage-up.sh\n'
}

finish() {
  local status=$?
  trap - EXIT
  set +e
  if [ "$MEM_ACTIVE" = 1 ]; then
    scripts/mem-profile.sh stop
  fi
  if [ "$PRINT_RESTORE" = 1 ]; then
    restore_command
  fi
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT TERM HUP

alive() {
  [ -f "logs/$1.pid" ] && kill -0 "$(cat "logs/$1.pid")" 2>/dev/null
}

script_of() {
  case "$1" in
    ops-api) echo ops_api ;;
    *) echo "$1" ;;
  esac
}

stop_one() {
  if alive "$1"; then
    kill "$(cat "logs/$1.pid")" 2>/dev/null || true
  fi
  pkill -f "scripts/$(script_of "$1")[.]py" 2>/dev/null || true
}

wait_for_ops_api() {
  local attempts=60
  while [ "$attempts" -gt 0 ]; do
    if curl -sf -m 2 http://127.0.0.1:8790/tools >/dev/null; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  echo "ops-api did not answer on http://127.0.0.1:8790/tools after 60 seconds" >&2
  return 1
}

if [ "$ENTER_DRILL_MODE" = 1 ]; then
  stop_one watchdog
  stop_one ops-api
  sleep 2
  env GUARDRAILS=1 APPROVAL=auto KB_RETRIEVAL=v4 REVIEW_THINK=0 \
    setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null &
  echo "$!" > logs/ops-api.pid
  wait_for_ops_api
fi

RUN_START=$(date +%s)
scripts/mem-profile.sh start
MEM_ACTIVE=1

DRILL_CASES=()
for case_name in "${CASES[@]}"; do
  for ((repeat = 0; repeat < REPEATS; repeat++)); do
    DRILL_CASES+=("$case_name")
  done
done

DRILL_LOG=$(scripts/drill-all.sh "${DRILL_CASES[@]}")
DEADLINE=$((RUN_START + 3600))
while ! { [ -f "$DRILL_LOG" ] && [ "$(tail -n 1 "$DRILL_LOG")" = ALL_DONE ]; }; do
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "timed out after 60 minutes waiting for ALL_DONE in $DRILL_LOG" >&2
    exit 1
  fi
  sleep 15
done

RUN_END=$(date +%s)
scripts/mem-profile.sh stop
MEM_ACTIVE=0

RUN_LOG="$RUN_DIR/$(basename "$DRILL_LOG")"
cp "$DRILL_LOG" "$RUN_LOG"
SCORES="$RUN_DIR/score.json"
SUMMARY="$RUN_DIR/summary.md"
{
  printf '# Regression summary\n\n## Scorecard\n\n'
  python3 scripts/score.py "$RUN_LOG" --shim logs/oai-shim.log --json "$SCORES"
  printf '\n\n## Memory\n\n'
  scripts/mem-profile.sh report --since "$RUN_START" --until "$RUN_END"
  printf '\n\n## Compact per-case results\n\n'
  python3 scripts/regression_summary.py "$SCORES"
} > "$SUMMARY"

echo "Regression artifacts: $RUN_DIR"
