from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from blackout_ops_kb.compression import compress_events
from blackout_ops_kb.evidence import load_evidence


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets-root", default="datasets/raw")
    parser.add_argument("--artifacts", default="blackout_ops_kb/artifacts")
    parser.add_argument("--max-events", type=int, default=50)
    parser.add_argument("--out", default="blackout_ops_kb/artifacts/dataset_validation.json")
    args = parser.parse_args()

    artifacts = Path(args.artifacts)
    truths = read_jsonl(artifacts / "eval_ground_truth.jsonl")
    eval_inputs = read_jsonl(artifacts / "cases_eval.jsonl")
    train = read_jsonl(artifacts / "cases_train.jsonl")
    dev = read_jsonl(artifacts / "cases_dev.jsonl")

    selected: list[dict[str, Any]] = []
    for source in (
        "Cloud-OpsBench",
        "AgenticOpsEval/AIOps2025",
        "AgenticOpsEval/RCA100",
        "RCAEval",
    ):
        selected.append(next(item for item in truths if item["source"] == source))
    selected.append(
        next(
            item
            for item in train + dev
            if item["source"] == "ITBench-Lite/SRE"
            and item.get("metadata", {}).get("full_snapshot_downloaded")
        )
    )

    source_results = []
    errors: list[str] = []
    for record in selected:
        try:
            bundle = load_evidence(record, args.datasets_root, max_events=args.max_events)
            compressed = compress_events(bundle["events"], target_ratio=4.0, keywords=["error", "failed", "timeout"])
            source_results.append(
                {
                    "source": record["source"],
                    "id": record["id"],
                    "events": len(bundle["events"]),
                    "event_kinds": dict(Counter(event["kind"] for event in bundle["events"])),
                    "compression": compressed["metrics"],
                    "status": "pass",
                }
            )
        except Exception as exc:  # validation report must capture adapter failures
            errors.append(f"{record['source']}: {exc}")
            source_results.append({"source": record["source"], "id": record["id"], "status": "fail", "error": str(exc)})

    forbidden_eval_keys = {"labels", "source_path", "risk_tier", "scenario", "tags"}
    eval_key_leaks = [
        {"id": item.get("id"), "keys": sorted(forbidden_eval_keys.intersection(item))}
        for item in eval_inputs
        if forbidden_eval_keys.intersection(item)
    ]
    connection = sqlite3.connect(artifacts / "blackout_ops_kb.sqlite")
    forbidden_rows = connection.execute(
        "SELECT COUNT(1) FROM documents WHERE split NOT IN (?, ?)", ("train", "knowledge")
    ).fetchone()[0]
    indexed = dict(connection.execute("SELECT doc_type, COUNT(1) FROM documents GROUP BY doc_type"))
    connection.close()
    report = {
        "status": "pass" if not errors and not eval_key_leaks and forbidden_rows == 0 else "fail",
        "normalized_cases": len(train) + len(dev) + len(eval_inputs),
        "split_counts": {"train": len(train), "dev": len(dev), "eval": len(eval_inputs), "eval_truth": len(truths)},
        "source_adapters": source_results,
        "eval_agent_visible_key_leaks": eval_key_leaks,
        "forbidden_dev_eval_rows_in_search_db": forbidden_rows,
        "indexed_documents": indexed,
        "errors": errors,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
