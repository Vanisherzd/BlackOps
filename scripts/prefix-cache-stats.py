#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import assert_never

Snapshot = dict[str, float | None]
LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{.*\})?\s+([^\s]+)(?:\s+\d+)?$")
METRICS = {
    "queries": ("vllm:prefix_cache_queries_total", "vllm:prefix_cache_queries"),
    "hits": ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_hits"),
    "prompt_tokens": ("vllm:prompt_tokens_total",),
    "generation_tokens": ("vllm:generation_tokens_total",),
    "kv_cache_usage": ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"),
}


def parse_metrics(text: str) -> dict[str, float | None]:
    totals: dict[str, float] = {}
    for line in text.splitlines():
        match = LINE.match(line.strip())
        if match is None or line.lstrip().startswith("#"):
            continue
        name, value = match.groups()
        try:
            totals[name] = totals.get(name, 0.0) + float(value)
        except ValueError:
            continue
    return {
        key: next((totals[name] for name in names if name in totals), None)
        for key, names in METRICS.items()
    }


def take_snapshot(url: str) -> Snapshot:
    with urllib.request.urlopen(f"{url.rstrip('/')}/metrics", timeout=10) as response:
        return parse_metrics(response.read().decode("utf-8"))


def diff_snapshots(before: Snapshot, after: Snapshot) -> Snapshot:
    result: Snapshot = {}
    for key in ("hits", "queries", "prompt_tokens", "generation_tokens"):
        old, new = before.get(key), after.get(key)
        result[key] = None if old is None or new is None else new - old
    queries = result["queries"]
    hits = result["hits"]
    result["hit_ratio"] = None if queries in (None, 0) or hits is None else 100 * hits / queries
    return result


def format_diff(before: Snapshot, after: Snapshot) -> str:
    diff = diff_snapshots(before, after)

    def show(key: str, suffix: str = "") -> str:
        value = diff[key]
        return "n/a" if value is None else f"{value:g}{suffix}"

    return "\n".join([
        "| Δ hits | Δ queries | Hit ratio | Δ prompt tokens | Δ generation tokens |",
        "| ---: | ---: | ---: | ---: | ---: |",
        f"| {show('hits')} | {show('queries')} | {show('hit_ratio', '%')} | "
        f"{show('prompt_tokens')} | {show('generation_tokens')} |",
    ])


def load_snapshot(path: str) -> Snapshot:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = commands.add_parser("snapshot")
    snapshot_parser.add_argument("--url", default="http://127.0.0.1:8000")
    diff_parser = commands.add_parser("diff")
    diff_parser.add_argument("before")
    diff_parser.add_argument("after")
    watch_parser = commands.add_parser("watch")
    watch_parser.add_argument("--seconds", type=float, required=True)
    watch_parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    match args.command:
        case "snapshot":
            print(json.dumps(take_snapshot(args.url), sort_keys=True))
        case "diff":
            print(format_diff(load_snapshot(args.before), load_snapshot(args.after)))
        case "watch":
            before = take_snapshot(args.url)
            time.sleep(args.seconds)
            print(format_diff(before, take_snapshot(args.url)))
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    main()
