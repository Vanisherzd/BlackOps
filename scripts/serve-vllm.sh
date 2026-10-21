#!/usr/bin/env bash
# The agent stack's inference server on vLLM, served as `edge-agent` on :8000: the name and port every client already
# uses (oai_shim UPSTREAM, agent.py and reviewer.py VLLM_URL, kb-eval, preflight), so nothing downstream changes when
# the model or the engine image does. Replaces scripts/serve-trt.sh as the default (2026-10-20 team decision).
#
# One switch, VLLM_PROFILE, picks the launch recipe:
#   bolt     (default) Nemotron-3.5-Bolt-74B-A9B with the model card's recipe, through deploy/vllm/start-bolt.sh and its
#            image (blackbox-vllm-bolt: vLLM nightly + b12x kernels + the ultra_v3 reasoning-parser plugin). Validated
#            on the DGX Spark 2026-10-21 (NemoClaw drills 15/15).
#   generic  plain `vllm serve <MODEL_ID>` in any vLLM image: the NGC Jetson Thor container, upstream vllm-openai, ...
#            Standard flags only (chunked prefill, auto tool choice); parsers and extra flags from the environment. A
#            reasoning parser the checkpoint ships as <name>_reasoning_parser.py (Nano: nano_v3) is found and loaded as
#            a plugin; for Bolt, REASONING_PARSER=ultra_v3 mounts the vendored one if the model repo has none.
#
#   scripts/serve-vllm.sh                      start, then wait for /health and the edge-agent model (up to 15 min)
#   scripts/serve-vllm.sh stop                 remove the container (either profile)
#   scripts/serve-vllm.sh status               container, health, served models
# Examples (docs/thor-bringup.md):
#   VLLM_PROFILE=generic IMAGE=nvcr.io/nvidia/vllm:26.01-py3 MODEL_ID=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 \
#     REASONING_PARSER=nano_v3 scripts/serve-vllm.sh
#   VLLM_PROFILE=generic IMAGE=nvcr.io/nvidia/vllm:26.01-py3 MODEL_ID=nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB \
#     REASONING_PARSER=ultra_v3 scripts/serve-vllm.sh
# Environment (both profiles): MODEL_ID, PORT (8000), HOST (0.0.0.0), NAME (vllm-edge), SERVED_NAME (edge-agent),
#   GPU_MEMORY_UTILIZATION (0.45), MAX_MODEL_LEN (32768), MAX_NUM_SEQS (2), HF_HOME, HF_HUB_OFFLINE (1),
#   GPU_FLAGS (--runtime nvidia on a Jetson, --gpus all elsewhere). DRY_RUN=1 (generic): print the docker command only.
# generic only: IMAGE, TOOL_PARSER (qwen3_coder), REASONING_PARSER (none), EXTRA_ARGS, ENTRY ("vllm serve" unless the
#   image's own entrypoint is the server, as in vllm/vllm-openai and blackbox-vllm-bolt).
# The box has no swap and one inference server takes about half of it: this refuses to start beside trtllm-edge or a
# second vLLM server on the same port.
set -euo pipefail
cd "$(dirname "$0")/.."
PROFILE="${VLLM_PROFILE:-bolt}"
NAME="${NAME:-${BOLT_CONTAINER:-vllm-edge}}"
PORT="${PORT:-${BOLT_PORT:-8000}}"
HOST="${HOST:-${BOLT_HOST:-0.0.0.0}}"
SERVED_NAME="${SERVED_NAME:-${SERVED_MODEL_NAME:-edge-agent}}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

health() { curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }

case "${1:-}" in
  stop) docker rm -f "$NAME" >/dev/null 2>&1 && echo "removed $NAME" || echo "no container $NAME"; exit 0 ;;
  status)
    docker ps --filter "name=^${NAME}$" --format '{{.Names}} {{.Image}} {{.Status}}'
    health && echo "health: ok" || echo "health: down"
    curl -s "http://127.0.0.1:$PORT/v1/models" 2>/dev/null | python3 -c "import json,sys; print('models:', [m['id'] for m in json.load(sys.stdin)['data']])" 2>/dev/null || true
    exit 0 ;;
esac

if docker ps --format '{{.Names}}' | grep -qx trtllm-edge; then
  echo "trtllm-edge is running and holds the GPU memory; stop it first (docker stop trtllm-edge)" >&2; exit 1
fi
if health; then
  echo "something already serves :$PORT; not starting a second server (scripts/serve-vllm.sh stop first)" >&2; exit 0
fi

wait_ready() {   # /health, then the served name in /v1/models (a server can be healthy before the model is listed)
  local i
  for i in $(seq 1 180); do
    if health && curl -s "http://127.0.0.1:$PORT/v1/models" | grep -q "\"$SERVED_NAME\""; then
      echo "ready: $SERVED_NAME on :$PORT ($PROFILE)"; return 0
    fi
    docker ps --format '{{.Names}}' | grep -qx "$NAME" || { echo "container $NAME exited; docker logs $NAME" >&2; return 1; }
    sleep 5
  done
  echo "not ready after 15 min; docker logs $NAME" >&2; return 1
}

case "$PROFILE" in
  bolt)
    export BOLT_CONTAINER="$NAME" BOLT_PORT="$PORT" BOLT_HOST="$HOST" SERVED_MODEL_NAME="$SERVED_NAME" \
           MAX_NUM_SEQS="${MAX_NUM_SEQS:-2}" BOLT_LOG_DIR="${BOLT_LOG_DIR:-$PWD/logs}"
    [ -n "${MODEL_ID:-}" ] && export MODEL_ID
    [ -n "${GPU_FLAGS:-}" ] && export BOLT_GPU_FLAGS="$GPU_FLAGS"
    deploy/vllm/start-bolt.sh
    wait_ready ;;
  generic)
    : "${MODEL_ID:?generic profile: set MODEL_ID (an HF id in the cache, or a local path)}"
    IMAGE="${IMAGE:-vllm/vllm-openai:nightly-77154ca6116ae34cbfe314beef1016bde9b63989}"
    GPU_FLAGS="${GPU_FLAGS:-$([ -f /etc/nv_tegra_release ] && echo "--runtime nvidia" || echo "--gpus all")}"
    case "$IMAGE" in
      vllm/vllm-openai*|blackbox-vllm-bolt*) ENTRY="${ENTRY-}" ;;   # the image's entrypoint is already the server
      *) ENTRY="${ENTRY-vllm serve}" ;;
    esac
    ARGS=(--served-model-name "$SERVED_NAME" --host "$HOST" --port "$PORT"
          --max-model-len "${MAX_MODEL_LEN:-32768}" --max-num-seqs "${MAX_NUM_SEQS:-2}"
          --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.45}" --enable-chunked-prefill --trust-remote-code
          --enable-auto-tool-choice --tool-call-parser "${TOOL_PARSER:-qwen3_coder}")
    MOUNTS=(-v "${HF_HOME:-$HOME/.cache/huggingface}:/root/.cache/huggingface")
    # Nemotron checkpoints ship their reasoning parser as a plugin file (Nano: nano_v3_reasoning_parser.py in the
    # model repo; Bolt: ultra_v3, vendored under deploy/vllm/). Found in the HF cache, it is passed as a plugin; the
    # cache is mounted at the same relative path, so the container sees it too.
    RP="${REASONING_PARSER:-}"
    SNAP=$(ls -d "${HF_HOME:-$HOME/.cache/huggingface}/hub/models--${MODEL_ID//\//--}/snapshots/"*/ 2>/dev/null | head -1 || true)
    if [ -n "$RP" ] && [ -n "$SNAP" ] && [ -f "$SNAP${RP}_reasoning_parser.py" ]; then
      ARGS+=(--reasoning-parser-plugin "/root/.cache/huggingface/${SNAP#"${HF_HOME:-$HOME/.cache/huggingface}/"}${RP}_reasoning_parser.py")
    elif [ "$RP" = "ultra_v3" ]; then
      MOUNTS+=(-v "$PWD/deploy/vllm/ultra_v3_reasoning_parser.py:/opt/bolt/ultra_v3_reasoning_parser.py:ro")
      ARGS+=(--reasoning-parser-plugin /opt/bolt/ultra_v3_reasoning_parser.py)
    fi
    [ -n "${REASONING_PARSER:-}" ] && ARGS+=(--reasoning-parser "$REASONING_PARSER")
    # shellcheck disable=SC2206
    [ -n "${EXTRA_ARGS:-}" ] && ARGS+=($EXTRA_ARGS)
    if [ "${DRY_RUN:-0}" = "1" ]; then                       # print what would run; start nothing
      echo docker run -d --name "$NAME" $GPU_FLAGS --ipc host --network host --restart no -e HF_HUB_OFFLINE \
        -e HF_HOME=/root/.cache/huggingface "${MOUNTS[@]}" "$IMAGE" $ENTRY "$MODEL_ID" "${ARGS[@]}"; exit 0
    fi
    mkdir -p logs
    # shellcheck disable=SC2086
    printf '%s\n' "$IMAGE" $ENTRY "$MODEL_ID" "${ARGS[@]}" > logs/vllm-edge-args.txt
    # shellcheck disable=SC2086
    docker run -d --name "$NAME" $GPU_FLAGS --ipc host --network host --restart no \
      -e HF_HUB_OFFLINE -e HF_HOME=/root/.cache/huggingface "${MOUNTS[@]}" \
      "$IMAGE" $ENTRY "$MODEL_ID" "${ARGS[@]}" >/dev/null
    echo "started $NAME: $IMAGE $MODEL_ID (args in logs/vllm-edge-args.txt)"
    wait_ready ;;
  *) echo "VLLM_PROFILE must be bolt or generic, not '$PROFILE'" >&2; exit 2 ;;
esac
