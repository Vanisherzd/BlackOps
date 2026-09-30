#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ARCHIVE="${PACKAGE_ROOT}/dist/blackout-ops-linux-rag-0.3.0.tar.gz"
TMP_DIR="$(mktemp -d)"

cleanup() {
  if [[ -n "${TMP_DIR:-}" && "${TMP_DIR}" == /tmp/* && -d "${TMP_DIR}" ]]; then
    rm -rf -- "${TMP_DIR}"
  fi
}
trap cleanup EXIT

tar -xzf "${ARCHIVE}" -C "${TMP_DIR}"
DEPLOY_ROOT="${TMP_DIR}/blackout-ops-linux-rag-0.3.0"
cd "${DEPLOY_ROOT}"

bash linux/install.sh
bash linux/verify.sh

cd /tmp
"${DEPLOY_ROOT}/.venv/bin/python" - <<'PY'
import json
import platform
import subprocess
import sys

assert sys.platform == "linux", sys.platform
result = json.loads(
    subprocess.check_output(
        [sys.executable, "-m", "blackout_ops_kb", "doctor"],
        text=True,
    )
)
assert result["status"] == "pass", result
assert result["rag_available"] is True, result
assert result["stats"]["documents"] == 1668, result
print(f"Installed wheel on Linux {platform.machine()}: PASS")
PY

python3 "${DEPLOY_ROOT}/integrations/nemoclaw/blackout-ops-rag/scripts/query_kb.py" \
  context --incident "${DEPLOY_ROOT}/examples/incident_linux_systemd.json" \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["knowledge_match"]["status"] == "matched"; assert r["solutions"][0]["id"] == "solution-linux-systemd-restart-loop"; print("Self-contained NemoClaw skill: PASS")'

printf '%s\n' '{"platform":"linux","log":"NVIDIA Xid 79 GPU has fallen off the bus"}' \
  | "${DEPLOY_ROOT}/linux/blackout-kb" context --incident - \
  | "${DEPLOY_ROOT}/.venv/bin/python" -c 'import json,sys; r=json.load(sys.stdin); assert r["knowledge_match"]["status"] == "insufficient"; assert r["solutions"] == []; print("Unknown-error refusal: PASS")'

echo "Linux bundle validation: PASS"
