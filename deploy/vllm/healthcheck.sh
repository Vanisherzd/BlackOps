#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/bolt.env.defaults"

usage() {
  echo "usage: $0 [--wait SECONDS]" >&2
  exit 2
}

WAIT_SECONDS=
if [ "$#" -gt 0 ]; then
  [ "$#" -eq 2 ] && [ "$1" = "--wait" ] || usage
  case "$2" in
    ''|*[!0-9]*) usage ;;
  esac
  WAIT_SECONDS=$2
fi

command -v curl >/dev/null 2>&1 || {
  echo "error: curl is required for the Bolt health check" >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  echo "error: python3 is required to read the vLLM model response" >&2
  exit 1
}

HEALTH_BODY=$(mktemp)
MODELS_BODY=$(mktemp)
trap 'rm -f "$HEALTH_BODY" "$MODELS_BODY"' 0 HUP INT TERM
BASE_URL="http://127.0.0.1:$BOLT_PORT"

check_once() {
  curl -sfS --connect-timeout 5 --max-time 10 "$BASE_URL/health" >"$HEALTH_BODY" || return 1
  curl -sfS --connect-timeout 5 --max-time 10 "$BASE_URL/v1/models" >"$MODELS_BODY" || return 1
  python3 - "$MODELS_BODY" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as response:
    payload = json.load(response)
names = [item["id"] for item in payload.get("data", []) if "id" in item]
if not names:
    raise SystemExit("error: /v1/models returned no served model names")
print("served model names: " + ", ".join(names))
PY
}

if [ -z "$WAIT_SECONDS" ]; then
  check_once || {
    echo "error: Bolt is not healthy at $BASE_URL" >&2
    exit 1
  }
  exit 0
fi

STARTED_AT=$(date +%s)
while :; do
  if check_once 2>/dev/null; then
    ELAPSED=$(( $(date +%s) - STARTED_AT ))
    echo "Bolt healthy after ${ELAPSED}s"
    exit 0
  fi
  ELAPSED=$(( $(date +%s) - STARTED_AT ))
  if [ "$ELAPSED" -ge "$WAIT_SECONDS" ]; then
    check_once || true
    echo "error: Bolt did not become healthy within ${WAIT_SECONDS}s at $BASE_URL" >&2
    exit 1
  fi
  echo "waiting for Bolt: ${ELAPSED}s elapsed"
  REMAINING=$(( WAIT_SECONDS - ELAPSED ))
  if [ "$REMAINING" -lt 5 ]; then
    sleep "$REMAINING"
  else
    sleep 5
  fi
done
