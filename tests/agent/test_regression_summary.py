import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "regression_summary.py"


class RegressionSummaryTest(unittest.TestCase):
    def test_cli_aggregates_three_score_records_by_case(self):
        # Given three score records spanning two cases.
        records = [
            {"case": "bad-config", "tier": "seen", "outcome": "autonomous",
             "seconds": 10, "calls": 3, "cause_named": True, "false_claim": False},
            {"case": "bad-config", "tier": "seen", "outcome": "made-worse",
             "seconds": 20, "calls": 5, "cause_named": False, "false_claim": True},
            {"case": "upstream-oom", "tier": "blind", "outcome": "autonomous",
             "seconds": 31, "calls": 6, "cause_named": True, "false_claim": False},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "score.json"
            path.write_text(json.dumps(records))

            # When the compact summary CLI reads them.
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)],
                check=True,
                capture_output=True,
                text=True,
            )

        # Then each case has the requested aggregate columns.
        self.assertEqual(
            result.stdout,
            "| case | tier | runs | autonomous/runs | median seconds | median calls | cause named % | made-worse | false claims |\n"
            "|---|---|---:|---:|---:|---:|---:|---:|---:|\n"
            "| bad-config | seen | 2 | 1/2 | 15 | 4 | 50% | 1 | 1 |\n"
            "| upstream-oom | blind | 1 | 1/1 | 31 | 6 | 100% | 0 | 0 |\n",
        )


if __name__ == "__main__":
    unittest.main()
