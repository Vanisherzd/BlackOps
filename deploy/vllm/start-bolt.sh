#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd)
. "$SCRIPT_DIR/bolt.env.defaults"

command -v docker >/dev/null 2>&1 || {
  echo "error: docker is required to start $BOLT_CONTAINER" >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "error: cannot reach the Docker daemon; start Docker and retry" >&2
  exit 1
}

if docker ps --format '{{.Names}}' | grep -Fxq "$BOLT_CONTAINER"; then
  echo "error: $BOLT_CONTAINER is already running; stop it with: make bolt-down" >&2
  exit 1
fi

cd "$REPO_ROOT"
mkdir -p "$HF_HOME" "$VLLM_CACHE_DIR" "$BOLT_LOG_DIR"

set -- "$MODEL_ID" \
  --trust-remote-code \
  --load-format auto \
  --tensor-parallel-size 1 \
  --max-model-len "$MAX_MODEL_LEN" \
  --max-num-seqs "$MAX_NUM_SEQS" \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --enable-chunked-prefill \
  --kv-cache-dtype fp8 \
  --linear-backend b12x \
  --moe-backend b12x \
  --attention-backend FLASHINFER \
  --mamba-backend flashinfer \
  --mamba-ssm-cache-dtype bfloat16 \
  --mamba-cache-dtype bfloat16 \
  --mamba-cache-mode align \
  --seed 42 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser-plugin /opt/bolt/ultra_v3_reasoning_parser.py \
  --reasoning-parser ultra_v3 \
  --served-model-name "$SERVED_MODEL_NAME" \
  --host "$BOLT_HOST" \
  --port "$BOLT_PORT"

if [ -n "$MODEL_REVISION" ]; then
  set -- "$@" --revision "$MODEL_REVISION"
fi

# Intentionally omit speculative-decoding and media flags from the Phase 1 baseline.
if [ -n "$BOLT_EXTRA_ARGS" ]; then
  set -f
  # shellcheck disable=SC2086
  set -- "$@" $BOLT_EXTRA_ARGS
  set +f
fi

printf '%s\n' "$@" >"$BOLT_LOG_DIR/bolt-args.txt"

TOKEN_ENV=
OFFLINE_ENV=
[ -n "${HF_TOKEN:-}" ] && TOKEN_ENV='-e HF_TOKEN'
[ -n "${HF_HUB_OFFLINE:-}" ] && OFFLINE_ENV='-e HF_HUB_OFFLINE'

# BOLT_GPU_FLAGS: the Spark takes --gpus all; a Jetson (Thor) needs the NVIDIA runtime instead.
if [ -z "${BOLT_GPU_FLAGS:-}" ]; then
  if [ -f /etc/nv_tegra_release ]; then BOLT_GPU_FLAGS="--runtime nvidia"; else BOLT_GPU_FLAGS="--gpus all"; fi
fi

# shellcheck disable=SC2086
docker run -d \
  --name "$BOLT_CONTAINER" \
  $BOLT_GPU_FLAGS \
  --ipc=host \
  --network host \
  --restart no \
  -e VLLM_USE_V2_MODEL_RUNNER=1 \
  -e HF_HOME=/root/.cache/huggingface \
  $TOKEN_ENV $OFFLINE_ENV \
  -v "$HF_HOME:/root/.cache/huggingface" \
  -v "$VLLM_CACHE_DIR:/root/.cache/vllm" \
  "$BOLT_IMAGE" "$@"

docker logs --tail 40 "$BOLT_CONTAINER"
echo "health check: $SCRIPT_DIR/healthcheck.sh --wait 900"
