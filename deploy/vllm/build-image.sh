#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd)
. "$SCRIPT_DIR/bolt.env.defaults"

command -v docker >/dev/null 2>&1 || {
  echo "error: docker is required to build $BOLT_IMAGE" >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "error: cannot reach the Docker daemon; start Docker and retry" >&2
  exit 1
}

cd "$REPO_ROOT"
docker build \
  --build-arg "BOLT_IMAGE_TAG=$BOLT_IMAGE_TAG" \
  -t "$BOLT_IMAGE" \
  deploy/vllm

IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$BOLT_IMAGE")
echo "built $BOLT_IMAGE ($IMAGE_ID)"
