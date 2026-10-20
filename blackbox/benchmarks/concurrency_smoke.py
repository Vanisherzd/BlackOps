from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from statistics import fmean

from blackbox.benchmarks.common import parse_args, summarize, usage, write_artifact
from blackbox.inference import InferenceGateway
from blackbox.inference.schemas import JsonObject, JsonValue


def run(gateway: InferenceGateway, **opts: JsonValue) -> JsonObject:
    """Measure N sequential requests and N requests across two threads."""

    count_value = opts.get("n", 4)
    count = (
        count_value
        if isinstance(count_value, int) and not isinstance(count_value, bool)
        else 4
    )
    count = max(1, count)
    effort_value = opts.get("reasoning_effort", "low")
    effort = effort_value if isinstance(effort_value, str) else "low"

    sequential_started = time.perf_counter()
    sequential = [_request(gateway, effort) for _ in range(count)]
    sequential_wall_ms = round((time.perf_counter() - sequential_started) * 1000)

    parallel_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_request, gateway, effort) for _ in range(count)]
        parallel = [future.result() for future in futures]
    parallel_wall_ms = round(
        (time.perf_counter() - parallel_started) * 1000
    )

    results = [
        _aggregate("sequential", 1, sequential, sequential_wall_ms),
        _aggregate("parallel", 2, parallel, parallel_wall_ms),
    ]
    _print_table(results)
    return {
        "benchmark": "concurrency_smoke",
        "requests_per_mode": count,
        "results": results,
    }


def _request(gateway: InferenceGateway, effort: str) -> JsonObject:
    result = gateway.reason(
        [
            {"role": "system", "content": "Reply with exactly the text requested"},
            {"role": "user", "content": "Return exactly:\nBLACKBOX_BOLT_OK"},
        ],
        reasoning_effort=effort,
    )
    tokens_per_second = (
        result.usage.completion_tokens / (result.latency_ms / 1000)
        if result.latency_ms > 0
        else 0.0
    )
    return {
        "passed": result.content.strip() == "BLACKBOX_BOLT_OK",
        "content": result.content,
        "latency_ms": result.latency_ms,
        "usage": usage(result),
        "output_tokens_per_second": round(tokens_per_second, 2),
    }


def _aggregate(
    name: str,
    concurrency: int,
    requests: list[JsonObject],
    wall_ms: int,
) -> JsonObject:
    latencies = [
        value
        for request in requests
        if isinstance((value := request.get("latency_ms")), int)
    ]
    rates = [
        float(value)
        for request in requests
        if isinstance((value := request.get("output_tokens_per_second")), int | float)
    ]
    return {
        "name": name,
        "passed": all(request.get("passed") is True for request in requests),
        "concurrency": concurrency,
        "request_count": len(requests),
        "wall_ms": wall_ms,
        "latency_ms": latencies,
        "mean_latency_ms": round(fmean(latencies), 2) if latencies else 0.0,
        "usage": [request["usage"] for request in requests],
        "mean_output_tokens_per_second": round(fmean(rates), 2) if rates else 0.0,
        "requests": requests,
    }


def _print_table(results: list[JsonObject]) -> None:
    print("mode        C   N   wall_ms   mean_latency_ms   mean_output_tok/s")
    for result in results:
        print(
            f"{str(result['name']):<10}  {str(result['concurrency']):>1}  "
            f"{str(result['request_count']):>2}  {str(result['wall_ms']):>8}  "
            f"{str(result['mean_latency_ms']):>16}  "
            f"{str(result['mean_output_tokens_per_second']):>17}"
        )


def main() -> int:
    args = parse_args(__doc__ or "Concurrency smoke test")
    gateway = InferenceGateway(base_url=args.base_url, model=args.model)
    payload = run(gateway, reasoning_effort=args.reasoning_effort)
    if args.out is not None:
        write_artifact(args.out, payload, gateway)
    results = payload["results"]
    summarize(results if isinstance(results, list) else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
