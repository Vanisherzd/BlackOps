from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blackout_ops_kb import retrieval_v2 as v2


class TokenTests(unittest.TestCase):
    def test_generic_words_and_noise_are_dropped(self) -> None:
        self.assertEqual(v2.tokens("pod error timeout service request"), [])
        self.assertEqual(v2.tokens("trace 02ec126516f9b8101659749a635e39bc at 2025-06-06"), [])

    def test_compound_names_keep_whole_and_parts(self) -> None:
        got = v2.tokens("pod_cpu_usage on ts-route-service")
        self.assertIn("pod_cpu_usage", got)
        self.assertIn("cpu", got)
        self.assertIn("ts-route-service", got)
        self.assertIn("route", got)

    def test_word_boundaries_not_substrings(self) -> None:
        self.assertNotIn("err", v2.tokens("ferry terror"))


class ScenarioTests(unittest.TestCase):
    def test_io_stress_is_not_a_full_disk(self) -> None:
        self.assertEqual(v2.scenario_v2("disk_full", ["disk"]), "other")
        self.assertEqual(v2.scenario_v2("disk_full", ["io"]), "other")
        self.assertEqual(v2.scenario_v2("disk_full", ["node disk fill"]), "disk_full")
        self.assertEqual(v2.scenario_v2("disk_full", ["code_excessive_file_writes"]), "disk_full")

    def test_a_single_kill_is_not_a_crash_loop(self) -> None:
        self.assertEqual(v2.scenario_v2("crashloop", ["pod kill"]), "other")
        self.assertEqual(v2.scenario_v2("crashloop", ["pod failure"]), "other")
        self.assertEqual(v2.scenario_v2("crashloop", ["container_memory_limit_too_low"]), "crashloop")

    def test_dns_and_other_unchanged(self) -> None:
        self.assertEqual(v2.scenario_v2("dns", ["dns error"]), "dns")
        self.assertEqual(v2.scenario_v2("other", ["cpu stress"]), "other")


class EvidenceTests(unittest.TestCase):
    def test_label_only_case_has_no_evidence(self) -> None:
        self.assertFalse(v2.has_evidence("System: Train Ticket\nRoot cause service: carts\nInjected fault: loss"))
        self.assertFalse(v2.has_evidence("Symptom: Service Availability Disruption.\nRoot cause: x"))
        self.assertTrue(v2.has_evidence("Fault: cpu stress\nSymptoms: high CPU utilization"))


class QueryTests(unittest.TestCase):
    def test_infra_summary_yields_the_metric_name(self) -> None:
        events = [{"kind": "metric", "source_file": "infra", "service": "aiops-k8s-04",
                   "message": "node_disk_read_bytes_total aiops-k8s-04: baseline 10 -> window 5000, peak 9000 (x500)"}]
        self.assertIn("node_disk_read_bytes_total", v2.incident_query(events))

    def test_wide_rows_rank_the_column_that_moved(self) -> None:
        rows = [{"kind": "metrics", "message": f'{{"time": {t}, "carts_latency": {1 if t < 5 else 50}, "front_cpu": 1}}'}
                for t in range(10)]
        rows.append({"kind": "boundary", "message": "fault injection boundary unix=5"})
        q = v2.incident_query(rows)
        self.assertTrue(q.startswith("carts_latency"), q)


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database = Path(__file__).parents[1] / "artifacts" / "blackout_ops_kb.sqlite"
        if not database.exists():
            raise unittest.SkipTest("build artifact first")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.corpus = Path(cls.tmp.name) / "corpus_v2.jsonl"
        cls.stats = v2.build_v2_corpus(database, cls.corpus)
        cls.kb = v2.KnowledgeBaseV2(cls.corpus)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_duplicates_merged(self) -> None:
        self.assertGreater(self.stats["merged"], 0)
        self.assertEqual(self.stats["cases"] + self.stats["merged"], 1644)

    def test_generic_query_is_not_matched(self) -> None:
        b = self.kb.retrieve({"query": "redis-cart pod timeout error"})
        self.assertEqual(b["knowledge_match"]["status"], "insufficient")

    def test_specific_symptom_still_matches(self) -> None:
        b = self.kb.retrieve({"query": "write failed: No space left on device, filesystem 100% full, DiskPressure"},
                             minimum_solution_coverage=0.2)
        self.assertEqual(b["knowledge_match"]["status"], "matched")
        self.assertEqual(b["solutions"][0]["scenario"], "disk_full")

    def test_no_hard_filter_by_default(self) -> None:
        b = self.kb.retrieve({"query": "disk latency on carts"}, case_k=10)
        self.assertTrue(any(c["scenario"] != "disk_full" for c in b["cases"]))


class ObservedIndexTests(unittest.TestCase):
    """Step 2: a case indexed by its own telemetry is found by telemetry, not by its label text."""

    @classmethod
    def setUpClass(cls) -> None:
        database = Path(__file__).parents[1] / "artifacts" / "blackout_ops_kb.sqlite"
        if not database.exists():
            raise unittest.SkipTest("build artifact first")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.corpus = Path(cls.tmp.name) / "corpus_v3.jsonl"
        cls.target = "rcaeval-re1ob_adservice_delay_1"
        observed = {cls.target: "adservice_latency-90 zzqmarker_metric adservice_cpu"}
        cls.stats = v2.build_v2_corpus(database, cls.corpus, observed=observed)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_observed_text_is_attached(self) -> None:
        self.assertEqual(self.stats.get("observed"), 1)

    def test_observed_index_finds_case_by_telemetry(self) -> None:
        kb = v2.KnowledgeBaseV2(self.corpus, index="observed")
        top = kb.search("zzqmarker_metric", doc_type="case", top_k=1)
        self.assertTrue(top and top[0]["id"] == self.target, top[:1])

    def test_original_index_does_not(self) -> None:
        kb = v2.KnowledgeBaseV2(self.corpus, index="original")
        self.assertEqual(kb.search("zzqmarker_metric", doc_type="case", top_k=1), [])

    def test_vote_needs_agreeing_neighbours(self) -> None:
        kb = v2.KnowledgeBaseV2(self.corpus, index="observed", vote=5)
        b = kb.retrieve({"query": "zzqmarker_metric"})
        self.assertEqual(b["knowledge_match"]["status"], "insufficient")


class AgentDefaultsTests(unittest.TestCase):
    """The configuration the agent ships with: typed errors still reach a card, generic words and telemetry do not."""

    @classmethod
    def setUpClass(cls) -> None:
        corpus = Path(__file__).parents[1] / "artifacts" / "corpus_v3.jsonl"
        if not corpus.exists():
            raise unittest.SkipTest("corpus_v3.jsonl not built")
        cls.kb = v2.KnowledgeBaseV2(corpus, **v2.AGENT_DEFAULTS)

    def test_specific_typed_error_matches_its_card(self) -> None:
        b = self.kb.retrieve({"query": "write error: No space left on device, / at 97%"})
        self.assertEqual(b["knowledge_match"]["status"], "matched")
        self.assertTrue(b["solutions"][0]["id"].startswith("solution-linux-disk"))

    def test_generic_phrase_does_not(self) -> None:
        b = self.kb.retrieve({"query": "upstream connect error connection refused"})
        self.assertEqual(b["knowledge_match"]["status"], "insufficient")

    def test_typed_query_still_finds_cases(self) -> None:
        self.assertTrue(self.kb.retrieve({"query": "No space left on device"})["cases"])


if __name__ == "__main__":
    unittest.main()
