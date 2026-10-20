from __future__ import annotations

import json

from blackbox.benchmarks.common import parse_args, summarize, usage, write_artifact
from blackbox.benchmarks.tool_call_smoke import TOOLS
from blackbox.inference import InferenceGateway
from blackbox.inference.schemas import JsonObject, JsonValue, ReasonResult, ToolCall


def run(gateway: InferenceGateway, **opts: JsonValue) -> JsonObject:
    """Run a successful fake-tool cycle and a separate error-result cycle."""

    effort_value = opts.get("reasoning_effort", "low")
    effort = effort_value if isinstance(effort_value, str) else "low"
    normal = _cycle(
        gateway,
        json.dumps({"service": "api", "healthy": False, "container": "api-1"}),
        effort,
    )
    normal["name"] = "tool_result_cycle"
    final_text = normal.get("final_content")
    normal["passed"] = (
        normal.get("cycle_complete") is True and _mentions_incident(final_text)
    )

    error = _cycle(gateway, "ERROR: service api health lookup failed", effort)
    error["name"] = "tool_error_cycle"
    error_text = error.get("final_content")
    lowered = error_text.lower() if isinstance(error_text, str) else ""
    claims_success = (
        '"healthy": true' in lowered or "resolved" in lowered
    ) and "error" not in lowered
    error["passed"] = (
        error.get("cycle_complete") is True and bool(lowered) and not claims_success
    )
    return {"benchmark": "multi_turn_smoke", "results": [normal, error]}


def _cycle(gateway: InferenceGateway, tool_content: str, effort: str) -> JsonObject:
    messages: list[JsonObject] = [
        {"role": "user", "content": "Check the health of service api"}
    ]
    first = gateway.reason(messages, tools=TOOLS, reasoning_effort=effort)
    if not first.tool_calls:
        return {
            "cycle_complete": False,
            "final_content": first.content,
            "turns": [_turn(first)],
            "latency_ms": {"turn_1": first.latency_ms},
            "usage": {"turn_1": usage(first)},
        }

    call = first.tool_calls[0]
    messages.extend(_tool_messages(call, tool_content))
    second = gateway.reason(messages, tools=TOOLS, reasoning_effort=effort)
    return {
        "cycle_complete": len(first.tool_calls) == 1
        and not second.tool_calls
        and (bool(second.content.strip()) or second.finish_reason == "stop"),
        "final_content": second.content,
        "turns": [_turn(first), _turn(second)],
        "latency_ms": {"turn_1": first.latency_ms, "turn_2": second.latency_ms},
        "usage": {"turn_1": usage(first), "turn_2": usage(second)},
    }


def _tool_messages(call: ToolCall, content: str) -> list[JsonObject]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": content,
        },
    ]


def _turn(result: ReasonResult) -> JsonObject:
    return {
        "content": result.content,
        "finish_reason": result.finish_reason,
        "tool_call_count": len(result.tool_calls),
        "latency_ms": result.latency_ms,
        "usage": usage(result),
    }


def _mentions_incident(value: JsonValue) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(term in lowered for term in ("api", "api-1", "unhealthy", "false"))


def main() -> int:
    args = parse_args(__doc__ or "Multi-turn smoke test")
    gateway = InferenceGateway(base_url=args.base_url, model=args.model)
    payload = run(gateway, reasoning_effort=args.reasoning_effort)
    if args.out is not None:
        write_artifact(args.out, payload, gateway)
    results = payload["results"]
    summarize(results if isinstance(results, list) else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
