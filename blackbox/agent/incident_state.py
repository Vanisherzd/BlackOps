"""Mutable incident state preserves an auditable record across model turns."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Literal, Self

from blackbox.inference.schemas import JsonObject, ToolCall
from blackbox.tools.schemas import ToolResult

IncidentStatus = Literal["open", "resolved", "escalated", "failed"]


@dataclass(slots=True)  # noqa: MUTABLE_OK - this is the loop's intentional accumulator.
class IncidentState:
    """Accumulate evidence and the final verdict in one serializable record."""

    incident_id: str
    objective: str
    observations: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    tool_results: list[JsonObject] = field(default_factory=list)
    status: IncidentStatus = "open"
    turns: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    root_cause: str | None = None
    summary: str | None = None

    @classmethod
    def new(cls, objective: str) -> Self:
        """Create a collision-resistant ID that remains sortable by start time."""

        started_at = time.time()
        return cls(
            incident_id=f"incident-{int(started_at * 1000)}-{secrets.token_hex(2)}",
            objective=objective,
            started_at=started_at,
        )

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        """Retain both requested arguments and policy outcome for later review."""

        self.tool_results.append(
            {
                "tool_call_id": call.id,
                "tool": call.name,
                "arguments": dict(call.arguments),
                "ok": result.ok,
                "output": result.output,
                "error": result.error,
                "denied": result.denied,
            }
        )

    def to_dict(self) -> JsonObject:
        """Return detached containers so serialization cannot mutate live state."""

        return {
            "incident_id": self.incident_id,
            "objective": self.objective,
            "observations": list(self.observations),
            "hypotheses": list(self.hypotheses),
            "actions_taken": list(self.actions_taken),
            "tool_results": [dict(result) for result in self.tool_results],
            "status": self.status,
            "turns": self.turns,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "root_cause": self.root_cause,
            "summary": self.summary,
        }


def new(objective: str) -> IncidentState:
    """Offer the plan's short factory without hiding the state type."""

    return IncidentState.new(objective)
