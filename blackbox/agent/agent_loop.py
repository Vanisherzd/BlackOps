"""Minimal incident loop adds state and a finish verdict to the generic loop."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from blackbox.agent.incident_state import IncidentState, IncidentStatus
from blackbox.agent.tool_loop import ReasonGateway, run_tool_loop
from blackbox.inference.schemas import JsonObject, ReasonResult
from blackbox.tools.mock_tools import MockWorld
from blackbox.tools.registry import ToolRegistry
from blackbox.tools.schemas import Tool, ToolResult

DEFAULT_SYSTEM_PROMPT: Final = """You are a minimal incident responder.
Read before write. Take one action at a time. Verify after every write with a read.
Never claim success without a read that shows it. Finish only by calling the finish tool."""

FinishStatus = Literal["resolved", "escalated", "failed"]
VerifyHook = Callable[[IncidentState, MockWorld | None], bool]


@dataclass(frozen=True, slots=True)
class InvalidFinishStatusError(Exception):
    """Identify malformed model verdicts without accepting accidental success."""

    status: str

    def __str__(self) -> str:
        return f"unsupported finish status: {self.status}"


def run_incident(
    objective: str,
    gateway: ReasonGateway,
    registry: ToolRegistry,
    *,
    max_turns: int = 8,
    reasoning_effort: str = "medium",
    system_prompt: str | None = None,
    verify: VerifyHook | None = None,
) -> IncidentState:
    """Run one bounded incident and require an explicit finish-tool verdict."""

    state = IncidentState.new(objective)
    verdict: dict[str, str] = {}

    def finish(arguments: JsonObject) -> str:
        raw_status = arguments.get("status", "resolved")
        status = raw_status if isinstance(raw_status, str) else ""
        if status not in {"resolved", "escalated", "failed"}:
            raise InvalidFinishStatusError(status)
        root_cause = str(arguments["root_cause"])
        actions = str(arguments["actions"])
        verdict.update(status=status, root_cause=root_cause, actions=actions)
        return json.dumps({"accepted": True, "status": status}, separators=(",", ":"))

    registry.register(
        Tool(
            name="finish",
            description="Record the final incident verdict after evidence-based verification.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["resolved", "escalated", "failed"],
                        "default": "resolved",
                    },
                    "root_cause": {"type": "string"},
                    "actions": {"type": "string"},
                },
                # The status default lets the deterministic mock exercise finish safely.
                "required": ["root_cause", "actions"],
            },
            handler=finish,
            risk="read",
        )
    )

    instructions = system_prompt or DEFAULT_SYSTEM_PROMPT
    messages: list[JsonObject] = [
        {"role": "system", "content": f"{instructions}\nObjective: {objective}"},
        {"role": "user", "content": objective},
    ]

    def update_state(reason: ReasonResult, outcomes: list[ToolResult]) -> bool | None:
        for line in reason.content.splitlines():
            lowered = line.lower()
            if "hypothes" in lowered or "likely" in lowered:
                state.hypotheses.append(line.strip())
        for call, outcome in zip(reason.tool_calls, outcomes, strict=True):
            state.record_tool(call, outcome)
            tool = registry.get(call.name)
            if tool is not None and tool.risk == "read" and outcome.output:
                state.observations.append(outcome.output.strip())
            if tool is not None and tool.risk == "write":
                state.actions_taken.append(
                    f"{call.name} {json.dumps(call.arguments, sort_keys=True, separators=(',', ':'))}"
                )
        state.turns += 1
        return False if verdict else None

    loop_result = run_tool_loop(
        gateway,
        registry,
        messages,
        max_turns=max_turns,
        reasoning_effort=reasoning_effort,
        on_turn=update_state,
    )
    state.turns = loop_result.turns
    state.finished_at = time.time()
    if verdict:
        state.status = _finish_status(verdict["status"])
        state.root_cause = verdict["root_cause"]
        state.summary = verdict["actions"]
        if verify is not None and not verify(state, registry.world):
            state.status = "failed"
            state.summary = "The finish claim was not verified by the configured verification hook."
    else:
        state.status = "failed"
        state.summary = f"Incident ended without finish: {loop_result.stopped_reason}."
    return state


def _finish_status(value: str) -> IncidentStatus:
    """Narrow a handler-validated string for the state type checker."""

    if value == "resolved":
        return "resolved"
    if value == "escalated":
        return "escalated"
    return "failed"
