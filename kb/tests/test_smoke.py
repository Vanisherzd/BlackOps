from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from blackout_ops_kb import KnowledgeBase, default_database_path, infer_scenario
from blackout_ops_kb.compression import compress_events
from blackout_ops_kb.evaluation import score_predictions
from blackout_ops_kb.rag import RAG_SCHEMA, export_rag_corpus


class ScenarioTests(unittest.TestCase):
    def test_scenario_detection(self) -> None:
        self.assertEqual(infer_scenario("no space left on device"), "disk_full")
        self.assertEqual(infer_scenario("CrashLoopBackOff OOMKilled"), "crashloop")
        self.assertEqual(infer_scenario("DNS lookup: no such host"), "dns")
        self.assertEqual(infer_scenario("節點磁碟空間不足"), "disk_full")
        self.assertEqual(infer_scenario("容器反覆重啟"), "crashloop")
        self.assertEqual(infer_scenario("服務名稱解析失敗"), "dns")


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = Path(__file__).parents[1] / "artifacts" / "blackout_ops_kb.sqlite"
        if not cls.database.exists():
            raise unittest.SkipTest("build artifact first")
        cls.kb = KnowledgeBase(cls.database)

    def test_crashloop_finds_solution(self) -> None:
        results = self.kb.search(
            "CrashLoopBackOff executable file not found exit code 127",
            scenario="crashloop",
            doc_type="solution",
            top_k=3,
        )
        self.assertTrue(results)
        self.assertIn("invalid-command", results[0]["id"])

    def test_dns_bundle_has_case_and_solution(self) -> None:
        bundle = self.kb.retrieve({"log": "DNS lookup no such host", "platform": "kubernetes"})
        self.assertEqual(bundle["scenario"], "dns")
        self.assertTrue(bundle["cases"])
        self.assertTrue(bundle["solutions"])

    def test_result_policy_is_plan_only(self) -> None:
        bundle = self.kb.retrieve({"status": "CrashLoopBackOff", "reason": "OOMKilled"})
        self.assertEqual(bundle["policy"]["mode"], "plan_only")

    def test_linux_systemd_runbook_is_retrievable(self) -> None:
        bundle = self.kb.retrieve(
            {
                "platform": "linux",
                "resource": "checkout-api.service",
                "status": "failed",
                "log": "Start request repeated too quickly",
            }
        )
        self.assertEqual(bundle["knowledge_match"]["status"], "matched")
        self.assertTrue(bundle["solutions"])
        self.assertEqual(bundle["solutions"][0]["id"], "solution-linux-systemd-restart-loop")

    def test_linux_tls_runbook_is_retrievable(self) -> None:
        bundle = self.kb.retrieve({"platform": "linux", "log": "x509 certificate has expired TLS handshake"})
        self.assertEqual(bundle["knowledge_match"]["status"], "matched")
        self.assertEqual(bundle["solutions"][0]["id"], "solution-linux-tls-certificate-expired")

    def test_unknown_error_does_not_authorize_weak_solution(self) -> None:
        bundle = self.kb.retrieve({"platform": "linux", "log": "NVIDIA Xid 79 GPU has fallen off the bus"})
        self.assertEqual(bundle["knowledge_match"]["status"], "insufficient")
        self.assertEqual(bundle["solutions"], [])

    def test_topology_is_retrievable(self) -> None:
        results = self.kb.search("checkout calls payment", doc_type="topology", top_k=2)
        self.assertTrue(results)

    def test_eval_inputs_have_no_answer_keys(self) -> None:
        path = Path(__file__).parents[1] / "artifacts" / "cases_eval.jsonl"
        forbidden = {"labels", "scenario", "source_path", "tags", "risk_tier"}
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            self.assertFalse(forbidden.intersection(item))

    def test_portable_rag_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rag.jsonl"
            report = export_rag_corpus(self.database, output)
            self.assertEqual(report["documents"], 1668)
            self.assertFalse(report["contains_eval_answers"])
            first = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first["metadata"]["schema"], RAG_SCHEMA)
            self.assertEqual(set(first), {"id", "text", "metadata"})

    def test_installed_default_database_is_present(self) -> None:
        self.assertTrue(default_database_path().is_file())
        self.assertGreater(KnowledgeBase().search("disk full", top_k=1).__len__(), 0)


class CompressionTests(unittest.TestCase):
    def test_compression_collapses_duplicates_and_keeps_error(self) -> None:
        events = [
            {"kind": "log", "message": "GET /healthz 200", "timestamp": None, "service": "api"},
            *[
                {"kind": "log", "message": "connection refused 10.1.2.3", "timestamp": None, "service": "api"}
                for _ in range(20)
            ],
        ]
        result = compress_events(events, target_ratio=4, keywords=["connection refused"])
        self.assertGreater(result["metrics"]["actual_ratio"], 2)
        self.assertGreater(result["metrics"]["duplicates_collapsed"], 0)
        self.assertTrue(any("connection refused" in item["message"] for item in result["events"]))


class ScoringTests(unittest.TestCase):
    def test_compression_curve_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            truth = root / "truth.jsonl"
            predictions = root / "predictions.jsonl"
            curve = root / "curve.csv"
            truth.write_text('{"id":"e1","labels":["dns_error"],"scenario":"dns"}\n', encoding="utf-8")
            predictions.write_text(
                '{"id":"e1","compression_ratio":4,"predicted_label":"dns error","predicted_scenario":"dns","input_tokens":10}\n',
                encoding="utf-8",
            )
            report = score_predictions(truth, predictions, curve)
            self.assertEqual(report["curve"][0]["diagnostic_accuracy"], 1)
            self.assertTrue(curve.exists())


if __name__ == "__main__":
    unittest.main()
