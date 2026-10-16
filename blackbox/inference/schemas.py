"""Typed results keep model-specific response details out of callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Represent a parsed call while retaining malformed source for diagnosis."""

    id: str
    name: str
    arguments: JsonObject
    raw: str | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    """Expose token accounting without coupling callers to the SDK model."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class ReasonResult:
    """Normalize vLLM version differences into the gateway's stable contract."""

    content: str
    reasoning: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
    truncated: bool
    reasoning_only: bool
    usage: Usage
    latency_ms: int
    model: str
    raw: JsonObject

    def to_dict(self) -> JsonObject:
        """Return a JSON-safe shape for logs and benchmark artifacts."""

        return {
            "content": self.content,
            "reasoning": self.reasoning,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "raw": call.raw,
                }
                for call in self.tool_calls
            ],
            "finish_reason": self.finish_reason,
            "truncated": self.truncated,
            "reasoning_only": self.reasoning_only,
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
            },
            "latency_ms": self.latency_ms,
            "model": self.model,
            "raw": self.raw,
        }


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Report probe failures as data so health checks never crash callers."""

    ok: bool
    status: int | None
    latency_ms: int
    error: str | None
