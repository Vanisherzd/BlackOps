#!/usr/bin/env bash
# Pre-stage check: every service the demo needs, one line each, exit 1 if any FAIL. Read-only by default (GETs and
# status queries; nothing lands in logs/ops-api.log, so a running drill's quiesce is not disturbed).
#   scripts/preflight.sh            # check
#   scripts/preflight.sh --warm     # also one short completion per model, so the stage never sees a cold start
# Endpoints (env): T2_URL (default http://127.0.0.1:8000, trtllm-serve), T1_URL (unset = no Tier-1 server yet, SKIP),
# SHIM_URL (8001), OPS_URL (8790), DASHBOARD_URL (unset = SKIP), SERVED_NAME (edge-agent), T1_SERVED_NAME.
cd ~/edge-agent || exit 1
T2_URL="${T2_URL:-http://127.0.0.1:8000}"; SHIM_URL="${SHIM_URL:-http://127.0.0.1:8001}"
OPS_URL="${OPS_URL:-http://127.0.0.1:8790}"; WARM=0; [ "$1" = "--warm" ] && WARM=1
FAILS=0
row() { printf '%-5s %-22s %s\n' "$1" "$2" "$3"; [ "$1" = FAIL ] && FAILS=$((FAILS + 1)); return 0; }
code() { curl -s -o /dev/null -m "${2:-5}" -w '%{http_code}' "$1" 2>/dev/null; }
fresh() { [ -f "$1" ] && [ $(( $(date +%s) - $(stat -c %Y "$1") )) -le "$2" ]; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

c=$(code "$T2_URL/health"); [ "$c" = 200 ] && row PASS tier-2-server "$T2_URL" || row FAIL tier-2-server "$T2_URL/health -> $c"
if [ -n "$T1_URL" ]; then
  c=$(code "$T1_URL/health"); [ "$c" = 200 ] && row PASS tier-1-server "$T1_URL" || row FAIL tier-1-server "$T1_URL/health -> $c"
else row SKIP tier-1-server "T1_URL not set"; fi
c=$(code "$SHIM_URL/v1/models"); [ "$c" != 000 ] && row PASS shim "$SHIM_URL (HTTP $c)" || row FAIL shim "$SHIM_URL not answering"
c=$(code "$OPS_URL/tools"); [ "$c" = 200 ] && row PASS ops-api "$OPS_URL" || row FAIL ops-api "$OPS_URL/tools -> $c"
if [ -n "$DASHBOARD_URL" ]; then
  c=$(code "$DASHBOARD_URL/api/state"); [ "$c" = 200 ] && row PASS dashboard "$DASHBOARD_URL" || row FAIL dashboard "-> $c"
else row SKIP dashboard "DASHBOARD_URL not set"; fi
if [ -f scripts/watchdog.py ]; then
  alive logs/watchdog.pid && row PASS watchdog "pid $(cat logs/watchdog.pid)" || row FAIL watchdog "not running (logs/watchdog.pid)"
else row SKIP watchdog "no scripts/watchdog.py"; fi
if [ -f logs/power.jsonl ]; then
  fresh logs/power.jsonl 10 && row PASS power-logger "logs/power.jsonl fresh" || row FAIL power-logger "logs/power.jsonl stale"
else row SKIP power-logger "no logs/power.jsonl yet"; fi
# nemoclaw status takes the host lock a running agent turn holds; never contend with a drill for it
if pgrep -f nemoclaw-drill.sh >/dev/null; then row SKIP nemoclaw "a drill is running (it holds the nemoclaw lock)"
elif command -v nemoclaw >/dev/null 2>&1 || [ -x "$HOME/.npm-global/bin/nemoclaw" ]; then
  INF=$(PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH" timeout 20 nemoclaw edge-agent status 2>/dev/null | grep -i "Inference" | head -1)
  echo "$INF" | grep -qi healthy && row PASS nemoclaw "$(echo "$INF" | tr -s ' ')" || row FAIL nemoclaw "Inference: ${INF:-no answer}"
else row FAIL nemoclaw "nemoclaw CLI not on PATH"; fi
pgrep -f nemoclaw-drill.sh >/dev/null && row INFO drill "a drill is running: the victim check below sees its fault"
V=$(.venv/bin/python scripts/faults.py verify 2>&1 | tail -1)
echo "$V" | grep -q PASS && row PASS victim "$V" || row FAIL victim "${V:-faults.py verify gave nothing} (faults.py reset)"
if [ -x scripts/wan.sh ]; then
  W=$(scripts/wan.sh status 2>&1 | head -1); echo "$W" | grep -q '^online' && row PASS wan "$W" || row FAIL wan "$W"
else row SKIP wan "no scripts/wan.sh"; fi
row INFO approval "APPROVAL=${APPROVAL:-unset} (stage wants manual)"
if command -v nvpmodel >/dev/null 2>&1; then row INFO nvpmodel "$(nvpmodel -q 2>/dev/null | head -1)"; fi

if [ $WARM = 1 ]; then
  warm() {   # $1 url, $2 served name
    local t0=$(date +%s.%N) c
    c=$(curl -s -o /dev/null -m 120 -w '%{http_code}' "$1/v1/chat/completions" -H 'Content-Type: application/json' \
      -d "{\"model\":\"$2\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8}")
    [ "$c" = 200 ] && row PASS "warm $2" "$(echo "$(date +%s.%N) - $t0" | bc | cut -c1-5)s" || row FAIL "warm $2" "$1 -> $c"
  }
  warm "$T2_URL" "${SERVED_NAME:-edge-agent}"
  [ -n "$T1_URL" ] && warm "$T1_URL" "${T1_SERVED_NAME:-edge-agent-t1}"
fi
echo; [ $FAILS = 0 ] && echo "PREFLIGHT GREEN" || echo "PREFLIGHT: $FAILS FAIL"
[ $FAILS = 0 ]
