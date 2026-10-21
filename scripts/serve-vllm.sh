#!/usr/bin/env bash
# The agent stack's inference server on vLLM, served as `edge-agent` on :8000: the name and port every client already
# uses (oai_shim UPSTREAM, agent.py and reviewer.py VLLM_URL, kb-eval, preflight), so nothing downstream changes when
# the model or the engine image does. Replaces scripts/serve-trt.sh as the default (2026-10-20 team decision).
#
# One switch, VLLM_PROFILE, picks the launch recipe:
#   lightning (default) Nemotron-3.5-Lightning-30B-A3B-NVFP4 with the recipe validated on the Thor (generic profile,
#            Marlin MoE, fp8 KV, GPU_MEMORY_UTILIZATION 0.30): what the stage runs since 2026-10-21.
#   bolt     Nemotron-3.5-Bolt-74B-A9B with the model card's recipe, through deploy/vllm/start-bolt.sh and its
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
#     REASONING_PARSER=nano_v3 EXTRA_ARGS="--kv-cache-dtype fp8" \
#     VLLM_ENV="VLLM_USE_FLASHINFER_MOE_FP4=1 VLLM_FLASHINFER_MOE_BACKEND=throughput" scripts/serve-vllm.sh
#     (Thor, from the model card: without the FlashInfer MoE env the NVFP4 MoE fails "Non-gated activations are only
#     supported by the flashinfer CUTLASS backend". The first start JIT-compiles FlashInfer for sm_110, 30+ min: use
#     READY_TIMEOUT=3600; the kernels land in COMPILE_CACHE and later starts reuse them.)
#   GPU_MEMORY_UTILIZATION=0.30 VLLM_PROFILE=generic MODEL_ID=nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 \
#     REASONING_PARSER=nemotron_v3 EXTRA_ARGS="--moe-backend marlin --kv-cache-dtype fp8 --mamba-cache-mode align" \
#     scripts/serve-vllm.sh
#     (the Tier-2 used on Thor from 2026-10-21; default image. Marlin MoE kernels are prebuilt: about 100 s to ready)
#   Bolt-74B-A9B-24GB does not run on Thor (2026-10-21): its block-quantized dense weights need b12x, built for SM12x
#   (GB10) only, and the NGC 26.01 image's transformers is too old for its remote code.
# Environment (both profiles): MODEL_ID, PORT (8000), HOST (0.0.0.0), NAME (vllm-edge), SERVED_NAME (edge-agent),
#   GPU_MEMORY_UTILIZATION (0.45), MAX_MODEL_LEN (32768), MAX_NUM_SEQS (2), HF_HOME, HF_HUB_OFFLINE (1),
#   GPU_FLAGS (--runtime nvidia on a Jetson, --gpus all elsewhere). DRY_RUN=1 (generic): print the docker command only.
#   READY_TIMEOUT (900 s). MIN_AVAIL_GB (12, Jetson only): while waiting, remove the container if MemAvailable drops
#   below it; with no swap, running out freezes the box and only a power cycle brings it back.
# generic only: IMAGE, TOOL_PARSER (qwen3_coder), REASONING_PARSER (none), EXTRA_ARGS, ENTRY ("vllm serve" unless the
#   image's own entrypoint is the server, as in vllm/vllm-openai and blackbox-vllm-bolt).
#   VLLM_ENV: space-separated NAME=VALUE pairs passed into the container. COMPILE_CACHE ($HOME/.cache): its flashinfer/
#   and vllm-compile/ are mounted so JIT kernels and torch.compile output survive the container. MAX_JOBS (4 on a
#   Jetson): parallel kernel compiles; unbounded, 49 nvcc jobs took MemAvailable from 78 to 25 GB on Thor.
# The box has no swap and one inference server takes about half of it: this refuses to start beside trtllm-edge or a
# second vLLM server on the same port.
set -euo pipefail
cd "$(dirname "$0")/.."
PROFILE="${VLLM_PROFILE:-lightning}"
# lightning (default since 2026-10-22, the team's model decision): the generic profile with the Lightning recipe the
# Thor runs. Every value stays overridable; `VLLM_PROFILE=bolt` still selects the Bolt recipe on a GB10.
if [ "$PROFILE" = lightning ]; then
  export MODEL_ID="${MODEL_ID:-nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4}" \
         REASONING_PARSER="${REASONING_PARSER:-nemotron_v3}" \
         EXTRA_ARGS="${EXTRA_ARGS:---moe-backend marlin --kv-cache-dtype fp8 --mamba-cache-mode align}" \
         GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.30}"
  PROFILE=generic
fi
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
  local i avail limit=$(( ${READY_TIMEOUT:-900} / 5 ))
  for i in $(seq 1 "$limit"); do
    if health && curl -s "http://127.0.0.1:$PORT/v1/models" | grep -q "\"$SERVED_NAME\""; then
      echo "ready: $SERVED_NAME on :$PORT ($PROFILE)"; return 0
    fi
    docker ps --format '{{.Names}}' | grep -qx "$NAME" || { echo "container $NAME exited; docker logs $NAME" >&2; return 1; }
    if [ -f /etc/nv_tegra_release ]; then
      avail=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
      if [ "$avail" -lt "${MIN_AVAIL_GB:-12}" ]; then
        docker rm -f "$NAME" >/dev/null 2>&1
        echo "MemAvailable ${avail} GB < ${MIN_AVAIL_GB:-12} GB: removed $NAME before the box runs out" >&2; return 1
      fi
    fi
    sleep 5
  done
  echo "not ready after ${READY_TIMEOUT:-900} s; docker logs $NAME" >&2; return 1
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
    CC="${COMPILE_CACHE:-$HOME/.cache}"
    mkdir -p "$CC/flashinfer" "$CC/vllm-compile" "$CC/flashinfer-gen"
    # FlashInfer writes the MoE CUTLASS sources it compiles into its own package directory. Created fresh in each new
    # container they are newer than every cached object, and ninja rebuilt all 253 (90 min on Thor); kept on the host,
    # they are rewritten only when their content changes.
    FI_GEN="${FLASHINFER_GEN_DIR:-/usr/local/lib/python3.12/dist-packages/flashinfer/data/csrc/nv_internal/tensorrt_llm/cutlass_instantiations}"
    MOUNTS=(-v "${HF_HOME:-$HOME/.cache/huggingface}:/root/.cache/huggingface"
            -v "$CC/flashinfer:/root/.cache/flashinfer" -v "$CC/vllm-compile:/root/.cache/vllm"
            -v "$CC/flashinfer-gen:$FI_GEN")
    ENVS=(-e HF_HUB_OFFLINE -e HF_HOME=/root/.cache/huggingface)
    JOBS="${MAX_JOBS:-$([ -f /etc/nv_tegra_release ] && echo 4 || true)}"   # the failed test must not trip set -e: on a non-Jetson box the script exited here silently (2026-10-22)
    [ -n "$JOBS" ] && ENVS+=(-e "MAX_JOBS=$JOBS" -e FLASHINFER_NVCC_THREADS=1)
    for kv in ${VLLM_ENV:-}; do ENVS+=(-e "$kv"); done
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
      echo docker run -d --name "$NAME" $GPU_FLAGS --ipc host --network host --restart no "${ENVS[@]}" \
        "${MOUNTS[@]}" "$IMAGE" $ENTRY "$MODEL_ID" "${ARGS[@]}"; exit 0
    fi
    mkdir -p logs
    # shellcheck disable=SC2086
    printf '%s\n' "$IMAGE" $ENTRY "$MODEL_ID" "${ARGS[@]}" > logs/vllm-edge-args.txt
    # shellcheck disable=SC2086
    docker run -d --name "$NAME" $GPU_FLAGS --ipc host --network host --restart no "${ENVS[@]}" "${MOUNTS[@]}" \
      "$IMAGE" $ENTRY "$MODEL_ID" "${ARGS[@]}" >/dev/null
    echo "started $NAME: $IMAGE $MODEL_ID (args in logs/vllm-edge-args.txt)"
    wait_ready ;;
  *) echo "VLLM_PROFILE must be lightning, bolt or generic, not '$PROFILE'" >&2; exit 2 ;;
esac
