"""Contract tests against the real SDK and deterministic HTTP mock."""

from __future__ import annotations

import json
import os
import unittest
from typing import ClassVar

from blackbox.inference import InferenceGateway, mock_server
from blackbox.inference.schemas import JsonObject

TOOLS: list[JsonObject] = [
    {
        "type": "function",
        "function": {
            "name": "get_service_health",
            "description": "Get service health",
            "parameters": {
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        },
    }
]


class GatewayTest(unittest.TestCase):
    """Exercise the public gateway through an OpenAI-compatible HTTP surface."""

    gateway: ClassVar[InferenceGateway]

    @classmethod
    def setUpClass(cls) -> None:
        cls._old_base_url = os.environ.get("BLACKBOX_INFERENCE_BASE_URL")
        cls.server, cls.thread, cls.base_url = mock_server.serve(port=0)
        os.environ["BLACKBOX_INFERENCE_BASE_URL"] = cls.base_url
        cls.gateway = InferenceGateway()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        if cls._old_base_url is None:
            os.environ.pop("BLACKBOX_INFERENCE_BASE_URL", None)
        else:
            os.environ["BLACKBOX_INFERENCE_BASE_URL"] = cls._old_base_url

    def test_plain_chat_returns_exact_string(self) -> None:
        result = self.gateway.reason(
            [{"role": "user", "content": "BLACKBOX_BOLT_OK"}],
            reasoning_effort="none",
        )

        self.assertEqual(result.content, "BLACKBOX_BOLT_OK")

    def test_json_response_format_round_trips(self) -> None:
        prompt = "Return JSON for this request"
        result = self.gateway.reason(
            [{"role": "user", "content": prompt}],
            reasoning_effort="none",
            response_format={"type": "json_object"},
        )

        self.assertEqual(json.loads(result.content), {"status": "ok", "echo": prompt})
        self.assertEqual(mock_server.LAST_REQUEST_BODY["response_format"], {"type": "json_object"})

    def test_reasoning_effort_maps_to_template_kwargs_and_defaults(self) -> None:
        cases = {
            "none": ({"enable_thinking": False}, 1024),
            "low": ({"enable_thinking": False}, 1024),
            "medium": ({"enable_thinking": True, "medium_effort": True}, 4096),
            "high": ({"enable_thinking": True}, 8192),
        }
        for effort, (expected_kwargs, expected_tokens) in cases.items():
            with self.subTest(effort=effort):
                result = self.gateway.reason([{"role": "user", "content": "hello"}], reasoning_effort=effort)
                self.assertEqual(mock_server.LAST_REQUEST_BODY["chat_template_kwargs"], expected_kwargs)
                self.assertEqual(mock_server.LAST_REQUEST_BODY["max_tokens"], expected_tokens)
                self.assertEqual(result.reasoning is not None, expected_kwargs["enable_thinking"])

    def test_max_tokens_marks_truncation(self) -> None:
        result = self.gateway.reason(
            [{"role": "user", "content": "a deliberately long answer"}],
            reasoning_effort="none",
            max_tokens=8,
        )

        self.assertTrue(result.truncated)
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(len(result.content), 8)

    def test_reasoning_only_is_reported(self) -> None:
        result = self.gateway.reason(
            [{"role": "user", "content": "answer"}],
            reasoning_effort="high",
            max_tokens=0,
        )

        self.assertTrue(result.reasoning_only)
        self.assertEqual(result.content, "")

    def test_tool_call_is_normalized(self) -> None:
        result = self.gateway.reason(
            [{"role": "user", "content": "check service api"}],
            tools=TOOLS,
            reasoning_effort="none",
        )

        self.assertEqual(result.tool_calls[0].name, "get_service_health")
        self.assertEqual(result.tool_calls[0].arguments, {"service": "api"})

    def test_xml_tool_call_is_salvaged_and_removed_from_content(self) -> None:
        result = self.gateway.reason(
            [{"role": "user", "content": "MOCK_SALVAGE service api"}],
            tools=TOOLS,
            reasoning_effort="none",
        )

        self.assertEqual(result.content, "")
        self.assertEqual(result.tool_calls[0].name, "get_service_health")
        self.assertEqual(result.tool_calls[0].arguments, {"service": "api"})
        raw = result.tool_calls[0].raw
        if raw is None:
            self.fail("salvaged calls must retain their raw block")
        self.assertIn("<tool_call>", raw)

    def test_full_tool_cycle_returns_final_content(self) -> None:
        messages: list[JsonObject] = [{"role": "user", "content": "check service api"}]
        first = self.gateway.reason(messages, tools=TOOLS, reasoning_effort="none")
        call = first.tool_calls[0]
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": "healthy",
                },
            ]
        )

        result = self.gateway.reason(messages, tools=TOOLS, reasoning_effort="none")

        self.assertEqual(result.content, "FINAL: get_service_health says healthy")
        self.assertEqual(result.tool_calls, [])

    def test_health_reports_ok(self) -> None:
        status = self.gateway.health()

        self.assertTrue(status.ok)
        self.assertEqual(status.status, 200)
        self.assertIsNone(status.error)

    def test_models_lists_served_model(self) -> None:
        self.assertEqual(self.gateway.models(), ["bolt"])

    def test_invalid_effort_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.gateway.reason([{"role": "user", "content": "hello"}], reasoning_effort="maximum")

    def test_connection_refused_health_is_data_not_exception(self) -> None:
        status = InferenceGateway(base_url="http://127.0.0.1:1").health()

        self.assertFalse(status.ok)
        self.assertIsNone(status.status)
        self.assertIsNotNone(status.error)


if __name__ == "__main__":
    unittest.main()
