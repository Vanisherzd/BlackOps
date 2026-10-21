import importlib.util
import math
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "kb_rerank_eval_under_test", ROOT / "scripts" / "kb-rerank-eval.py"
)
assert SPEC and SPEC.loader
kb_rerank_eval = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kb_rerank_eval)


class RankMetricsTests(unittest.TestCase):
    def test_first_rank_match_has_perfect_mrr_and_ndcg(self):
        metrics = kb_rerank_eval.rank_metrics("target", ["target", "other"])

        self.assertEqual(metrics["category_mrr"], 1.0)
        self.assertEqual(metrics["category_ndcg_at_5"], 1.0)

    def test_rank_three_match_has_one_third_mrr(self):
        metrics = kb_rerank_eval.rank_metrics("target", ["other", "other", "target"])

        self.assertAlmostEqual(metrics["category_mrr"], 1 / 3)
        self.assertEqual(metrics["category_recall_at_5"], 1.0)
        self.assertEqual(metrics["category_recall_at_10"], 1.0)

    def test_recall_distinguishes_top_five_from_top_ten(self):
        metrics = kb_rerank_eval.rank_metrics(
            "target", ["other"] * 6 + ["target"]
        )

        self.assertEqual(metrics["category_recall_at_5"], 0.0)
        self.assertEqual(metrics["category_recall_at_10"], 1.0)

    def test_no_match_has_zero_metrics(self):
        metrics = kb_rerank_eval.rank_metrics("target", ["other", "other"])

        self.assertEqual(metrics, {
            "category_mrr": 0.0,
            "category_ndcg_at_5": 0.0,
            "category_recall_at_5": 0.0,
            "category_recall_at_10": 0.0,
        })

    def test_ndcg_uses_ideal_dcg_for_two_matches(self):
        metrics = kb_rerank_eval.rank_metrics(
            "target", ["other", "target", "other", "target", "other"]
        )
        expected = (1 / math.log2(3) + 1 / math.log2(5)) / (
            1 + 1 / math.log2(3)
        )

        self.assertAlmostEqual(metrics["category_ndcg_at_5"], expected)

    def test_aggregator_means_per_query_metrics(self):
        first = kb_rerank_eval.rank_metrics("target", ["target"])
        second = kb_rerank_eval.rank_metrics("target", ["other"])

        metrics = kb_rerank_eval.aggregate_rank_metrics([first, second])

        self.assertEqual(metrics, {
            "category_mrr": 0.5,
            "category_ndcg_at_5": 0.5,
            "category_recall_at_5": 0.5,
            "category_recall_at_10": 0.5,
        })


class PercentileTests(unittest.TestCase):
    def test_percentile_interpolates_and_handles_boundaries(self):
        values = [40.0, 10.0, 30.0, 20.0]

        self.assertEqual(kb_rerank_eval.percentile(values, 0), 10.0)
        self.assertEqual(kb_rerank_eval.percentile(values, 50), 25.0)
        self.assertAlmostEqual(kb_rerank_eval.percentile(values, 95), 38.5)
        self.assertEqual(kb_rerank_eval.percentile(values, 100), 40.0)

    def test_percentile_handles_empty_and_singleton_inputs(self):
        self.assertEqual(kb_rerank_eval.percentile([], 95), 0.0)
        self.assertEqual(kb_rerank_eval.percentile([7.5], 50), 7.5)


if __name__ == "__main__":
    unittest.main()
