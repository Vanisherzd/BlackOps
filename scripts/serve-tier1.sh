#!/usr/bin/env bash
# Tier-1 server: a second trtllm-serve on :8010 next to Tier-2 on :8000, through the same scripts/serve-trt.sh.
#   T1_MODEL=<hf id of the 4B checkpoint> scripts/serve-tier1.sh
# The HF id is not pinned here on purpose: confirm the exact Nemotron Nano 4B checkpoint (and that it is in the HF
# cache / offline bundle) at the workshop. It serves under the same name as Tier-2 (edge-agent), because the shim
# forwards the client's request body unchanged and only swaps the server (scripts/tier.py). Loopback only: nothing
# but the shim talks to it. Check first, from the shim's point of view, that its tool calls and reasoning come back in
# the shape the shim parses: REASONING_PARSER / TOOL_PARSER default to Tier-2's (nano-v3, qwen3_coder).
cd "$(dirname "$0")/.." || exit 1
: "${T1_MODEL:?set T1_MODEL to the Tier-1 checkpoint (HF id)}"
MODEL="$T1_MODEL" NAME="${NAME:-trtllm-tier1}" PORT="${PORT:-8010}" HOST="${HOST:-127.0.0.1}" \
  YAML="${YAML:-$PWD/trt/tier1.yaml}" SERVED_NAME="${SERVED_NAME:-edge-agent}" exec scripts/serve-trt.sh "$@"
