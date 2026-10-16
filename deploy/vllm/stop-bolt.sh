#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/bolt.env.defaults"

command -v docker >/dev/null 2>&1 || {
  echo "error: docker is required to stop $BOLT_CONTAINER" >&2
  exit 1
}

docker stop "$BOLT_CONTAINER"
docker rm "$BOLT_CONTAINER"
