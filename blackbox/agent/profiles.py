"""Phase 8 interfaces only; this baseline deliberately builds no second agent."""

from __future__ import annotations

from dataclasses import dataclass

from blackbox.agent.incident_state import IncidentState


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Declare future per-agent permissions without adding orchestration now."""

    name: str
    allowed_skills: list[str]
    allowed_tools: list[str]
    system_instructions: str


SharedIncidentState = IncidentState
