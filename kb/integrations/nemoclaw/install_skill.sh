#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <nemo-deepagents-sandbox-name>" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
nemo-deepagents "$1" skill install "${SCRIPT_DIR}/blackout-ops-rag"
nemo-deepagents "$1" skill list
