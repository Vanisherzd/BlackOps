from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from blackbox.inference import InferenceGateway
from blackbox.inference.schemas import JsonObject, JsonValue, ReasonResult

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class BenchmarkArgs:
    base_url: str | None
    model: str | None
    out: Path | None
    reasoning_effort: str


def parse_args(description: str, argv: Sequence[str] | None = None) -> BenchmarkArgs:
    """Parse the common smoke benchmark command-line options."""

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default="low",
    )
    args = parser.parse_args(argv)
    return BenchmarkArgs(
        base_url=args.base_url,
        model=args.model,
        out=args.out,
        reasoning_effort=args.reasoning_effort,
    )


def environment(gateway: InferenceGateway | None = None) -> JsonObject:
    """Capture reproducibility metadata measured from the current machine."""

    active_gateway = gateway or InferenceGateway()
    values: JsonObject = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "models": active_gateway.models(),
        "blackbox_inference_env": {
            key: _environment_value(key, value)
            for key, value in sorted(os.environ.items())
            if key.startswith("BLACKBOX_INFERENCE_")
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
    git_sha = _command_output(("git", "rev-parse", "--short", "HEAD"))
    if git_sha:
        values["git_sha"] = git_sha
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is not None:
        values["nvidia_smi"] = _command_output(
            (
                nvidia_smi,
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            )
        )
    return values


def write_artifact(
    path: str | Path,
    payload: JsonObject,
    gateway: InferenceGateway | None = None,
) -> None:
    """Write a local measurement artifact only when explicitly requested."""

    document = dict(payload)
    document["measurement"] = "LOCAL_MEASUREMENT"
    document["environment"] = environment(gateway)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as artifact:
        json.dump(document, artifact, indent=2, sort_keys=True)
        artifact.write("\n")


def summarize(results: Sequence[JsonObject]) -> None:
    """Print one result per line and fail the process if any check failed."""

    failed = False
    for result in results:
        name = result.get("name")
        passed = result.get("passed") is True
        failed = failed or not passed
        label = name if isinstance(name, str) else "unnamed"
        print(f"{'PASS' if passed else 'FAIL'} {label}")
    if failed:
        raise SystemExit(1)


def usage(result: ReasonResult) -> JsonObject:
    """Serialize usage and expose a reasoning/final split when supplied."""

    values: JsonObject = {
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
        "total_tokens": result.usage.total_tokens,
    }
    raw_usage = result.raw.get("usage")
    if not isinstance(raw_usage, dict):
        return values
    details = raw_usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return values
    reasoning_tokens = details.get("reasoning_tokens")
    if isinstance(reasoning_tokens, int) and not isinstance(reasoning_tokens, bool):
        values["reasoning_tokens"] = reasoning_tokens
        values["final_tokens"] = max(
            0, result.usage.completion_tokens - reasoning_tokens
        )
    return values


def _environment_value(name: str, value: str) -> str:
    sensitive = ("KEY", "TOKEN", "SECRET", "PASSWORD")
    return "<redacted>" if any(marker in name for marker in sensitive) else value


def _command_output(command: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            cwd=REPO_ROOT,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip()
