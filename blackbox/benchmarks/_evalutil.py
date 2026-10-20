"""Shared benchmark mechanics that do not belong in the smoke-test helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Final

from openai import OpenAIError

from blackbox.benchmarks.common import REPO_ROOT
from blackbox.inference import InferenceGateway
from blackbox.inference.client import InferenceClient
from blackbox.inference.schemas import JsonObject, JsonValue

_READY_TEXT: Final = re.compile(
    r"server is ready|application startup complete", re.IGNORECASE
)
_ISO_TIMESTAMP: Final = re.compile(
    r"(?P<stamp>\d{4}-\d{2}-\d{2}[T ][0-9:.]+(?:Z|[+-][0-9:]+)?)"
)
_SHORT_TIMESTAMP: Final = re.compile(r"(?P<stamp>\d{2}-\d{2} [0-9:.]+)")


@dataclass(frozen=True, slots=True)
class TimedChunk:
    """Preserve arrival time because aggregate responses cannot recover TTFT or ITL."""

    timestamp_s: float
    payload: JsonObject
    streaming: bool


@dataclass(frozen=True, slots=True)
class HardwareSample:
    """Keep one multi-GPU sample together so peak memory is not understated."""

    memory_mib: float
    power_w: float | None


class StreamingClientRequiredError(RuntimeError):
    """Explain why an injected test client cannot run the SDK streaming benchmark."""


def stream_chat(
    gateway: InferenceGateway,
    messages: Sequence[JsonObject],
    max_tokens: int,
) -> Iterator[TimedChunk]:
    """Yield timestamped SDK chunks, falling back when an endpoint sends no SSE events."""

    client = gateway._client
    if not isinstance(client, InferenceClient):
        raise StreamingClientRequiredError(
            "performance streaming requires the gateway's InferenceClient"
        )
    body = {
        "model": gateway._model,
        "messages": list(messages),
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    emitted = False
    try:
        response = client._sdk.chat.completions.create(
            **body,
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in response:
            emitted = True
            yield TimedChunk(time.perf_counter(), chunk.model_dump(), True)
    except OpenAIError:
        if emitted:
            raise
    if emitted:
        return

    payload = client.chat_completions(**body)
    yield TimedChunk(time.perf_counter(), payload, False)


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Interpolate small samples so test runs with two requests still have a p90."""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def distribution(values: Sequence[float]) -> JsonObject:
    """Use one stable aggregate shape for every per-request performance measure."""

    if not values:
        return {"median": None, "p90": None}
    return {
        "median": round(median(values), 3),
        "p90": round(percentile(values, 0.9) or 0.0, 3),
    }


def command_output(command: Sequence[str]) -> str | None:
    """Treat absent local system tools as metadata gaps rather than benchmark failures."""

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
        return None
    output = completed.stdout.strip()
    return output or None


def sample_gpu() -> HardwareSample | None:
    """Sum per-GPU memory because a tensor-parallel request occupies the full device set."""

    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    output = command_output(
        (
            executable,
            "--query-gpu=memory.used,power.draw",
            "--format=csv,noheader,nounits",
        )
    )
    if output is None:
        return None
    memory = 0.0
    power = 0.0
    has_power = False
    try:
        for line in output.splitlines():
            memory_text, power_text = (part.strip() for part in line.split(",", 1))
            memory += float(memory_text)
            if power_text not in {"[N/A]", "N/A"}:
                power += float(power_text)
                has_power = True
    except ValueError:
        return None
    return HardwareSample(memory, power if has_power else None)


def parse_startup_time(path: Path | None) -> float | None:
    """Compute startup only from timestamps present in logs, never from file metadata."""

    if path is None or not path.is_file():
        return None
    first: datetime | None = None
    ready: datetime | None = None
    with path.open(encoding="utf-8", errors="replace") as log:
        for index, line in enumerate(log):
            timestamp = _line_timestamp(line)
            if index == 0:
                first = timestamp
            if _READY_TEXT.search(line) and timestamp is not None:
                ready = timestamp
                break
    if first is None or ready is None:
        return None
    return max(0.0, round((ready - first).total_seconds(), 3))


def system_metadata(gateway: InferenceGateway) -> JsonObject:
    """Capture only locally observable deployment identity and hardware metadata."""

    nvidia = command_output(("nvidia-smi",)) if shutil.which("nvidia-smi") else None
    gpu_line = (
        command_output(
            (
                "nvidia-smi",
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            )
        )
        if shutil.which("nvidia-smi")
        else None
    )
    mem_total_kib: int | None = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        match = re.search(
            r"^MemTotal:\s+(\d+)\s+kB$", meminfo.read_text(), re.MULTILINE
        )
        if match:
            mem_total_kib = int(match.group(1))
    model_revision = _model_revision(gateway)
    args_path = REPO_ROOT / "logs" / "bolt-args.txt"
    argv = (
        args_path.read_text(encoding="utf-8").splitlines()
        if args_path.is_file()
        else None
    )
    cuda_match = re.search(r"CUDA Version:\s*([^ |]+)", nvidia or "")
    return {
        "gpu_name_driver": gpu_line,
        "cuda_version": cuda_match.group(1) if cuda_match else None,
        "ram_memtotal_kib": mem_total_kib,
        "container_image_id": command_output(
            ("docker", "inspect", "--format={{.Image}}", "vllm-bolt")
        )
        if shutil.which("docker")
        else None,
        "vllm_argv": argv,
        "model_revision": model_revision,
        "git_sha": command_output(("git", "rev-parse", "HEAD")),
    }


def _line_timestamp(line: str) -> datetime | None:
    match = _ISO_TIMESTAMP.search(line)
    if match is not None:
        value = match.group("stamp").replace("Z", "+00:00")
    else:
        short = _SHORT_TIMESTAMP.search(line)
        value = f"2000-{short.group('stamp')}" if short is not None else ""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _model_revision(gateway: InferenceGateway) -> JsonValue:
    result = gateway._client.get("/v1/models")
    if result.status != 200 or not isinstance(result.body, dict):
        return None
    entries = result.body.get("data")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") != gateway._model:
            continue
        for key in ("revision", "model_revision"):
            value = entry.get(key)
            if isinstance(value, str):
                return value
        metadata = entry.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get("revision") or metadata.get("model_revision")
            if isinstance(value, str):
                return value
    return None
