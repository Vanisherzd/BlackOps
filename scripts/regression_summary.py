#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScoreRecord:
    case: str
    tier: str
    outcome: str
    seconds: float | None
    calls: float | None
    cause_named: bool | None
    false_claim: bool


class InvalidScoreRecordsError(ValueError):
    pass


def load_records(path: pathlib.Path) -> list[ScoreRecord]:
    with path.open() as stream:
        raw_records = json.load(stream)
    if not isinstance(raw_records, list):
        raise InvalidScoreRecordsError("score JSON must be a list")

    records = []
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            raise InvalidScoreRecordsError(f"record {index} must be an object")
        try:
            case = raw["case"]
            tier = raw["tier"]
            outcome = raw["outcome"]
            seconds = raw["seconds"]
            calls = raw["calls"]
            cause_named = raw["cause_named"]
            false_claim = raw["false_claim"]
        except KeyError as error:
            raise InvalidScoreRecordsError(
                f"record {index} is missing {error.args[0]}"
            ) from error
        if (
            not isinstance(case, str)
            or not isinstance(tier, str)
            or not isinstance(outcome, str)
            or (seconds is not None and not isinstance(seconds, (int, float)))
            or (calls is not None and not isinstance(calls, (int, float)))
            or (cause_named is not None and not isinstance(cause_named, bool))
            or not isinstance(false_claim, bool)
        ):
            raise InvalidScoreRecordsError(f"record {index} has invalid field types")
        records.append(
            ScoreRecord(
                case=case,
                tier=tier,
                outcome=outcome,
                seconds=seconds,
                calls=calls,
                cause_named=cause_named,
                false_claim=false_claim,
            )
        )
    return records


def render(records: list[ScoreRecord]) -> str:
    grouped: dict[str, list[ScoreRecord]] = {}
    for record in records:
        grouped.setdefault(record.case, []).append(record)

    lines = [
        "| case | tier | runs | autonomous/runs | median seconds | median calls | cause named % | made-worse | false claims |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case, case_records in grouped.items():
        seconds = [record.seconds for record in case_records if record.seconds is not None]
        calls = [record.calls for record in case_records if record.calls is not None]
        causes = [record.cause_named for record in case_records if record.cause_named is not None]
        lines.append(
            f"| {case} | {case_records[0].tier} | {len(case_records)} "
            f"| {sum(record.outcome == 'autonomous' for record in case_records)}/{len(case_records)} "
            f"| {statistics.median(seconds):.0f} " if seconds else ""
        )
        if not seconds:
            lines[-1] += "| – "
        lines[-1] += f"| {statistics.median(calls):.0f} " if calls else "| – "
        lines[-1] += (
            f"| {100 * sum(causes) / len(causes):.0f}% " if causes else "| – "
        )
        lines[-1] += (
            f"| {sum(record.outcome == 'made-worse' for record in case_records)} "
            f"| {sum(record.false_claim for record in case_records)} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print a compact Markdown summary of score.py JSON records."
    )
    parser.add_argument("scores", type=pathlib.Path)
    args = parser.parse_args()
    print(render(load_records(args.scores)))


if __name__ == "__main__":
    main()
