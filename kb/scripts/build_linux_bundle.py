from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = PACKAGE_ROOT.parent
SRC = PACKAGE_ROOT / "src"
ARTIFACTS = PACKAGE_ROOT / "artifacts"
DIST = PACKAGE_ROOT / "dist"
DATA = SRC / "blackout_ops_kb" / "data"
SKILL = PACKAGE_ROOT / "integrations" / "nemoclaw" / "blackout-ops-rag"


def version() -> str:
    text = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise RuntimeError("version missing from pyproject.toml")
    return match.group(1)


def run(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, cwd=WORKSPACE, env=env, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    if info.isdir():
        info.mode = 0o755
    elif info.name.endswith(("/install.sh", "/verify.sh", "/blackout-kb", "/install_skill.sh")):
        info.mode = 0o755
    elif info.isfile():
        info.mode = 0o644
    return info


def main() -> int:
    package_version = version()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)

    run(
        sys.executable,
        "-m",
        "blackout_ops_kb",
        "build",
        "--datasets-root",
        str(WORKSPACE / "datasets" / "raw"),
        "--out-dir",
        str(ARTIFACTS),
        env=env,
    )

    DATA.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ARTIFACTS / "blackout_ops_kb.sqlite", DATA / "blackout_ops_kb.sqlite")
    shutil.copy2(ARTIFACTS / "rag_documents.jsonl", DATA / "rag_documents.jsonl")

    run(
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(PACKAGE_ROOT / "tests"),
        "-v",
        env=env,
    )
    run(
        sys.executable,
        str(PACKAGE_ROOT / "scripts" / "validate_datasets.py"),
        "--datasets-root",
        str(WORKSPACE / "datasets" / "raw"),
        "--artifacts",
        str(ARTIFACTS),
        "--out",
        str(ARTIFACTS / "dataset_validation.json"),
        env=env,
    )

    DIST.mkdir(parents=True, exist_ok=True)
    for old_wheel in DIST.glob("blackout_ops_kb-*.whl"):
        old_wheel.unlink()
    run(sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(DIST), str(PACKAGE_ROOT))
    wheel = DIST / f"blackout_ops_kb-{package_version}-py3-none-any.whl"
    if not wheel.is_file():
        raise RuntimeError(f"wheel missing: {wheel}")

    previous_manifest_path = ARTIFACTS / "manifest.json"
    previous_manifest = (
        json.loads(previous_manifest_path.read_text(encoding="utf-8")) if previous_manifest_path.exists() else {}
    )
    rag_manifest = json.loads((ARTIFACTS / "rag_manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((ARTIFACTS / "dataset_validation.json").read_text(encoding="utf-8"))
    manifest_files = [
        "blackout_ops_kb.sqlite",
        "rag_documents.jsonl",
        "rag_manifest.json",
        "cases_train.jsonl",
        "cases_dev.jsonl",
        "cases_eval.jsonl",
        "eval_ground_truth.jsonl",
        "solutions.json",
        "topologies.jsonl",
        "dataset_validation.json",
    ]
    manifest = {
        "package": "blackout-ops-kb",
        "version": package_version,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "os": ["Linux", "Jetson Linux", "DGX OS", "Windows development only"],
            "architectures": ["aarch64", "x86_64"],
            "python": ">=3.10",
            "cuda_required": False,
            "network_required": False,
            "raw_datasets_required": False,
        },
        "rag": rag_manifest,
        "validation": validation,
        "source_revisions": previous_manifest.get("source_revisions", {}),
        "sha256": {name: sha256(ARTIFACTS / name) for name in manifest_files},
        "wheel": {"name": wheel.name, "sha256": sha256(wheel)},
    }
    previous_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    vendor = SKILL / "vendor"
    skill_data = SKILL / "data"
    vendor.mkdir(parents=True, exist_ok=True)
    skill_data.mkdir(parents=True, exist_ok=True)
    for old_wheel in vendor.glob("blackout_ops_kb-*.whl"):
        old_wheel.unlink()
    shutil.copy2(wheel, vendor / wheel.name)
    shutil.copy2(ARTIFACTS / "blackout_ops_kb.sqlite", skill_data / "blackout_ops_kb.sqlite")

    archive = DIST / f"blackout-ops-linux-rag-{package_version}.tar.gz"
    if archive.exists():
        archive.unlink()
    prefix = f"blackout-ops-linux-rag-{package_version}"
    paths = [
        PACKAGE_ROOT / "README.md",
        PACKAGE_ROOT / "LINUX_DEPLOY.md",
        PACKAGE_ROOT / "Dockerfile",
        PACKAGE_ROOT / "linux",
        PACKAGE_ROOT / "docs" / "rag_schema.md",
        PACKAGE_ROOT / "docs" / "linux_rag_completion_audit.md",
        *sorted((PACKAGE_ROOT / "examples").glob("incident_*.json")),
        PACKAGE_ROOT / "integrations" / "nemoclaw",
        wheel,
        ARTIFACTS / "blackout_ops_kb.sqlite",
        ARTIFACTS / "rag_documents.jsonl",
        ARTIFACTS / "rag_manifest.json",
        ARTIFACTS / "manifest.json",
    ]
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as stream:
        for path in paths:
            relative = path.relative_to(PACKAGE_ROOT)
            stream.add(path, arcname=str(Path(prefix) / relative), filter=tar_filter)

    report = {
        "version": package_version,
        "wheel": wheel.name,
        "wheel_sha256": sha256(wheel),
        "linux_bundle": archive.name,
        "linux_bundle_sha256": sha256(archive),
        "database_sha256": sha256(ARTIFACTS / "blackout_ops_kb.sqlite"),
        "rag_sha256": sha256(ARTIFACTS / "rag_documents.jsonl"),
    }
    (DIST / "linux_bundle_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
