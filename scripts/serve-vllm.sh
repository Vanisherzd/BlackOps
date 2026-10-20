#!/usr/bin/env bash
# The agent stack's inference server: Nemotron-3.5-Bolt-74B-A9B on vLLM (deploy/vllm/, from feat/blackbox-bolt-vllm),
# served as `edge-agent` on :8000, the name and port every client already uses (oai_shim UPSTREAM, agent.py and
# reviewer.py VLLM_URL, kb-eval, preflight). So nothing downstream changes when the engine does. Replaces
# scripts/serve-trt.sh as the default (2026-10-20 team decision: vLLM, not TensorRT-LLM); serve-trt.sh stays for the
# measurements already published on it.
#   scripts/serve-vllm.sh            # start (detached container `vllm-edge`), then wait for /health up to 15 min
#   scripts/serve-vllm.sh stop
# Every deploy/vllm/bolt.env.defaults variable can be overridden from the environment (MODEL_ID, GPU_MEMORY_UTILIZATION,
# MAX_MODEL_LEN ...). Two concurrent sequences: the change reviewer and a preflight warm-up can overlap an agent turn.
set -euo pipefail
cd "$(dirname "$0")/.."
export BOLT_CONTAINER="${BOLT_CONTAINER:-vllm-edge}" SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-edge-agent}" \
       BOLT_PORT="${BOLT_PORT:-${PORT:-8000}}" BOLT_HOST="${BOLT_HOST:-0.0.0.0}" MAX_NUM_SEQS="${MAX_NUM_SEQS:-2}" \
       HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" BOLT_LOG_DIR="${BOLT_LOG_DIR:-$PWD/logs}"
if [ "${1:-}" = "stop" ]; then exec deploy/vllm/stop-bolt.sh; fi
if docker ps --format '{{.Names}}' | grep -qx trtllm-edge; then
  echo "trtllm-edge is running and holds the GPU memory; stop it first (docker stop trtllm-edge)" >&2; exit 1
fi
if curl -sf "http://127.0.0.1:$BOLT_PORT/health" >/dev/null 2>&1; then
  echo "something already serves :$BOLT_PORT; not starting a second server" >&2; exit 0
fi
deploy/vllm/start-bolt.sh
exec deploy/vllm/healthcheck.sh --wait 900
