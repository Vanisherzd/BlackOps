import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "prefix-cache-stats.py"
SPEC = importlib.util.spec_from_file_location("prefix_cache_stats", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
prefix_cache_stats = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prefix_cache_stats)


class PrefixCacheStatsTest(unittest.TestCase):
    def test_parse_metrics_sums_labels_and_prefers_current_names(self):
        # Given current and legacy metrics with labels, comments, and scientific notation.
        metrics = """
# HELP vllm:prefix_cache_queries_total Prefix-cache queries.
vllm:prefix_cache_queries_total{model_name="a"} 1e2
vllm:prefix_cache_queries_total{model_name="b"} 5.0E1
vllm:prefix_cache_queries{model_name="legacy"} 999
vllm:prefix_cache_hits_total{model_name="a"} 60
vllm:prefix_cache_hits_total{model_name="b"} 1.5e1
vllm:prefix_cache_hits{model_name="legacy"} 888
vllm:prompt_tokens_total{model_name="a"} 2.5e2
vllm:generation_tokens_total 40
vllm:gpu_cache_usage_perc{model_name="a"} 0.75
"""

        # When the exposition is parsed.
        parsed = prefix_cache_stats.parse_metrics(metrics)

        # Then label series are summed and current counter names win over aliases.
        self.assertEqual(parsed, {
            "queries": 150.0,
            "hits": 75.0,
            "prompt_tokens": 250.0,
            "generation_tokens": 40.0,
            "kv_cache_usage": 0.75,
        })

    def test_parse_metrics_falls_back_to_legacy_names(self):
        # Given only the legacy prefix-cache counter names and the current KV gauge.
        metrics = """
vllm:prefix_cache_queries{model_name="a"} 20
vllm:prefix_cache_hits{model_name="a"} 5
vllm:kv_cache_usage_perc{model_name="a"} 0.5
"""

        # When the exposition is parsed, then aliases and missing metrics are handled.
        self.assertEqual(prefix_cache_stats.parse_metrics(metrics), {
            "queries": 20.0,
            "hits": 5.0,
            "prompt_tokens": None,
            "generation_tokens": None,
            "kv_cache_usage": 0.5,
        })

    def test_diff_snapshots_computes_window_ratio(self):
        # Given two cumulative counter snapshots.
        before = {"queries": 100.0, "hits": 25.0, "prompt_tokens": 400.0,
                  "generation_tokens": 80.0, "kv_cache_usage": 0.4}
        after = {"queries": 150.0, "hits": 65.0, "prompt_tokens": 520.0,
                 "generation_tokens": 110.0, "kv_cache_usage": 0.6}

        # When the window statistics are calculated.
        diff = prefix_cache_stats.diff_snapshots(before, after)

        # Then the ratio uses counter deltas, not lifetime totals.
        self.assertEqual(diff["hits"], 40.0)
        self.assertEqual(diff["queries"], 50.0)
        self.assertEqual(diff["hit_ratio"], 80.0)


if __name__ == "__main__":
    unittest.main()
