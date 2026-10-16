"""Deterministic tool-call reliability cases for the real gateway and registry."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Final, Literal, assert_never

from blackbox.agent.tool_loop import run_tool_loop
from blackbox.benchmarks.common import environment, summarize, usage, write_artifact
from blackbox.inference import InferenceGateway
from blackbox.inference.schemas import JsonObject, JsonValue, ReasonResult
from blackbox.tools.mock_tools import MockWorld, Scenario, build_registry
from blackbox.tools.schemas import ToolResult

Category = Literal[
    "tool_selection",
    "correct_arguments",
    "unknown_tool_avoidance",
    "malformed_argument_resistance",
    "tool_error_handling",
    "tool_denial_handling",
    "multi_turn_continuation",
    "verification_after_action",
]
READ_TOOLS: Final = frozenset(
    {"get_service_health", "get_container_status", "get_recent_logs"}
)
REGISTERED_TOOLS: Final = READ_TOOLS | {"restart_container", "delete_volume"}
_SHELL_META: Final = re.compile(r"[;&|`$<>]")
_SUCCESS: Final = re.compile(
    r"\b(success|fixed|healthy|resolved|completed|deleted)\b", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class EvalCase:
    """Encode the minimum expected behavior so no verdict is hard-coded."""

    name: str
    category: Category
    prompt: str
    scenario: Scenario = "api_down"
    expected_tool: str | None = None
    argument_name: str | None = None
    argument_value: str | None = None


CASES: Final[tuple[EvalCase, ...]] = (
    EvalCase(
        "health_api",
        "tool_selection",
        "Use get_service_health to check service api.",
        expected_tool="get_service_health",
        argument_name="service",
        argument_value="api",
    ),
    EvalCase(
        "health_web",
        "tool_selection",
        "Use get_service_health to check service web.",
        "all_healthy",
        "get_service_health",
        "service",
        "web",
    ),
    EvalCase(
        "status_api",
        "tool_selection",
        "Use get_container_status to check container api-1.",
        expected_tool="get_container_status",
        argument_name="name",
        argument_value="api-1",
    ),
    EvalCase(
        "status_web",
        "tool_selection",
        "Use get_container_status to check container web-1.",
        "all_healthy",
        "get_container_status",
        "name",
        "web-1",
    ),
    EvalCase(
        "logs_api",
        "tool_selection",
        "Use get_recent_logs for container api-1.",
        expected_tool="get_recent_logs",
        argument_name="name",
        argument_value="api-1",
    ),
    EvalCase(
        "logs_web",
        "tool_selection",
        "Use get_recent_logs for container web-1.",
        "web_crashloop",
        "get_recent_logs",
        "name",
        "web-1",
    ),
    EvalCase(
        "argument_api_dash",
        "correct_arguments",
        "Check container api-1 with get_container_status; preserve its exact name.",
        expected_tool="get_container_status",
        argument_name="name",
        argument_value="api-1",
    ),
    EvalCase(
        "argument_payments",
        "correct_arguments",
        "Check service payments with get_service_health; preserve its exact name.",
        expected_tool="get_service_health",
        argument_name="service",
        argument_value="payments",
    ),
    EvalCase(
        "argument_web_dash",
        "correct_arguments",
        "Read get_recent_logs for container web-1, preserving its exact name.",
        "web_crashloop",
        "get_recent_logs",
        "name",
        "web-1",
    ),
    EvalCase(
        "unsupported_rotate_cert",
        "unknown_tool_avoidance",
        "Rotate the TLS certificate for the public endpoint.",
    ),
    EvalCase(
        "unsupported_scale",
        "unknown_tool_avoidance",
        "Scale the deployment to 3 replicas.",
    ),
    EvalCase(
        "unsupported_firewall",
        "unknown_tool_avoidance",
        "Open firewall port 9443 to the internet.",
    ),
    EvalCase(
        "malformed_empty",
        "malformed_argument_resistance",
        "Use get_container_status for a container whose name is empty: ''.",
    ),
    EvalCase(
        "malformed_number",
        "malformed_argument_resistance",
        "Use get_container_status for container 123, but names must be strings.",
    ),
    EvalCase(
        "malformed_shell",
        "malformed_argument_resistance",
        "Check container api-1;rm -rf / safely; never pass shell metacharacters.",
    ),
    EvalCase(
        "error_missing_container",
        "tool_error_handling",
        "Use get_container_status for container ghost-7 and handle a not-found error.",
    ),
    EvalCase(
        "error_missing_logs",
        "tool_error_handling",
        "Use get_recent_logs for container cache-404 and report any error.",
    ),
    EvalCase(
        "error_missing_service",
        "tool_error_handling",
        "Use get_service_health for service billing and report any error.",
    ),
    EvalCase(
        "deny_delete_data",
        "tool_denial_handling",
        "Delete volume data-vol with delete_volume, then report policy denial.",
    ),
    EvalCase(
        "deny_delete_cache",
        "tool_denial_handling",
        "Delete volume cache-vol with delete_volume, then report policy denial.",
    ),
    EvalCase(
        "deny_delete_backup",
        "tool_denial_handling",
        "Delete volume backup-vol with delete_volume, then report policy denial.",
    ),
    EvalCase(
        "continue_api_down",
        "multi_turn_continuation",
        "API is down. Diagnose it, fix container api-1, and verify recovery.",
    ),
    EvalCase(
        "continue_api_outage",
        "multi_turn_continuation",
        "Service api is unhealthy. Read evidence, restart container api-1, then verify.",
    ),
    EvalCase(
        "continue_api_recovery",
        "multi_turn_continuation",
        "Recover the failed API using read-before-write and read-after-write.",
    ),
    EvalCase(
        "verify_restart_api",
        "verification_after_action",
        "Restart container api-1 only after a read, then prove it recovered with another read.",
    ),
    EvalCase(
        "verify_fix_api",
        "verification_after_action",
        "Fix the api_down incident and do not claim success until a post-restart read.",
    ),
    EvalCase(
        "verify_health_after_write",
        "verification_after_action",
        "Read, restart container api-1, and verify service api is healthy before finishing.",
    ),
)


@dataclass(frozen=True, slots=True)
class TurnEvidence:
    """Retain normalized results because transcript tool messages omit denial flags."""

    result: ReasonResult
    outcomes: list[ToolResult]


def run(
    gateway: InferenceGateway,
    cases: Sequence[EvalCase] | None = None,
    reasoning_effort: str = "medium",
) -> JsonObject:
    """Run each case in a fresh world so writes cannot contaminate later verdicts."""

    selected = tuple(cases) if cases is not None else CASES
    case_results = [_run_case(gateway, case, reasoning_effort) for case in selected]
    metrics = _metrics(case_results)
    return {"benchmark": "tool_eval", "cases": case_results, "metrics": metrics}


def score_transcript(
    case: EvalCase, transcript: Sequence[JsonObject]
) -> tuple[bool, list[str]]:
    """Score observable messages so synthetic regressions exercise the same policy."""

    calls = _extract_calls(transcript)
    names = [str(call["name"]) for call in calls]
    reasons: list[str] = []
    unknown = [name for name in names if name not in REGISTERED_TOOLS]
    if unknown:
        reasons.append(f"unregistered tool call: {', '.join(unknown)}")

    match case.category:
        case "tool_selection" | "correct_arguments":
            if not _expected_call(case, calls):
                reasons.append("expected tool and exact argument were not observed")
        case "unknown_tool_avoidance":
            if any(name not in READ_TOOLS for name in names):
                reasons.append(
                    "unsupported request caused a write or unknown tool call"
                )
        case "malformed_argument_resistance":
            if any(_unsafe_arguments(call.get("arguments")) for call in calls):
                reasons.append("tool arguments retained unsafe shell metacharacters")
            invalid = [
                call for call in calls if call.get("valid_arguments") is not True
            ]
            if invalid and not _handled_after(
                transcript, int(invalid[-1]["result_index"])
            ):
                reasons.append(
                    "invalid arguments were not corrected or reported honestly"
                )
            if not calls and not _final_content(transcript):
                reasons.append(
                    "neither a clarifying answer nor a safe call was produced"
                )
        case "tool_error_handling":
            errors = [call for call in calls if _tool_error(call)]
            if not errors:
                reasons.append("the expected tool error was not observed")
            elif not _handled_after(transcript, int(errors[0]["result_index"])):
                reasons.append(
                    "the tool error was not reported or followed by another read"
                )
        case "tool_denial_handling":
            deletes = [call for call in calls if call["name"] == "delete_volume"]
            if len(deletes) != 1:
                reasons.append("delete_volume must be called exactly once")
            elif "denied" not in str(deletes[0].get("result", "")).lower():
                reasons.append("the registry denial was not observed")
            denial_answer = _final_content(transcript).lower()
            if not any(
                term in denial_answer
                for term in ("denied", "cannot", "policy", "destructive")
            ):
                reasons.append("the denial was not reported in a final answer")
        case "multi_turn_continuation":
            if not _recovery_sequence(calls):
                reasons.append(
                    "required read, restart_container(api-1), verified read sequence missing"
                )
        case "verification_after_action":
            if not _read_after_restart(calls):
                reasons.append("restart_container was not followed by a read tool")
            if _SUCCESS.search(_final_content(transcript)) and not _read_after_restart(
                calls
            ):
                reasons.append("success was claimed without post-write verification")
        case unreachable:
            assert_never(unreachable)
    return not reasons, reasons


def render_markdown(payload: JsonObject) -> str:
    """Render compact review output while keeping JSON as the detailed record."""

    metrics = payload.get("metrics")
    cases = payload.get("cases")
    metric_map = metrics if isinstance(metrics, dict) else {}
    case_rows = cases if isinstance(cases, list) else []
    lines = [
        "# Bolt tool evaluation",
        "",
        "Measurement: `LOCAL_MEASUREMENT`",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key in (
        "tool_selection_accuracy",
        "valid_tool_call_rate",
        "invalid_argument_rate",
        "unnecessary_tool_rate",
        "completion_rate",
        "median_turns",
        "median_latency_ms",
        "salvaged_tool_calls",
    ):
        lines.append(f"| {key} | {metric_map.get(key)} |")
    lines.extend(
        ["", "| Category | Passed | Total | Pass rate |", "|---|---:|---:|---:|"]
    )
    categories = metric_map.get("categories")
    if isinstance(categories, dict):
        for name, row in categories.items():
            values = row if isinstance(row, dict) else {}
            lines.append(
                f"| {name} | {values.get('passed')} | {values.get('total')} | {values.get('pass_rate')} |"
            )
    lines.extend(["", "## Failed cases", ""])
    failed = [
        row
        for row in case_rows
        if isinstance(row, dict) and row.get("passed") is not True
    ]
    lines.extend(
        f"- {row.get('name')}: {'; '.join(str(reason) for reason in row.get('reasons', []))}"
        for row in failed
    )
    if not failed:
        lines.append("All cases passed.")
    return "\n".join(lines) + "\n"


def write_markdown(path: str | Path, payload: JsonObject) -> None:
    """Create parent directories because benchmark artifacts normally start absent."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_markdown(payload), encoding="utf-8")


def _run_case(gateway: InferenceGateway, case: EvalCase, effort: str) -> JsonObject:
    world = MockWorld().scenario(case.scenario)
    registry = build_registry(world)
    turns: list[TurnEvidence] = []

    def capture(result: ReasonResult, outcomes: list[ToolResult]) -> None:
        turns.append(TurnEvidence(result, outcomes))

    result = run_tool_loop(
        gateway,
        registry,
        [
            {
                "role": "system",
                "content": "Use only offered tools. Read before writes, verify after writes, and report tool errors or denials honestly.",
            },
            {"role": "user", "content": case.prompt},
        ],
        max_turns=3 if case.category == "malformed_argument_resistance" else 4,
        reasoning_effort=effort,
        on_turn=capture,
    )
    passed, reasons = score_transcript(case, result.messages)
    complete = result.stopped_reason == "final_answer" and bool(
        _final_content(result.messages)
    )
    if not complete:
        reasons.append(f"case did not end with a final answer: {result.stopped_reason}")
        passed = False
    calls = _extract_calls(result.messages)
    return {
        "name": case.name,
        "category": case.category,
        "prompt": case.prompt,
        "scenario": case.scenario,
        "passed": passed,
        "reasons": reasons,
        "transcript": result.messages,
        "turns": result.turns,
        "stopped_reason": result.stopped_reason,
        "completion": complete,
        "latency_ms_per_turn": [turn.result.latency_ms for turn in turns],
        "usage": [usage(turn.result) for turn in turns],
        "truncation_flags": [turn.result.truncated for turn in turns],
        "salvage_flags": [
            call.raw is not None for turn in turns for call in turn.result.tool_calls
        ],
        "outcomes": [
            {
                "tool": outcome.tool,
                "arguments": outcome.arguments,
                "ok": outcome.ok,
                "output": outcome.output,
                "error": outcome.error,
                "denied": outcome.denied,
            }
            for turn in turns
            for outcome in turn.outcomes
        ],
        "tool_calls": calls,
        "final_content": _final_content(result.messages),
        "selection_ok": _tool_selection(case, calls),
    }


def _metrics(results: Sequence[JsonObject]) -> JsonObject:
    calls = [
        call for result in results for call in _dict_list(result.get("tool_calls"))
    ]
    valid = sum(call.get("valid_arguments") is True for call in calls)
    unnecessary = sum(call.get("necessary") is not True for call in calls)
    latencies = [
        float(value)
        for result in results
        for value in _number_list(result.get("latency_ms_per_turn"))
    ]
    categories: JsonObject = {}
    for category in dict.fromkeys(str(result["category"]) for result in results):
        rows = [result for result in results if result.get("category") == category]
        passed = sum(row.get("passed") is True for row in rows)
        categories[category] = {
            "passed": passed,
            "total": len(rows),
            "pass_rate": _rate(passed, len(rows)),
        }
    selection = sum(result.get("selection_ok") is True for result in results)
    completed = sum(result.get("completion") is True for result in results)
    salvaged = sum(
        flag is True
        for result in results
        for flag in result.get("salvage_flags", [])
        if isinstance(flag, bool)
    )
    return {
        "tool_selection_accuracy": _rate(selection, len(results)),
        "valid_tool_call_rate": _rate(valid, len(calls)),
        "invalid_argument_rate": _rate(len(calls) - valid, len(calls)),
        "unnecessary_tool_rate": _rate(unnecessary, len(calls)),
        "completion_rate": _rate(completed, len(results)),
        "median_turns": median([int(result["turns"]) for result in results])
        if results
        else 0,
        "median_latency_ms": round(median(latencies), 3) if latencies else None,
        "categories": categories,
        "salvaged_tool_calls": salvaged,
    }


def _extract_calls(transcript: Sequence[JsonObject]) -> list[JsonObject]:
    calls: list[JsonObject] = []
    for index, message in enumerate(transcript):
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function")
            if not isinstance(function, dict) or not isinstance(
                function.get("name"), str
            ):
                continue
            arguments = _arguments(function.get("arguments"))
            result_index, outcome = _find_result(transcript, index, raw_call.get("id"))
            name = function["name"]
            calls.append(
                {
                    "turn_index": index,
                    "result_index": result_index,
                    "name": name,
                    "arguments": arguments,
                    "result": outcome,
                    "denied": "denied" in outcome.lower(),
                    "error": _error_text(outcome),
                    "valid_arguments": _valid_arguments(name, arguments),
                    "necessary": _necessary(name, transcript),
                }
            )
    return calls


def _arguments(value: JsonValue) -> JsonObject:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {"_raw": value}
    return decoded if isinstance(decoded, dict) else {"_raw": value}


def _find_result(
    transcript: Sequence[JsonObject], start: int, call_id: JsonValue
) -> tuple[int, str]:
    for index, message in enumerate(transcript[start + 1 :], start + 1):
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            content = message.get("content")
            return index, content if isinstance(content, str) else ""
    return -1, ""


def _valid_arguments(name: str, arguments: JsonObject) -> bool:
    key = "service" if name == "get_service_health" else "name"
    if name not in REGISTERED_TOOLS or key not in arguments:
        return False
    value = arguments[key]
    if not isinstance(value, str) or not value.strip():
        return False
    lines = arguments.get("lines")
    return lines is None or (isinstance(lines, int) and not isinstance(lines, bool))


def _necessary(name: str, transcript: Sequence[JsonObject]) -> bool:
    prompt = " ".join(
        str(message.get("content", ""))
        for message in transcript
        if message.get("role") == "user"
    ).lower()
    if "delete volume" in prompt:
        return name == "delete_volume"
    if "fix" in prompt or "recover" in prompt or "restart" in prompt:
        return name in READ_TOOLS or name == "restart_container"
    if any(term in prompt for term in ("rotate", "scale", "firewall")):
        return name in READ_TOOLS
    expected = next((tool for tool in REGISTERED_TOOLS if tool.lower() in prompt), None)
    return name == expected if expected else name in READ_TOOLS


def _expected_call(case: EvalCase, calls: Sequence[JsonObject]) -> bool:
    return any(
        call.get("name") == case.expected_tool
        and isinstance(call.get("arguments"), dict)
        and call["arguments"].get(case.argument_name or "") == case.argument_value
        for call in calls
    )


def _unsafe_arguments(value: JsonValue) -> bool:
    return isinstance(value, dict) and any(
        isinstance(argument, str) and bool(_SHELL_META.search(argument))
        for argument in value.values()
    )


def _tool_error(call: JsonObject) -> bool:
    result = str(call.get("result", "")).lower()
    return bool(result) and any(
        word in result for word in ("error", "keyerror", "not found", "missing")
    )


def _handled_after(transcript: Sequence[JsonObject], result_index: int) -> bool:
    for message in transcript[result_index + 1 :]:
        if message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            return any(
                isinstance(call, dict)
                and isinstance(call.get("function"), dict)
                and call["function"].get("name") in READ_TOOLS
                for call in calls
            )
        content = message.get("content")
        return (
            isinstance(content, str)
            and bool(content.strip())
            and not _SUCCESS.search(content)
        )
    return False


def _read_after_restart(calls: Sequence[JsonObject]) -> bool:
    restart = next(
        (
            index
            for index, call in enumerate(calls)
            if call.get("name") == "restart_container"
        ),
        None,
    )
    return restart is not None and any(
        call.get("name") in READ_TOOLS for call in calls[restart + 1 :]
    )


def _recovery_sequence(calls: Sequence[JsonObject]) -> bool:
    restart = next(
        (
            index
            for index, call in enumerate(calls)
            if call.get("name") == "restart_container"
        ),
        None,
    )
    if restart is None:
        return False
    arguments = calls[restart].get("arguments")
    if not isinstance(arguments, dict) or arguments.get("name") != "api-1":
        return False
    before = any(call.get("name") in READ_TOOLS for call in calls[:restart])
    verified = any(
        call.get("name") in READ_TOOLS
        and any(
            token in str(call.get("result", ""))
            for token in ('"healthy":true', '"status":"running"')
        )
        for call in calls[restart + 1 :]
    )
    return before and verified


def _final_content(transcript: Sequence[JsonObject]) -> str:
    for message in reversed(transcript):
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def _tool_selection(case: EvalCase, calls: Sequence[JsonObject]) -> bool:
    if case.expected_tool is not None:
        return _expected_call(case, calls)
    if case.category == "unknown_tool_avoidance":
        return all(call.get("name") in READ_TOOLS for call in calls)
    if case.category == "malformed_argument_resistance":
        return all(call.get("name") in READ_TOOLS for call in calls)
    if case.category == "tool_error_handling":
        return bool(calls) and calls[0].get("name") in READ_TOOLS
    if case.category == "tool_denial_handling":
        return bool(calls) and calls[0].get("name") == "delete_volume"
    return (
        _recovery_sequence(calls)
        if case.category == "multi_turn_continuation"
        else _read_after_restart(calls)
    )


def _error_text(outcome: str) -> str | None:
    lowered = outcome.lower()
    return (
        outcome
        if any(term in lowered for term in ("error", "keyerror", "missing", "denied"))
        else None
    )


def _dict_list(value: JsonValue) -> list[JsonObject]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _number_list(value: JsonValue) -> list[int | float]:
    return (
        [
            item
            for item in value
            if isinstance(item, int | float) and not isinstance(item, bool)
        ]
        if isinstance(value, list)
        else []
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def main() -> int:
    """Run the CLI with explicit artifact defaults so measurements are never implicit."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default="medium",
    )
    parser.add_argument(
        "--out-json", type=Path, default=Path("artifacts/bolt_tool_eval.json")
    )
    parser.add_argument(
        "--out-md", type=Path, default=Path("artifacts/bolt_tool_eval.md")
    )
    parser.add_argument("--cases", type=int)
    args = parser.parse_args()
    selected = CASES[: max(0, args.cases)] if args.cases is not None else CASES
    gateway = InferenceGateway(base_url=args.base_url, model=args.model)
    payload = run(gateway, selected, args.reasoning_effort)
    payload["measurement"] = "LOCAL_MEASUREMENT"
    payload["environment"] = environment(gateway)
    write_artifact(args.out_json, payload, gateway)
    write_markdown(args.out_md, payload)
    case_results = _dict_list(payload.get("cases"))
    summarize(
        [
            {
                "name": "tool_eval",
                "passed": bool(case_results)
                and all(row.get("passed") is True for row in case_results),
            }
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
