from __future__ import annotations

import csv
import io
import json
import tarfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, BinaryIO


class EvidenceUnavailable(RuntimeError):
    pass


def _short(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit] + "…"


def _event(
    kind: str,
    message: Any,
    *,
    timestamp: Any = None,
    service: Any = None,
    source_file: str | None = None,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "timestamp": None if timestamp is None else str(timestamp),
        "service": None if service is None else str(service),
        "message": _short(message),
        "source_file": source_file,
        "fields": fields or {},
    }


def _timestamp(row: dict[str, Any]) -> Any:
    for key in (
        "timestamp",
        "time",
        "ts",
        "datetime",
        "start_time",
        "event_time",
        "observed_time_unix_nano",
    ):
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _service(row: dict[str, Any]) -> Any:
    for key in ("service", "serviceName", "service_name", "container_name", "pod", "instance"):
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _row_message(row: dict[str, Any]) -> str:
    for key in ("message", "body", "log", "content", "description", "summary"):
        if key in row and row[key] not in (None, ""):
            return _short(row[key])
    useful = {key: value for key, value in row.items() if value not in (None, "")}
    return _short(useful)


def _parquet_events(
    source: Path | BinaryIO,
    *,
    kind: str,
    source_name: str,
    limit: int,
) -> Iterator[dict[str, Any]]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise EvidenceUnavailable("Parquet reader missing. Install: pip install 'blackout-ops-kb[telemetry]'") from exc
    parquet_file = parquet.ParquetFile(source)
    emitted = 0
    for batch in parquet_file.iter_batches(batch_size=min(256, max(limit, 1))):
        for row in batch.to_pylist():
            yield _event(
                kind,
                _row_message(row),
                timestamp=_timestamp(row),
                service=_service(row),
                source_file=source_name,
            )
            emitted += 1
            if emitted >= limit:
                return


def _json_events(path: Path, kind: str, limit: int) -> Iterator[dict[str, Any]]:
    item = json.loads(path.read_text(encoding="utf-8"))
    emitted = 0
    if kind == "log" and isinstance(item, dict):
        for service, values in item.items():
            for raw in values if isinstance(values, list) else [values]:
                parsed: Any = raw
                if isinstance(raw, str) and raw.lstrip().startswith(("{", "[")):
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        parsed = raw
                if isinstance(parsed, dict):
                    yield _event(
                        kind,
                        parsed.get("message", parsed),
                        timestamp=parsed.get("time") or parsed.get("timestamp"),
                        service=service,
                        source_file=path.name,
                        fields={"level": parsed.get("level")},
                    )
                else:
                    yield _event(kind, parsed, service=service, source_file=path.name)
                emitted += 1
                if emitted >= limit:
                    return
        return
    if isinstance(item, dict):
        for key, value in item.items():
            if isinstance(value, str):
                for line in value.splitlines() or [value]:
                    yield _event(kind, line, service=key, source_file=path.name)
                    emitted += 1
                    if emitted >= limit:
                        return
            else:
                yield _event(kind, value, service=key, source_file=path.name)
                emitted += 1
                if emitted >= limit:
                    return
    elif isinstance(item, list):
        for value in item[:limit]:
            if isinstance(value, dict):
                yield _event(
                    kind,
                    _row_message(value),
                    timestamp=_timestamp(value),
                    service=_service(value),
                    source_file=path.name,
                )
            else:
                yield _event(kind, value, source_file=path.name)


def _delimited_events(path: Path, kind: str, limit: int) -> Iterator[dict[str, Any]]:
    if path.read_bytes()[:40].startswith(b"version https://git-lfs"):
        raise EvidenceUnavailable(f"Git LFS payload not downloaded: {path}")
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        for index, row in enumerate(reader):
            if index >= limit:
                return
            yield _event(
                kind,
                _row_message(row),
                timestamp=_timestamp(row),
                service=_service(row),
                source_file=path.name,
            )


def _load_cloud(record: dict[str, Any], root: Path, max_events: int) -> list[dict[str, Any]]:
    case_dir = (root / record["source_path"]).parent
    raw = case_dir / "raw_data"
    if not raw.exists():
        raise EvidenceUnavailable(f"Cloud-OpsBench raw_data missing: {raw}")
    events: list[dict[str, Any]] = []
    specs = (("logs.json", "log"), ("k8s_states.json", "k8s_state"), ("alert.json", "alert"))
    per_source = max(1, max_events // max(len(specs) + 1, 1))
    for filename, kind in specs:
        path = raw / filename
        if path.exists():
            events.extend(_json_events(path, kind, per_source))
    metrics = raw / "metrics.csv"
    if metrics.exists():
        events.extend(_delimited_events(metrics, "metric", per_source))
    return events[:max_events]


def _load_rca100(record: dict[str, Any], root: Path, max_events: int) -> list[dict[str, Any]]:
    task_id = record.get("metadata", {}).get("task_id")
    case_dir = root / "AgenticOpsEval" / "RCA100" / "cases" / str(task_id)
    if not case_dir.exists():
        raise EvidenceUnavailable(f"RCA100 case missing: {case_dir}")
    events: list[dict[str, Any]] = []
    files = [path for path in case_dir.iterdir() if path.suffix in {".json", ".parquet"}]
    per_source = max(1, max_events // max(len(files), 1))
    for path in sorted(files):
        if path.name == "topology.json":
            item = json.loads(path.read_text(encoding="utf-8"))
            entities = {entity.get("id"): entity.get("name") for entity in item.get("entities", [])}
            for edge in item.get("edges", [])[:per_source]:
                events.append(
                    _event(
                        "topology",
                        f"{entities.get(edge.get('src'), edge.get('src'))} {edge.get('relation')} "
                        f"{entities.get(edge.get('dst'), edge.get('dst'))}",
                        source_file=path.name,
                    )
                )
        elif path.suffix == ".json":
            events.extend(_json_events(path, "task", per_source))
        else:
            events.extend(_parquet_events(path, kind=path.stem, source_name=path.name, limit=per_source))
    return events[:max_events]


def _load_rcaeval(record: dict[str, Any], root: Path, max_events: int) -> list[dict[str, Any]]:
    case_dir = root / record["source_path"]
    if not case_dir.exists():
        raise EvidenceUnavailable(f"RCAEval case missing: {case_dir}")
    paths = sorted(case_dir.glob("*.parquet"))
    per_source = max(1, max_events // max(len(paths), 1))
    events: list[dict[str, Any]] = []
    for path in paths:
        events.extend(_parquet_events(path, kind=path.stem, source_name=path.name, limit=per_source))
    inject = case_dir / "inject_time.txt"
    if inject.exists():
        events.append(_event("boundary", f"fault injection boundary unix={inject.read_text().strip()}", source_file=inject.name))
    return events[:max_events]


def _load_itbench(record: dict[str, Any], root: Path, max_events: int) -> list[dict[str, Any]]:
    case_dir = (root / record["source_path"]).parent
    files = [
        path
        for path in case_dir.rglob("*")
        if path.is_file() and path.name != "ground_truth.yaml" and path.suffix.lower() in {".json", ".tsv"}
    ]
    if not files:
        raise EvidenceUnavailable(f"ITBench telemetry missing: {case_dir}")
    per_source = max(1, max_events // len(files))
    events: list[dict[str, Any]] = []
    for path in sorted(files):
        kind = "alert" if path.suffix.lower() == ".json" else path.stem
        try:
            if path.suffix.lower() == ".json":
                events.extend(_json_events(path, kind, per_source))
            else:
                events.extend(_delimited_events(path, kind, per_source))
        except EvidenceUnavailable:
            continue
        if len(events) >= max_events:
            break
    if not events:
        raise EvidenceUnavailable(f"ITBench selected payload is not downloaded: {case_dir}")
    return events[:max_events]


def _select_aiops_archive(record: dict[str, Any], root: Path) -> Path:
    archive_root = root / "AgenticOpsEval" / "AIOps2025"
    start = str(record.get("metadata", {}).get("start_time") or "")[:10]
    exact = archive_root / f"{start}.tar.gz"
    if exact.exists():
        return exact
    archives = sorted(archive_root.glob("*.tar.gz"))
    if not archives:
        raise EvidenceUnavailable("AIOps2025 archives missing")
    return min(archives, key=lambda path: abs(int(path.stem[:10].replace("-", "")) - int(start.replace("-", "") or 0)))


def _load_aiops2025(record: dict[str, Any], root: Path, max_events: int) -> list[dict[str, Any]]:
    archive = _select_aiops_archive(record, root)
    key_metrics = [str(value).lower() for value in record.get("metadata", {}).get("key_metrics", [])]
    with tarfile.open(archive, "r:gz") as tar:
        members = [
            member
            for member in tar.getmembers()
            if member.isfile() and member.name.endswith(".parquet") and "/._" not in member.name and not Path(member.name).name.startswith("._")
        ]
        members.sort(
            key=lambda member: (
                0 if "log-parquet" in member.name else 1 if any(metric in member.name.lower() for metric in key_metrics) else 2,
                member.size,
            )
        )
        selected = members[: min(12, len(members))]
        per_source = max(1, max_events // max(len(selected), 1))
        events: list[dict[str, Any]] = []
        for member in selected:
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            data = io.BytesIO(extracted.read())
            kind = "log" if "log-parquet" in member.name else "trace" if "trace-parquet" in member.name else "metric"
            events.extend(_parquet_events(data, kind=kind, source_name=f"{archive.name}:{member.name}", limit=per_source))
        return events[:max_events]


def load_evidence(record: dict[str, Any], datasets_root: str | Path, max_events: int = 2000) -> dict[str, Any]:
    root = Path(datasets_root).resolve()
    source = str(record.get("source", ""))
    if source == "Cloud-OpsBench":
        events = _load_cloud(record, root, max_events)
    elif source == "AgenticOpsEval/RCA100":
        events = _load_rca100(record, root, max_events)
    elif source == "RCAEval":
        events = _load_rcaeval(record, root, max_events)
    elif source == "ITBench-Lite/SRE":
        events = _load_itbench(record, root, max_events)
    elif source == "AgenticOpsEval/AIOps2025":
        events = _load_aiops2025(record, root, max_events)
    else:
        raise EvidenceUnavailable(f"Unsupported source: {source}")
    if not events:
        raise EvidenceUnavailable(f"No evidence loaded for {record.get('id')}")
    return {
        "id": record["id"],
        "source": source,
        "window": {
            "start": record.get("metadata", {}).get("start_time") or record.get("metadata", {}).get("time_start"),
            "end": record.get("metadata", {}).get("end_time") or record.get("metadata", {}).get("time_end"),
        },
        "events": events,
    }


def find_eval_truth(path: str | Path, eval_id: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                item = json.loads(line)
                if item.get("id") == eval_id:
                    return item
    raise KeyError(f"Unknown eval id: {eval_id}")
