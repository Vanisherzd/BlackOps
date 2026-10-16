"""Typed tool contracts keep execution policy separate from model messages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from blackbox.inference.schemas import JsonObject

ToolRisk = Literal["read", "write", "destructive"]
ToolHandler = Callable[[JsonObject], str]


@dataclass(frozen=True, slots=True)
class Tool:
    """Describe one callable so the registry can enforce policy before dispatch."""

    name: str
    description: str
    parameters: JsonObject
    handler: ToolHandler
    risk: ToolRisk

    def to_openai(self) -> JsonObject:
        """Return the function-tool shape consumed by the inference gateway."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Carry failures as data so model-controlled calls never crash the loop."""

    ok: bool
    output: str
    error: str | None
    denied: bool
    tool: str
    arguments: JsonObject

    def to_message(self, tool_call_id: str) -> JsonObject:
        """Return the exact tool-role message needed for the next model turn."""

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": self.tool,
            "content": self.output if self.ok else self.error or "tool execution failed",
        }
