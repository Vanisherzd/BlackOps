#!/usr/bin/env bash
# Tier-1 server: a second vLLM on :8010 beside Tier-2 (scripts/serve-vllm.sh, Bolt on :8000), for scripts/tier.py.
#   T1_MODEL=<hf id> scripts/serve-tier1.sh          # detached container `vllm-tier1`
#   scripts/serve-tier1.sh stop
# The checkpoint is not pinned here on purpose: confirm the exact small Nemotron (4B class) and that it is in the HF
# cache / offline bundle at the workshop. It serves under the same name as Tier-2 (edge-agent), because the shim
# forwards the client's request body unchanged and only swaps the server. Loopback only: nothing but the shim talks to
# it. Parsers default to Tier-2's tool parser; set REASONING_PARSER to the one vLLM documents for the checkpoint (no
# reasoning parser is passed when it is unset). Memory: the box has no swap; Tier-2 takes 0.45 of it, keep this small.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${NAME:-vllm-tier1}"
if [ "${1:-}" = "stop" ]; then docker rm -f "$NAME"; exit 0; fi
: "${T1_MODEL:?set T1_MODEL to the Tier-1 checkpoint (HF id)}"
IMAGE="${IMAGE:-vllm/vllm-openai:nightly-77154ca6116ae34cbfe314beef1016bde9b63989}"
GPU_FLAGS="${GPU_FLAGS:-$([ -f /etc/nv_tegra_release ] && echo "--runtime nvidia" || echo "--gpus all")}"
ARGS=(--served-model-name "${SERVED_NAME:-edge-agent}" --host 127.0.0.1 --port "${PORT:-8010}"
      --max-model-len "${MAX_MODEL_LEN:-32768}" --max-num-seqs "${MAX_NUM_SEQS:-2}"
      --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.12}" --enable-chunked-prefill
      --trust-remote-code --enable-auto-tool-choice --tool-call-parser "${TOOL_PARSER:-qwen3_coder}")
[ -n "${REASONING_PARSER:-}" ] && ARGS+=(--reasoning-parser "$REASONING_PARSER")
# shellcheck disable=SC2086
docker run -d --name "$NAME" $GPU_FLAGS --ipc host --network host --restart no -e HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" \
  -v "${HF_HOME:-$HOME/.cache/huggingface}:/root/.cache/huggingface" "$IMAGE" "$T1_MODEL" "${ARGS[@]}"
for _ in $(seq 1 180); do curl -sf "http://127.0.0.1:${PORT:-8010}/health" >/dev/null && { echo "tier-1 healthy on :${PORT:-8010}"; exit 0; }; sleep 5; done
echo "tier-1 not healthy after 15 min: docker logs $NAME" >&2; exit 1
