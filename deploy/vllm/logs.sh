#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/bolt.env.defaults"

command -v docker >/dev/null 2>&1 || {
  echo "error: docker is required to read $BOLT_CONTAINER logs" >&2
  exit 1
}

case "$#" in
  0) exec docker logs -f "$BOLT_CONTAINER" ;;
  2)
    [ "$1" = "--tail" ] || {
      echo "usage: $0 [--tail N]" >&2
      exit 2
    }
    case "$2" in
      ''|*[!0-9]*) echo "error: --tail requires a non-negative integer" >&2; exit 2 ;;
    esac
    exec docker logs --tail "$2" "$BOLT_CONTAINER"
    ;;
  *) echo "usage: $0 [--tail N]" >&2; exit 2 ;;
esac
