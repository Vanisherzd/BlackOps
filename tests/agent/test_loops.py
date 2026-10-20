"""Tool and incident loops exercised through the real in-process HTTP mock."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping, Sequence
from typing import ClassVar

from blackbox.agent.agent_loop import run_incident
from blackbox.agent.tool_loop import run_tool_loop
from blackbox.inference import InferenceGateway, mock_server
from blackbox.inference.schemas import JsonValue, ReasonResult, ToolCall, Usage
from blackbox.tools.mock_tools import MockWorld, build_registry


class LoopTest(unittest.TestCase):
    server: ClassVar
    thread: ClassVar
    gateway: ClassVar[InferenceGateway]

    @classmethod
    def setUpClass(cls) -> None:
        cls.server, cls.thread, base_url = mock_server.serve(port=0)
        cls.gateway = InferenceGateway(base_url=base_url)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_tool_loop_executes_call_appends_tool_message_and_finishes(self) -> None:
        result = run_tool_loop(
            self.gateway,
            build_registry(),
            [{"role": "user", "content": "check service api"}],
            reasoning_effort="none",
        )

        self.assertEqual(result.stopped_reason, "final_answer")
        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual([message["role"] for message in result.messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(result.messages[2]["name"], "get_service_health")
        self.assertFalse(json.loads(result.messages[2]["content"])["healthy"])
        self.assertIsNotNone(result.final)

    def test_repeat_guard_stops_third_identical_call(self) -> None:
        result = run_tool_loop(
            RepeatingGateway(),
            build_registry(),
            [{"role": "user", "content": "check service api"}],
            reasoning_effort="none",
        )

        self.assertEqual(result.stopped_reason, "error")
        self.assertEqual(result.turns, 3)
        self.assertEqual(result.tool_calls, 3)
        self.assertEqual(result.errors, 1)
        self.assertIn("repeat guard", result.messages[-1]["content"])

    def test_incident_finishes_resolved_against_scripted_server(self) -> None:
        world = MockWorld().scenario("api_down")

        state = run_incident(
            "check service api",
            self.gateway,
            build_registry(world),
            reasoning_effort="none",
        )

        self.assertEqual(state.status, "resolved")
        self.assertEqual(state.turns, 2)
        self.assertEqual([result["tool"] for result in state.tool_results], ["get_service_health", "finish"])
        self.assertTrue(any('"healthy":false' in observation for observation in state.observations))

    def test_verify_hook_turns_unverified_finish_into_failure(self) -> None:
        world = MockWorld().scenario("api_down")

        state = run_incident(
            "check service api",
            self.gateway,
            build_registry(world),
            reasoning_effort="none",
            verify=lambda _state, verified_world: bool(
                verified_world and verified_world.services["api"]["healthy"]
            ),
        )

        self.assertEqual(state.status, "failed")
        self.assertIn("not verified", state.summary or "")


class RepeatingGateway:
    """Emit one stable call so the repeat guard is the only stopping condition."""

    def reason(
        self,
        messages: Sequence[Mapping[str, JsonValue]],
        tools: Sequence[Mapping[str, JsonValue]] | None = None,
        reasoning_effort: str = "medium",
    ) -> ReasonResult:
        return ReasonResult(
            content="",
            reasoning=None,
            tool_calls=[ToolCall("repeat", "get_service_health", {"service": "api"})],
            finish_reason="tool_calls",
            truncated=False,
            reasoning_only=False,
            usage=Usage(0, 0, 0),
            latency_ms=0,
            model="fake",
            raw={},
        )


if __name__ == "__main__":
    unittest.main()
