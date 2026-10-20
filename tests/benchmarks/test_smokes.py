from __future__ import annotations

import unittest
from typing import ClassVar

from blackbox.benchmarks import (
    concurrency_smoke,
    inference_smoke,
    multi_turn_smoke,
    tool_call_smoke,
)
from blackbox.inference import InferenceGateway, mock_server
from blackbox.inference.schemas import JsonObject


class SmokeBenchmarkTest(unittest.TestCase):
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

    def test_inference_smoke_passes(self) -> None:
        payload = inference_smoke.run(self.gateway)

        self._assert_checks_pass(payload)

    def test_tool_call_smoke_passes(self) -> None:
        payload = tool_call_smoke.run(self.gateway)

        self._assert_checks_pass(payload)

    def test_multi_turn_smoke_passes(self) -> None:
        payload = multi_turn_smoke.run(self.gateway)

        self._assert_checks_pass(payload)

    def test_concurrency_smoke_passes_with_two_requests(self) -> None:
        payload = concurrency_smoke.run(self.gateway, n=2)

        self._assert_checks_pass(payload)

    def _assert_checks_pass(self, payload: JsonObject) -> None:
        results = payload.get("results")
        self.assertIsInstance(results, list)
        if not isinstance(results, list):
            return
        self.assertTrue(results)
        self.assertTrue(
            all(
                isinstance(check, dict) and check.get("passed") is True
                for check in results
            )
        )
        self.assertTrue(
            all(
                isinstance(check, dict) and "latency_ms" in check
                for check in results
            )
        )
        self.assertTrue(
            all(isinstance(check, dict) and "usage" in check for check in results)
        )


if __name__ == "__main__":
    unittest.main()
