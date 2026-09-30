#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${BLACKOUT_KB_VENV:-${PROJECT_ROOT}/.venv}/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Missing ${PYTHON}; run bash ${PROJECT_ROOT}/linux/install.sh first." >&2
  exit 2
fi

"${PYTHON}" -m blackout_ops_kb doctor
"${PYTHON}" -m blackout_ops_kb context \
  --incident "${PROJECT_ROOT}/examples/incident_linux_systemd.json" \
  | "${PYTHON}" -c 'import json,sys; r=json.load(sys.stdin); assert r["knowledge_match"]["status"] == "matched"; assert r["solutions"]; print("Linux RAG context: PASS")'
