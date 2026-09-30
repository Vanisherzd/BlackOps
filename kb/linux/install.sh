#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${BLACKOUT_KB_VENV:-${PROJECT_ROOT}/.venv}"

"${PYTHON_BIN}" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'

mapfile -t WHEELS < <(find "${PROJECT_ROOT}/dist" -maxdepth 1 -type f -name 'blackout_ops_kb-*.whl' -print)
if [[ ${#WHEELS[@]} -ne 1 ]]; then
  echo "Expected exactly one blackout_ops_kb wheel under ${PROJECT_ROOT}/dist; found ${#WHEELS[@]}" >&2
  exit 2
fi

if ! "${PYTHON_BIN}" -m venv "${VENV_DIR}"; then
  echo "venv creation failed. Install python3-venv for this Python version, then retry." >&2
  exit 3
fi

"${VENV_DIR}/bin/python" -m pip install --no-index --no-deps --force-reinstall "${WHEELS[0]}"
"${VENV_DIR}/bin/python" -m blackout_ops_kb doctor

echo "Installed. Run: ${PROJECT_ROOT}/linux/blackout-kb doctor"
