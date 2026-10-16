"""Generic OpenAI-style tool loop shared by incident and benchmark callers."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from blackbox.inference.schemas import JsonObject, JsonValue, ReasonResult
from blackbox.tools.registry import ToolRegistry
from blackbox.tools.schemas import ToolResult

StoppedReason = Literal["final_answer", "max_turns", "truncated", "error"]
TurnCallback = Callable[[ReasonResult, list[ToolResult]], bool | None]


class ReasonGateway(Protocol):
    """Accept the narrow gateway capability needed by the loop."""

    def reason(
        self,
        messages: Sequence[Mapping[str, JsonValue]],
        tools: Sequence[Mapping[str, JsonValue]] | None = None,
        reasoning_effort: str = "medium",
    ) -> ReasonResult: ...


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    """Report the complete transcript and why autonomous execution stopped."""

    messages: list[JsonObject]
    turns: int
    final: ReasonResult | None
    tool_calls: int
    denied: int
    errors: int
    stopped_reason: StoppedReason


def run_tool_loop(
    gateway: ReasonGateway,
    registry: ToolRegistry,
    messages: Sequence[Mapping[str, JsonValue]],
    *,
    max_turns: int = 8,
    reasoning_effort: str = "medium",
    allowed: Iterable[str] | None = None,
    on_turn: TurnCallback | None = None,
) -> ToolLoopResult:
    """Run bounded model/tool turns with denial, error, and repetition accounting."""

    transcript = [dict(message) for message in messages]
    allowed_names = None if allowed is None else tuple(allowed)
    offered_tools = registry.openai_tools(allowed_names)
    tool_calls = 0
    denied = 0
    errors = 0
    previous_signature: tuple[str, str] | None = None
    repeat_count = 0

    for turn in range(1, max_turns + 1):
        try:
            result = gateway.reason(
                transcript,
                tools=offered_tools,
                reasoning_effort=reasoning_effort,
            )
        except Exception:  # noqa: BROAD_EXCEPT_OK - gateway is the loop's external boundary.
            return ToolLoopResult(transcript, turn - 1, None, tool_calls, denied, errors + 1, "error")

        assistant = _assistant_message(result)
        transcript.append(assistant)
        outcomes: list[ToolResult] = []
        repeat_stopped = False
        for call in result.tool_calls:
            tool_calls += 1
            signature = (call.name, json.dumps(call.arguments, sort_keys=True, separators=(",", ":")))
            repeat_count = repeat_count + 1 if signature == previous_signature else 1
            previous_signature = signature
            if repeat_count >= 3:
                outcome = ToolResult(
                    ok=False,
                    output="",
                    error=f"repeat guard: {call.name!r} was called with identical arguments 3 times in a row",
                    denied=False,
                    tool=call.name,
                    arguments=dict(call.arguments),
                )
                errors += 1
                repeat_stopped = True
            else:
                outcome = registry.execute(call.name, call.arguments, allowed_names)
                if outcome.denied:
                    denied += 1
                elif not outcome.ok:
                    errors += 1
            outcomes.append(outcome)
            transcript.append(outcome.to_message(call.id))
            if repeat_stopped:
                break

        keep_going = True if on_turn is None else on_turn(result, outcomes) is not False
        if repeat_stopped:
            return ToolLoopResult(transcript, turn, None, tool_calls, denied, errors, "error")
        if not keep_going:
            return ToolLoopResult(transcript, turn, result, tool_calls, denied, errors, "final_answer")
        if not result.tool_calls:
            stopped_reason: StoppedReason = "truncated" if result.truncated else "final_answer"
            return ToolLoopResult(transcript, turn, result, tool_calls, denied, errors, stopped_reason)

    return ToolLoopResult(transcript, max_turns, None, tool_calls, denied, errors, "max_turns")


def _assistant_message(result: ReasonResult) -> JsonObject:
    """Preserve reasoning and canonical tool calls for the gateway's next turn."""

    message: JsonObject = {"role": "assistant", "content": result.content}
    if result.reasoning is not None:
        message["reasoning_content"] = result.reasoning
    if result.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, sort_keys=True),
                },
            }
            for call in result.tool_calls
        ]
    return message
