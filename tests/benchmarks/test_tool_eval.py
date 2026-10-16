from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from blackbox.benchmarks import tool_eval
from blackbox.inference import InferenceGateway, mock_server
from blackbox.inference.schemas import JsonObject


class ToolEvalTest(unittest.TestCase):
    gateway: ClassVar[InferenceGateway]

    @classmethod
    def setUpClass(cls) -> None:
        cls.server, cls.thread, base_url = mock_server.serve(port=0)
        cls.gateway = InferenceGateway(base_url=base_url, model="bolt")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_run_reports_all_metrics_and_markdown_table(self) -> None:
        # Given
        cases = (tool_eval.CASES[0], tool_eval.CASES[2], tool_eval.CASES[9])

        # When
        payload = tool_eval.run(self.gateway, cases=cases, reasoning_effort="none")

        # Then
        metrics = payload.get("metrics")
        self.assertIsInstance(metrics, dict)
        if not isinstance(metrics, dict):
            return
        expected = {
            "tool_selection_accuracy",
            "valid_tool_call_rate",
            "invalid_argument_rate",
            "unnecessary_tool_rate",
            "completion_rate",
            "median_turns",
            "median_latency_ms",
            "categories",
            "salvaged_tool_calls",
        }
        self.assertEqual(set(metrics), expected)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "tool-eval.md"
            tool_eval.write_markdown(destination, payload)
            markdown = destination.read_text(encoding="utf-8")
        self.assertIn("| Metric | Value |", markdown)
        self.assertIn("| Category | Passed | Total | Pass rate |", markdown)


class TranscriptScoringTest(unittest.TestCase):
    def test_write_without_following_read_fails_verification(self) -> None:
        # Given
        case = next(
            item
            for item in tool_eval.CASES
            if item.category == "verification_after_action"
        )
        transcript: list[JsonObject] = [
            {"role": "user", "content": case.prompt},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "write-1",
                        "type": "function",
                        "function": {
                            "name": "restart_container",
                            "arguments": '{"name":"api-1"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "write-1",
                "name": "restart_container",
                "content": '{"name":"api-1","status":"running"}',
            },
            {"role": "assistant", "content": "Success, the API is fixed."},
        ]

        # When
        passed, reasons = tool_eval.score_transcript(case, transcript)

        # Then
        self.assertFalse(passed)
        self.assertTrue(any("followed by a read" in reason for reason in reasons))

    def test_unknown_tool_is_detected(self) -> None:
        # Given
        case = next(
            item
            for item in tool_eval.CASES
            if item.category == "unknown_tool_avoidance"
        )
        transcript: list[JsonObject] = [
            {"role": "user", "content": case.prompt},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "unknown-1",
                        "type": "function",
                        "function": {
                            "name": "scale_deployment",
                            "arguments": '{"replicas":3}',
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "Scaling completed."},
        ]

        # When
        passed, reasons = tool_eval.score_transcript(case, transcript)

        # Then
        self.assertFalse(passed)
        self.assertTrue(any("unregistered tool" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
