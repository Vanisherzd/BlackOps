#!/usr/bin/env bash
# Rerank sidecar: nvidia/llama-nemotron-rerank-1b-v2 on vLLM (the model card's serving route), separate container,
# own port, a sliver of GPU memory. Never part of stage-up: an experiment behind RERANK_URL (scripts/kb-rerank-eval.py)
# until README.md says it earns a place. No restart policy, like every other container here.
#
#   scripts/serve-rerank.sh            start (idempotent: reuses a healthy container) and wait for /health
#   scripts/serve-rerank.sh stop       remove the container
#   RERANK_PORT=8020 GPU_MEMORY_UTILIZATION=0.04 IMAGE=vllm/vllm-openai:nightly-...   knobs
#
# Check: curl -s localhost:8020/rerank -H 'Content-Type: application/json' \
#          -d '{"model":"rerank","query":"disk full","documents":["No space left on device","Bananas are yellow"]}'
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=vllm-rerank
MODEL_ID="${RERANK_MODEL_ID:-nvidia/llama-nemotron-rerank-1b-v2}"
PORT="${RERANK_PORT:-8020}"
IMAGE="${IMAGE:-vllm/vllm-openai:nightly-77154ca6116ae34cbfe314beef1016bde9b63989}"
GPU_FLAGS="${GPU_FLAGS:-$([ -f /etc/nv_tegra_release ] && echo "--runtime nvidia" || echo "--gpus all")}"
HF="${HF_HOME:-$HOME/.cache/huggingface}"
TPL="$PWD/logs/nemotron-rerank.jinja"

if [ "${1:-}" = "stop" ]; then docker rm -f "$NAME" >/dev/null 2>&1 && echo "stopped $NAME" || echo "$NAME not running"; exit 0; fi
if curl -sf -m 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then echo "$NAME already healthy on :$PORT"; exit 0; fi
docker rm -f "$NAME" >/dev/null 2>&1 || true
mkdir -p logs
# The model card's score template: the pair must be rendered as "question:... \n \n passage:..." or scores are wrong.
printf '%s' 'question:{{ (messages | selectattr("role", "eq", "query") | first).content }} '$'\n'' '$'\n'' passage:{{ (messages | selectattr("role", "eq", "document") | first).content }}' > "$TPL"
docker run -d --name "$NAME" $GPU_FLAGS --ipc host --network host --restart no \
  -e HF_HUB_OFFLINE=1 -e HF_HOME=/root/.cache/huggingface \
  -v "$HF:/root/.cache/huggingface" -v "$TPL:/opt/rerank/nemotron-rerank.jinja:ro" \
  "$IMAGE" "$MODEL_ID" --served-model-name rerank --host 127.0.0.1 --port "$PORT" \
  --trust-remote-code --chat-template /opt/rerank/nemotron-rerank.jinja \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.04}" --max-model-len "${MAX_MODEL_LEN:-1024}" \
  --max-num-seqs "${MAX_NUM_SEQS:-64}" >/dev/null
echo "started $NAME ($MODEL_ID) on :$PORT"
for i in $(seq 1 "${READY_TIMEOUT:-300}"); do
  if curl -sf -m 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then echo "healthy after ${i}s"; exit 0; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != "true" ]; then
    echo "$NAME exited; last log lines:" >&2; docker logs --tail 30 "$NAME" >&2; exit 1
  fi
  sleep 1
done
echo "not healthy after ${READY_TIMEOUT:-300}s; docker logs $NAME" >&2; exit 1
