from __future__ import annotations

import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
WHEELS = sorted((SKILL_ROOT / "vendor").glob("blackout_ops_kb-*.whl"))
DATABASE = SKILL_ROOT / "data" / "blackout_ops_kb.sqlite"

if len(WHEELS) != 1:
    raise SystemExit(f"expected one vendored blackout_ops_kb wheel; found {len(WHEELS)}")
if not DATABASE.is_file():
    raise SystemExit(f"missing local RAG database: {DATABASE}")

sys.path.insert(0, str(WHEELS[0]))

from blackout_ops_kb.cli import main  # noqa: E402


def run() -> int:
    argv = sys.argv[1:]
    if not argv:
        argv = ["doctor"]
    if argv[0] in {"search", "context", "stats", "doctor", "export-rag"} and "--db" not in argv:
        argv[1:1] = ["--db", str(DATABASE)]
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(run())
