from __future__ import annotations

from blackbox.benchmarks.common import parse_args, summarize, usage, write_artifact
from blackbox.inference import InferenceGateway
from blackbox.inference.schemas import JsonObject, JsonValue

TOOLS: list[JsonObject] = [
    {
        "type": "function",
        "function": {
            "name": "get_service_health",
            "description": "Get the health of a service",
            "parameters": {
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_container_status",
            "description": "Get the status of a container",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
]


def run(gateway: InferenceGateway, **opts: JsonValue) -> JsonObject:
    """Ask for one tool call and validate its normalized shape."""

    effort_value = opts.get("reasoning_effort", "low")
    effort = effort_value if isinstance(effort_value, str) else "low"
    result = gateway.reason(
        [{"role": "user", "content": "Check the health of service api"}],
        tools=TOOLS,
        reasoning_effort=effort,
    )
    call = result.tool_calls[0] if len(result.tool_calls) == 1 else None
    required_key = None
    if call is not None:
        required_key = "service" if call.name == "get_service_health" else "name"
    passed = (
        call is not None
        and call.name in {"get_service_health", "get_container_status"}
        and required_key in call.arguments
        and "_raw" not in call.arguments
    )
    check: JsonObject = {
        "name": "single_tool_call",
        "passed": passed,
        "tool_call_count": len(result.tool_calls),
        "tool_name": call.name if call is not None else None,
        "arguments": call.arguments if call is not None else {},
        "source": (
            None
            if call is None
            else "salvage"
            if call.raw is not None
            else "parser"
        ),
        "latency_ms": result.latency_ms,
        "usage": usage(result),
    }
    return {"benchmark": "tool_call_smoke", "results": [check]}


def main() -> int:
    args = parse_args(__doc__ or "Tool-call smoke test")
    gateway = InferenceGateway(base_url=args.base_url, model=args.model)
    payload = run(gateway, reasoning_effort=args.reasoning_effort)
    if args.out is not None:
        write_artifact(args.out, payload, gateway)
    results = payload["results"]
    summarize(results if isinstance(results, list) else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
