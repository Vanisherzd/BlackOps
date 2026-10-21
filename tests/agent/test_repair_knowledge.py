"""RAG-only hard failures: no model calls, fault injection, subprocess execution, or filesystem cleanup."""
import importlib.util
import json
import os
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "kb" / "src")]
import repair_knowledge
from blackout_ops_kb.retrieval_v2 import AGENT_DEFAULTS, KnowledgeBaseV2


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepairKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evaluator = load("hard_repair_eval_under_test", ROOT / "scripts" / "eval-hard-repairs.py")
        cls.agent = load("hard_repair_agent_under_test", ROOT / "scripts" / "agent.py")
        cls.docs = repair_knowledge.documents()
        cls.kb = KnowledgeBaseV2(ROOT / "kb/artifacts/corpus_v3.jsonl", extra_documents=cls.docs, **AGENT_DEFAULTS)
        cls.inputs = cls.evaluator.rows(cls.evaluator.DATA / "eval_inputs.jsonl")

    def test_variants_retrieve_correct_card_without_evaluation_answers(self):
        report = self.evaluator.evaluate()
        self.assertTrue(report["eval_answers_isolated"])
        for row in report["results"]:
            with self.subTest(version=row["version"]):
                self.assertEqual(row["correct"], row["total"])
                self.assertEqual(row["false_hard_matches"], [])

    def test_every_repair_call_uses_actual_tool_schema_and_target(self):
        self.assertGreater(self.evaluator.validate_cards(self.docs), 30)

    def test_loader_reads_only_the_runtime_knowledge_file(self):
        data = repair_knowledge.KNOWLEDGE.read_text(encoding="utf-8")
        path = Mock()
        path.read_text.return_value = data
        with patch.object(repair_knowledge, "KNOWLEDGE", path):
            docs = repair_knowledge.documents()
        path.read_text.assert_called_once_with(encoding="utf-8")
        self.assertEqual(len(docs), len(self.docs))

    def test_extension_does_not_duplicate_documents(self):
        kb = KnowledgeBaseV2(ROOT / "kb/artifacts/corpus_v3.jsonl", extra_documents=self.docs + self.docs, **AGENT_DEFAULTS)
        ids = [d["id"] for d in kb.docs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_new_knowledge_is_enabled_in_default_agent_rag(self):
        with patch.object(self.agent, "_kb", None), patch.dict(os.environ, {"KB_RETRIEVAL": "v3", "OPS_HARD_REPAIR_KNOWLEDGE": "1"}):
            kb = self.agent.knowledge_base()
        self.assertEqual(sum(d["id"].startswith("hard-repair-") for d in kb.docs), len(self.docs))

    def test_ablation_disables_only_extra_knowledge(self):
        with patch.object(self.agent, "_kb", None), patch.dict(os.environ, {"KB_RETRIEVAL": "v3", "OPS_HARD_REPAIR_KNOWLEDGE": "0"}):
            kb = self.agent.knowledge_base()
        self.assertFalse(any(d["id"].startswith("hard-repair-") for d in kb.docs))
        self.assertTrue(kb.retrieve({"query": "No space left on device"})["cases"])

    def test_missing_extra_file_keeps_existing_rag_working(self):
        with patch.object(self.agent, "_kb", None), patch.dict(os.environ, {"KB_RETRIEVAL": "v3", "OPS_HARD_REPAIR_KNOWLEDGE": "1"}), patch.object(repair_knowledge, "documents", side_effect=FileNotFoundError("missing optional knowledge")), patch("sys.stderr"):
            kb = self.agent.knowledge_base()
        self.assertEqual(kb.retrieve({"query": "No space left on device"})["knowledge_match"]["status"], "matched")

    def test_full_tool_result_keeps_conditions_verification_and_source(self):
        for row in self.inputs:
            with self.subTest(incident=row["id"]), patch.object(self.agent, "knowledge_base", return_value=self.kb):
                result = self.agent.search_incident_knowledge(row["observation"])
            self.assertIn('"tool": "recreate_stack"', result)
            self.assertIn("ONLY if", result)
            self.assertIn("ROLLBACK:", result)
            self.assertIn("VERIFY:", result)
            self.assertIn("ESCALATE:", result)
            self.assertIn("Sources:", result)
            self.assertNotIn("...(cut)", result)

    def test_unqualified_hard_candidate_does_not_replace_another_match(self):
        # Existing RAG may match an unrelated generic card. A zero-coverage hard candidate must never hijack it.
        with patch.object(self.agent, "knowledge_base", return_value=self.kb):
            text = self.agent.search_incident_knowledge("SSH permission denied publickey on a new machine")
        self.assertNotIn("retrieved hard-failure procedure", text)

    def test_brief_note_prompts_retrieval_without_including_tool_path(self):
        with patch.object(self.agent, "knowledge_base", return_value=self.kb):
            text = self.agent.knowledge_note(self.inputs[0]["observation"])
        self.assertIn("search_incident_knowledge", text)
        self.assertNotIn('"tool": "recreate_stack"', text)

    def test_log_ownership_reaches_knowledge_lookup_without_changing_runbook_query(self):
        rag = Mock()
        rag.rank.return_value = [("example.md", 1)]
        rag.documents.return_value = [("example.md", "title", "procedure")]
        with patch.dict(sys.modules, {"rag": rag}), patch.object(self.agent, "knowledge_note", return_value="") as note, patch.dict(self.agent._incident, runbook_seen=False):
            self.agent.attach_runbook("upstream log", "unexpected end of file", container="edge-victim-app")
        rag.rank.assert_called_once_with("unexpected end of file")
        note.assert_called_once_with("edge-victim-app unexpected end of file")

    def test_retrieval_never_executes_the_returned_calls(self):
        with patch.object(self.agent, "knowledge_base", return_value=self.kb), patch.object(self.agent, "sh", side_effect=AssertionError("RAG executed a command")) as command, patch.object(self.agent, "_run_tool", side_effect=AssertionError("RAG executed a repair")) as dispatch:
            text = self.agent.search_incident_knowledge(self.inputs[0]["observation"])
        self.assertIn("TOOL PATH", text)
        command.assert_not_called()
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
