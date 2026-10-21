#!/usr/bin/env bash
# (Re)start the demo services around the inference servers, in the docs/demo.md 6.1 order and with the settings checked
# on the Thor on 2026-10-21, then run the preflight. With nothing on :8000 it first starts the Tier-2 the Thor uses
# (Nemotron-3.5-Lightning-30B-A3B-NVFP4, see scripts/serve-vllm.sh; T2_START=0 to refuse instead). A Tier-1 server is
# started separately: scripts/serve-tier1.sh on :8010.
#   scripts/stage-up.sh           (Tier-2 if missing,) restart shim, guard_api, ops_api, power, watchdog, dashboard;
#                                 preflight --warm
#   scripts/stage-up.sh status    one line per service
#   scripts/stage-up.sh down      stop those services (inference, victim and NemoClaw are left alone)
# Environment, with the stage defaults:
#   TIER (auto)          auto: Tier on when T1_URL answers /health; on: require it; off: never
#   T1_URL (http://127.0.0.1:8010)
#   TIER_NVPMODEL (1 on a Jetson with Tier on), NVP_T1 (1), NVP_T2 (0). On Thor modes 0 (MAXN) and 1 (120W) switch live;
#                        2 and 3 need a reboot, so they cannot follow the tier. Turned off if NVP_T1 does not take.
#   APPROVAL (manual), GUARDRAILS (1 when .nemo-venv exists), SHIM_ENV ("SHIM_THINK=1 SHIM_ADAPTIVE=1"),
#   POWER_TOTAL_RAIL (VIN on a Jetson: the module input), DASH_HOST (unset: every interface).
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
PY=.venv/bin/python
JETSON=0; [ -f /etc/nv_tegra_release ] && JETSON=1
SERVICES="dashboard watchdog power ops-api guard-api oai-shim"

script_of() { case "$1" in oai-shim) echo oai_shim ;; ops-api) echo ops_api ;; guard-api) echo guard_api ;; *) echo "$1" ;; esac; }
alive() { [ -f "logs/$1.pid" ] && kill -0 "$(cat "logs/$1.pid")" 2>/dev/null; }

stop_one() {
  alive "$1" && kill "$(cat "logs/$1.pid")" 2>/dev/null
  # the [.] keeps pkill from matching its own command line
  pkill -f "scripts/$(script_of "$1")[.]py" 2>/dev/null
  rm -f "logs/$1.pid"
}

start_one() {   # name, interpreter, then env assignments; the script is scripts/<name>.py (args via ARGS)
  local name=$1 py=$2; shift 2
  env "$@" setsid nohup "$py" "scripts/$(script_of "$name").py" ${ARGS:-} > "logs/$name.out" 2>&1 < /dev/null &
  echo $! > "logs/$name.pid"
}

wait_url() {   # url, seconds
  local i; for i in $(seq 1 "$2"); do curl -s -o /dev/null -m 2 "$1" && return 0; sleep 1; done; return 1
}

status() {
  local s
  for s in $SERVICES; do
    alive "$s" && echo "up    $s (pid $(cat "logs/$s.pid"))" || echo "down  $s"
  done
}

case "${1:-}" in
  status) status; exit 0 ;;
  down) for s in $SERVICES; do stop_one "$s"; done; status; exit 0 ;;
  "") ;;
  *) sed -n '5,7p' "$0"; exit 2 ;;
esac

if ! curl -sf -m 5 http://127.0.0.1:8000/health >/dev/null; then
  [ "${T2_START:-1}" = 1 ] || { echo "no Tier-2 server on :8000 (T2_START=0)" >&2; exit 1; }
  echo "no Tier-2 server on :8000: starting Lightning (about 100 s with the kernel caches in place)"
  [ $JETSON = 1 ] && { sync; sudo -n sh -c "echo 3 > /proc/sys/vm/drop_caches" 2>/dev/null || true; }
  GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.30}" VLLM_PROFILE=generic READY_TIMEOUT="${READY_TIMEOUT:-1800}" \
    MODEL_ID=nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 REASONING_PARSER=nemotron_v3 \
    EXTRA_ARGS="--moe-backend marlin --kv-cache-dtype fp8 --mamba-cache-mode align" scripts/serve-vllm.sh || exit 1
fi

T1_URL="${T1_URL:-http://127.0.0.1:8010}"
case "${TIER:-auto}" in
  off) TIER_ON=0 ;;
  on) curl -sf -m 5 "$T1_URL/health" >/dev/null || { echo "TIER=on but no Tier-1 server at $T1_URL" >&2; exit 1; }; TIER_ON=1 ;;
  *) curl -sf -m 5 "$T1_URL/health" >/dev/null && TIER_ON=1 || TIER_ON=0 ;;
esac

TIER_ENV=()
if [ $TIER_ON = 1 ]; then
  NVP_T1="${NVP_T1:-1}"; NVP_T2="${NVP_T2:-0}"
  TNV="${TIER_NVPMODEL:-$JETSON}"
  if [ "$TNV" = 1 ]; then
    # start in the patrol mode; a mode that wants a reboot reads "no" from /dev/null and is not applied
    sudo -n nvpmodel -m "$NVP_T1" < /dev/null >/dev/null 2>&1
    if [ "$(sudo -n nvpmodel -q 2>/dev/null | sed -n 2p)" != "$NVP_T1" ]; then
      echo "nvpmodel did not switch to $NVP_T1 without a reboot: TIER_NVPMODEL off" >&2; TNV=0
    fi
  fi
  TIER_ENV=(TIER_MODE=adaptive "UPSTREAM_T1=$T1_URL" "TIER_NVPMODEL=$TNV" "NVP_T1=$NVP_T1" "NVP_T2=$NVP_T2")
  echo "tier: adaptive, Tier-1 $T1_URL, nvpmodel link $TNV (T1 mode $NVP_T1, T2 mode $NVP_T2)"
else
  echo "tier: off (no Tier-1 server at $T1_URL); TIER_MODE stays unset so both tiers are not the same model on stage"
fi

APPROVAL="${APPROVAL:-manual}"
GUARD="${GUARDRAILS:-$([ -x .nemo-venv/bin/python ] && echo 1 || echo 0)}"
RAIL="${POWER_TOTAL_RAIL:-$([ $JETSON = 1 ] && echo VIN)}"

for s in $SERVICES; do stop_one "$s"; done
sleep 2

# shellcheck disable=SC2086
start_one oai-shim "$PY" ${SHIM_ENV:-SHIM_THINK=1 SHIM_ADAPTIVE=1} "${TIER_ENV[@]}"
wait_url http://127.0.0.1:8001/v1/models 20 || echo "WARN shim not answering on :8001 (logs/oai-shim.out)" >&2
if [ "$GUARD" = 1 ]; then
  start_one guard-api .nemo-venv/bin/python
  wait_url http://127.0.0.1:8792/ 60 || echo "WARN guardrails sidecar not answering on :8792 (logs/guard-api.out)" >&2
else
  echo "guardrails off (no .nemo-venv, or GUARDRAILS=0)"
fi
start_one ops-api "$PY" "GUARDRAILS=$GUARD" "APPROVAL=$APPROVAL" "${TIER_ENV[@]}"
wait_url http://127.0.0.1:8790/tools 20 || echo "WARN ops_api not answering on :8790 (logs/ops-api.out)" >&2
ARGS=log start_one power "$PY" ${RAIL:+"POWER_TOTAL_RAIL=$RAIL"}
start_one watchdog "$PY" WATCH_TRIAGE=1
start_one dashboard "$PY" DASH_ACTIONS=1 "APPROVAL=$APPROVAL" ${DASH_HOST:+"DASH_HOST=$DASH_HOST"}
wait_url http://127.0.0.1:8097/api/state 20 || echo "WARN dashboard not answering on :8097 (logs/dashboard.out)" >&2
sleep 3
status
echo
PRE=(DASHBOARD_URL=http://127.0.0.1:8097 "APPROVAL=$APPROVAL")
[ $TIER_ON = 1 ] && PRE+=("T1_URL=$T1_URL")
env "${PRE[@]}" scripts/preflight.sh --warm
