from __future__ import annotations

import json

from blackbox.benchmarks.common import parse_args, summarize, usage, write_artifact
from blackbox.inference import InferenceGateway
from blackbox.inference.schemas import JsonObject, JsonValue, ReasonResult


def run(gateway: InferenceGateway, **opts: JsonValue) -> JsonObject:
    """Run the inference contract checks and return JSON-safe measurements."""

    effort_value = opts.get("reasoning_effort", "low")
    effort = effort_value if isinstance(effort_value, str) else "low"
    results: list[JsonObject] = []

    exact = gateway.reason(
        [
            {"role": "system", "content": "Reply with exactly the text requested"},
            {"role": "user", "content": "Return exactly:\nBLACKBOX_BOLT_OK"},
        ],
        reasoning_effort="low",
    )
    results.append(
        _check("exact_text", exact, exact.content.strip() == "BLACKBOX_BOLT_OK")
    )

    json_prompt = "Return a JSON object with keys status and echo. JSON only."
    for name, response_format in (
        ("structured_json", {"type": "json_object"}),
        ("structured_json_fallback", None),
    ):
        structured = gateway.reason(
            [{"role": "user", "content": json_prompt}],
            reasoning_effort=effort,
            response_format=response_format,
        )
        results.append(_json_check(name, structured))

    arithmetic = "What is 17 * 23? Give the final numeric answer."
    reasoning_none = gateway.reason(
        [{"role": "user", "content": arithmetic}],
        reasoning_effort="none",
    )
    results.append(
        _reasoning_check(
            "reasoning_none",
            reasoning_none,
            passed=reasoning_none.reasoning is None
            and bool(reasoning_none.content.strip()),
        )
    )
    reasoning_high = gateway.reason(
        [{"role": "user", "content": arithmetic}],
        reasoning_effort="high",
    )
    results.append(
        _reasoning_check(
            "reasoning_high",
            reasoning_high,
            passed=reasoning_high.reasoning is not None
            and bool(reasoning_high.content.strip()),
        )
    )
    truncated = gateway.reason(
        [{"role": "user", "content": arithmetic}],
        reasoning_effort="high",
        max_tokens=16,
    )
    results.append(
        _reasoning_check(
            "truncation_explicit", truncated, passed=truncated.truncated
        )
    )
    return {"benchmark": "inference_smoke", "results": results}


def _check(name: str, result: ReasonResult, passed: bool) -> JsonObject:
    return {
        "name": name,
        "passed": passed,
        "content": result.content,
        "latency_ms": result.latency_ms,
        "usage": usage(result),
    }


def _json_check(name: str, result: ReasonResult) -> JsonObject:
    try:
        decoded = json.loads(result.content)
    except json.JSONDecodeError:
        decoded = None
    check = _check(name, result, isinstance(decoded, dict) and "status" in decoded)
    check["decoded"] = decoded
    return check


def _reasoning_check(name: str, result: ReasonResult, passed: bool) -> JsonObject:
    check = _check(name, result, passed)
    check.update(
        {
            "reasoning_length": len(result.reasoning or ""),
            "truncated": result.truncated,
            "reasoning_only": result.reasoning_only,
            "finish_reason": result.finish_reason,
        }
    )
    return check


def main() -> int:
    args = parse_args(__doc__ or "Inference smoke test")
    gateway = InferenceGateway(base_url=args.base_url, model=args.model)
    payload = run(gateway, reasoning_effort=args.reasoning_effort)
    if args.out is not None:
        write_artifact(args.out, payload, gateway)
    results = payload["results"]
    summarize(results if isinstance(results, list) else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
