from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar

from blackbox.benchmarks import perf_baseline
from blackbox.benchmarks._evalutil import parse_startup_time
from blackbox.inference import InferenceGateway, mock_server


class StartupLogTest(unittest.TestCase):
    def test_startup_time_uses_vllm_log_timestamps(self) -> None:
        # Given
        with TemporaryDirectory() as temporary:
            log = Path(temporary) / "startup.log"
            log.write_text(
                "INFO 10-16 08:00:00 starting server\n"
                "INFO 10-16 08:00:12 Application startup complete\n",
                encoding="utf-8",
            )

            # When
            startup_time = parse_startup_time(log)

        # Then
        self.assertEqual(startup_time, 12.0)


class PerfBaselineTest(unittest.TestCase):
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

    def test_non_streaming_mock_exercises_aggregate_path(self) -> None:
        # Given
        options = {
            "requests": 2,
            "concurrency": 1,
            "prompt_tokens_target": 32,
            "max_tokens": 32,
        }

        # When
        payload = perf_baseline.run(self.gateway, **options)

        # Then
        self.assertFalse(payload["streaming"])
        requests = payload["requests"]
        self.assertIsInstance(requests, list)
        if not isinstance(requests, list):
            return
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request["ttft_ms"] is None for request in requests))
        aggregate = payload["aggregate"]
        self.assertIsInstance(aggregate, dict)
        if isinstance(aggregate, dict):
            self.assertEqual(aggregate["request_count"], 2)
            self.assertIsNone(aggregate["ttft_ms"]["median"])


if __name__ == "__main__":
    unittest.main()
