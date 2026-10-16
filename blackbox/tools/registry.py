"""In-process tool registry with the baseline's non-shell safety boundary."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from blackbox.inference.schemas import JsonObject, JsonValue
from blackbox.tools.schemas import Tool, ToolResult

if TYPE_CHECKING:
    from blackbox.tools.mock_tools import MockWorld


class ToolRegistry:
    """Own registration, schema filtering, validation, and safe dispatch."""

    def __init__(self, world: MockWorld | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.world = world

    def register(self, tool: Tool) -> None:
        """Register by name so replacing a test tool is deterministic."""

        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Return None for unknown model output instead of raising a lookup error."""

        return self._tools.get(name)

    def names(self) -> list[str]:
        """Preserve registration order because the mock selects the first tool."""

        return list(self._tools)

    def openai_tools(self, allowed: Iterable[str] | None = None) -> list[JsonObject]:
        """Offer only policy-approved tools to reduce invalid model choices."""

        allowed_names = None if allowed is None else set(allowed)
        return [
            tool.to_openai()
            for tool in self._tools.values()
            if allowed_names is None or tool.name in allowed_names
        ]

    def execute(
        self,
        name: str,
        arguments: JsonObject,
        allowed: Iterable[str] | None = None,
    ) -> ToolResult:
        """Validate and invoke one handler while converting every failure to data."""

        tool = self.get(name)
        if tool is None:
            return self._failure(name, arguments, f"unknown tool {name!r}")
        if allowed is not None and name not in set(allowed):
            return self._failure(
                name,
                arguments,
                f"denied: tool {name!r} is not allowed",
                denied=True,
            )
        if tool.risk == "destructive":
            return self._failure(
                name,
                arguments,
                "denied: destructive tools are disabled in the baseline",
                denied=True,
            )

        error = _validate(arguments, tool.parameters)
        if error is not None:
            return self._failure(name, arguments, error)
        try:
            output = tool.handler(arguments)
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - handler boundary must never escape.
            return self._failure(name, arguments, f"{type(exc).__name__}: {exc}")
        return ToolResult(
            ok=True,
            output=output,
            error=None,
            denied=False,
            tool=name,
            arguments=dict(arguments),
        )

    @staticmethod
    def _failure(
        name: str,
        arguments: JsonObject,
        error: str,
        *,
        denied: bool = False,
    ) -> ToolResult:
        """Keep all rejection paths structurally identical for callers."""

        return ToolResult(
            ok=False,
            output="",
            error=error,
            denied=denied,
            tool=name,
            arguments=dict(arguments),
        )


def _validate(arguments: JsonObject, schema: JsonObject) -> str | None:
    """Check only the simple JSON-schema subset promised by the baseline."""

    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in arguments:
                return f"missing required argument {key!r}"

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    for key, value in arguments.items():
        property_schema = properties.get(key)
        if not isinstance(property_schema, dict):
            continue
        expected = property_schema.get("type")
        if isinstance(expected, str) and not _matches_simple_type(value, expected):
            return f"argument {key!r} must be {expected}, got {type(value).__name__}"
    return None


def _matches_simple_type(value: JsonValue, expected: str) -> bool:
    """Avoid Python's bool-is-int trap while checking JSON primitive types."""

    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True
