#!/usr/bin/env bash
# Tier-1 server: a second, small vLLM on :8010 beside Tier-2 (scripts/serve-vllm.sh on :8000), for scripts/tier.py.
# The generic profile of serve-vllm.sh with Tier-1 defaults: loopback only, container vllm-tier1, 0.12 of the memory.
#   T1_MODEL=<hf id> scripts/serve-tier1.sh            e.g. T1_MODEL=nvidia/NVIDIA-Nemotron-3-Nano-4B-...
#   scripts/serve-tier1.sh stop | status
# Same served name as Tier-2 (edge-agent): the shim forwards the client's body unchanged and only swaps the server.
# Every serve-vllm.sh generic variable applies (IMAGE, TOOL_PARSER, REASONING_PARSER, EXTRA_ARGS ...). The box has no
# swap and Tier-2 takes 0.45 of it: keep this small.
set -euo pipefail
cd "$(dirname "$0")/.."
export VLLM_PROFILE=generic NAME="${NAME:-vllm-tier1}" PORT="${PORT:-8010}" HOST="${HOST:-127.0.0.1}" \
       GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.12}"
if [ "${1:-}" = "stop" ] || [ "${1:-}" = "status" ]; then exec scripts/serve-vllm.sh "$1"; fi
export MODEL_ID="${T1_MODEL:?set T1_MODEL to the Tier-1 checkpoint (HF id)}"
exec scripts/serve-vllm.sh
