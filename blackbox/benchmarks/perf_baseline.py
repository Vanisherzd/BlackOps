"""Local-only streaming performance baseline for the configured inference gateway."""

from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path
from statistics import fmean, median
from typing import Final

from blackbox.benchmarks._evalutil import (
    distribution,
    parse_startup_time,
    sample_gpu,
    stream_chat,
    system_metadata,
)
from blackbox.benchmarks.common import environment, summarize, write_artifact
from blackbox.inference import InferenceGateway
from blackbox.inference.schemas import JsonObject, JsonValue

_PROMPT_PARAGRAPH: Final = (
    "A deterministic edge incident report records the service state, container "
    "status, recent evidence, action taken, and verification result. "
)


def run(gateway: InferenceGateway, **opts: JsonValue | Path) -> JsonObject:
    """Measure request timing together so concurrency and sampling share one window."""

    request_count = _positive_int(opts.get("requests"), 6)
    concurrency = _positive_int(opts.get("concurrency"), 1)
    prompt_target = _positive_int(opts.get("prompt_tokens_target"), 2000)
    max_tokens = _positive_int(opts.get("max_tokens"), 256)
    startup_value = opts.get("startup_log")
    startup_log = (
        startup_value
        if isinstance(startup_value, Path)
        else Path(startup_value)
        if isinstance(startup_value, str)
        else None
    )
    prompt = _prompt(prompt_target)

    stop_sampling = threading.Event()
    samples: list[tuple[float, float | None]] = []

    def collect_samples() -> None:
        while not stop_sampling.is_set():
            sample = sample_gpu()
            if sample is not None:
                samples.append((sample.memory_mib, sample.power_w))
            stop_sampling.wait(1.0)

    sampler = threading.Thread(target=collect_samples, daemon=True)
    sampler.start()
    run_started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(_request, gateway, index, prompt, max_tokens)
                for index in range(request_count)
            ]
            requests = [future.result() for future in futures]
    finally:
        stop_sampling.set()
        sampler.join(timeout=2)
    run_wall_ms = round((time.perf_counter() - run_started) * 1000, 3)

    memory_values = [memory for memory, _ in samples]
    power_values = [power for _, power in samples if power is not None]
    return {
        "benchmark": "perf_baseline",
        "measurement": "LOCAL_MEASUREMENT",
        "streaming": all(request.get("streaming") is True for request in requests),
        "config": {
            "concurrency": concurrency,
            "requests": request_count,
            "prompt_tokens_target": prompt_target,
            "max_tokens": max_tokens,
        },
        "requests": requests,
        "aggregate": _aggregate(requests, run_wall_ms),
        "peak_gpu_memory_mib": max(memory_values) if memory_values else None,
        "peak_power_w": max(power_values) if power_values else None,
        "startup_time_s": parse_startup_time(startup_log),
        "system": system_metadata(gateway),
    }


def _request(
    gateway: InferenceGateway,
    index: int,
    prompt: str,
    max_tokens: int,
) -> JsonObject:
    started = time.perf_counter()
    chunks = list(
        stream_chat(
            gateway,
            [
                {
                    "role": "system",
                    "content": "Summarize the supplied report in one concise paragraph.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens,
        )
    )
    ended = time.perf_counter()
    streaming = bool(chunks) and all(chunk.streaming for chunk in chunks)
    token_times = (
        [chunk.timestamp_s for chunk in chunks if _token_text(chunk.payload)]
        if streaming
        else []
    )
    usage = (
        _usage(chunks[-1].payload)
        if chunks
        else {"prompt_tokens": 0, "completion_tokens": 0}
    )
    prompt_tokens = _integer(usage.get("prompt_tokens"))
    completion_tokens = _integer(usage.get("completion_tokens"))
    ttft_ms = round((token_times[0] - started) * 1000, 3) if token_times else None
    gaps = [(current - previous) * 1000 for previous, current in pairwise(token_times)]
    if len(token_times) > 1:
        generation_s = max(token_times[-1] - token_times[0], 0.000001)
    elif token_times:
        generation_s = max(ended - token_times[0], 0.000001)
    else:
        generation_s = max(ended - started, 0.000001)
    output_rate = completion_tokens / generation_s
    content = "".join(_token_text(chunk.payload) for chunk in chunks)
    if not streaming and chunks:
        content = _message_content(chunks[-1].payload)
    return {
        "request": index,
        "streaming": streaming,
        "ttft_ms": ttft_ms,
        "itl_mean_ms": round(fmean(gaps), 3) if gaps else None,
        "itl_p50_ms": round(median(gaps), 3) if gaps else None,
        "output_tokens_per_second": round(output_rate, 3),
        "wall_time_ms": round((ended - started) * 1000, 3),
        "generation_time_ms": round(generation_s * 1000, 3),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "content_length": len(content),
        "passed": bool(chunks),
    }


def _aggregate(requests: list[JsonObject], run_wall_ms: float) -> JsonObject:
    """Aggregate nullable streaming metrics without inventing values for fallback runs."""

    return {
        "request_count": len(requests),
        "successful_requests": sum(
            request.get("passed") is True for request in requests
        ),
        "run_wall_time_ms": run_wall_ms,
        "ttft_ms": distribution(_numbers(requests, "ttft_ms")),
        "itl_mean_ms": distribution(_numbers(requests, "itl_mean_ms")),
        "itl_p50_ms": distribution(_numbers(requests, "itl_p50_ms")),
        "output_tokens_per_second": distribution(
            _numbers(requests, "output_tokens_per_second")
        ),
        "wall_time_ms": distribution(_numbers(requests, "wall_time_ms")),
        "prompt_tokens": distribution(_numbers(requests, "prompt_tokens")),
        "completion_tokens": distribution(_numbers(requests, "completion_tokens")),
    }


def _prompt(target_tokens: int) -> str:
    target_chars = target_tokens * 4
    repetitions = max(
        1, (target_chars + len(_PROMPT_PARAGRAPH) - 1) // len(_PROMPT_PARAGRAPH)
    )
    report = (_PROMPT_PARAGRAPH * repetitions)[:target_chars]
    return f"Summarize this deterministic report:\n{report}"


def _token_text(payload: JsonObject) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    if isinstance(content, str) and content:
        return content
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    return reasoning if isinstance(reasoning, str) else ""


def _message_content(payload: JsonObject) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _usage(payload: JsonObject) -> JsonObject:
    usage = payload.get("usage")
    return usage if isinstance(usage, dict) else {}


def _integer(value: JsonValue) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _numbers(rows: list[JsonObject], key: str) -> list[float]:
    return [
        float(value)
        for row in rows
        if isinstance((value := row.get(key)), int | float)
        and not isinstance(value, bool)
    ]


def _positive_int(value: JsonValue | Path | None, default: int) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else default
    )


def main() -> int:
    """Write one self-describing local artifact for the requested concurrency."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--requests", type=int, default=6)
    parser.add_argument("--prompt-tokens-target", type=int, default=2000)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--startup-log", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/bolt_perf_c1.json"))
    args = parser.parse_args()
    gateway = InferenceGateway(base_url=args.base_url, model=args.model)
    payload = run(
        gateway,
        concurrency=args.concurrency,
        requests=args.requests,
        prompt_tokens_target=args.prompt_tokens_target,
        max_tokens=args.max_tokens,
        startup_log=args.startup_log,
    )
    payload["environment"] = environment(gateway)
    write_artifact(args.out, payload, gateway)
    requests = payload.get("requests")
    rows = (
        [row for row in requests if isinstance(row, dict)]
        if isinstance(requests, list)
        else []
    )
    summarize(
        [
            {
                "name": "perf_baseline",
                "passed": bool(rows) and all(row.get("passed") is True for row in rows),
            }
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
