"""Unit tests for registry policy and deterministic mock operations."""

from __future__ import annotations

import json
import unittest

from blackbox.inference.schemas import JsonObject
from blackbox.tools.mock_tools import MockWorld, build_registry
from blackbox.tools.registry import ToolRegistry
from blackbox.tools.schemas import Tool


def _schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }


class ToolRegistryTest(unittest.TestCase):
    def test_unknown_tool_returns_error(self) -> None:
        result = ToolRegistry().execute("missing", {})

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unknown tool 'missing'")

    def test_missing_required_argument_returns_precise_error(self) -> None:
        registry = ToolRegistry()
        registry.register(Tool("lookup", "Lookup", _schema(), lambda _: "ok", "read"))

        result = registry.execute("lookup", {})

        self.assertEqual(result.error, "missing required argument 'name'")

    def test_wrong_simple_type_is_rejected(self) -> None:
        registry = ToolRegistry()
        registry.register(Tool("lookup", "Lookup", _schema(), lambda _: "ok", "read"))

        result = registry.execute("lookup", {"name": 3})

        self.assertEqual(result.error, "argument 'name' must be string, got int")

    def test_destructive_tool_is_denied_without_calling_handler(self) -> None:
        calls: list[JsonObject] = []
        registry = ToolRegistry()
        registry.register(
            Tool("erase", "Erase", _schema(), lambda args: calls.append(args) or "erased", "destructive")
        )

        result = registry.execute("erase", {"name": "data"})

        self.assertTrue(result.denied)
        self.assertEqual(result.error, "denied: destructive tools are disabled in the baseline")
        self.assertEqual(calls, [])

    def test_handler_exception_becomes_error_data(self) -> None:
        def fail(_: JsonObject) -> str:
            raise OSError(5, "boom")

        registry = ToolRegistry()
        registry.register(Tool("fail", "Fail", _schema(), fail, "read"))

        result = registry.execute("fail", {"name": "x"})

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "OSError: [Errno 5] boom")

    def test_openai_tools_has_function_shape_and_allowed_filter(self) -> None:
        registry = ToolRegistry()
        registry.register(Tool("lookup", "Lookup", _schema(), lambda _: "ok", "read"))

        tools = registry.openai_tools(allowed=["lookup"])

        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["function"]["name"], "lookup")
        self.assertEqual(registry.openai_tools(allowed=[]), [])


class MockWorldTest(unittest.TestCase):
    def test_scenarios_have_expected_health_and_crashloop_evidence(self) -> None:
        world = MockWorld()

        self.assertFalse(world.services["api"]["healthy"])
        world.scenario("all_healthy")
        self.assertTrue(all(service["healthy"] for service in world.services.values()))
        world.scenario("web_crashloop")
        self.assertEqual(world.containers["web-1"]["status"], "restarting")
        self.assertEqual(world.containers["web-1"]["restarts"], 5)
        self.assertTrue(any("OOM" in line for line in world.containers["web-1"]["logs"]))

    def test_restart_repairs_api_down_but_not_web_crashloop_health(self) -> None:
        world = MockWorld()
        registry = build_registry(world)

        api_result = registry.execute("restart_container", {"name": "api-1"})
        self.assertTrue(api_result.ok)
        self.assertTrue(world.services["api"]["healthy"])
        self.assertEqual(world.containers["api-1"]["restarts"], 1)

        world.scenario("web_crashloop")
        web_result = registry.execute("restart_container", {"name": "web-1"})
        self.assertTrue(web_result.ok)
        self.assertFalse(world.services["web"]["healthy"])
        self.assertEqual(json.loads(web_result.output)["status"], "running")


if __name__ == "__main__":
    unittest.main()
