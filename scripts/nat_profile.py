"""Profile archived incidents with the NeMo Agent Toolkit profiler: where an incident's time, tokens and energy go.

  .nemo-venv/bin/python scripts/nat_profile.py logs/runs/<run> [logs/runs/<run> ...] [--out logs/nat-profile/<name>]

Each archived run (scripts/run-archive.sh, or a watchdog incident) becomes one NAT request: its agent turns from
oai-shim.log (a request row opens the LLM span, the next usage row closes it with the token counts) and its tool calls
from ops-api.log (start time plus the call's own latency) are turned into NAT IntermediateSteps under one workflow
span, and NAT's ProfilerRunner computes its metrics (per-request and per-step latency, token usage, workflow-runtime and LLM-latency
confidence intervals) into --out. On top of NAT's report this script integrates logs/runs/<run>/power.jsonl over each
span category, which gives the energy split between model generation, tool execution and the gaps in between: the
number the edge slide needs. Read-only on the runs.
"""
import asyncio
import datetime
import json
import pathlib
import sys
import uuid

from nat.data_models.intermediate_step import (IntermediateStep, IntermediateStepPayload, IntermediateStepType,
                                               StreamEventData, UsageInfo)
from nat.data_models.invocation_node import InvocationNode
from nat.data_models.profiler import ProfilerConfig
from nat.data_models.token_usage import TokenUsageBaseModel
from nat.plugins.profiler.profile_runner import ProfilerRunner

ROOT = pathlib.Path(__file__).resolve().parent.parent


def rows(p):
    try:
        return [json.loads(line) for line in open(p) if line.strip()]
    except (OSError, ValueError):
        return []


def clock(hms, start):
    """ops-api.log keeps HH:MM:SS; put it on the run's start day (next day if it falls before the start)."""
    h, m, s = (int(x) for x in str(hms).split(":"))
    t = datetime.datetime.fromtimestamp(start).replace(hour=h, minute=m, second=s, microsecond=0).timestamp()
    return t + 86400 if t < start - 3600 else t


def spans(run):
    """(kind, name, t0, t1, usage) for one archived run."""
    meta = json.loads((run / "meta.json").read_text())
    start, end = float(meta["start"]), float(meta["end"])
    out, opened = [], None
    for r in rows(run / "oai-shim.log"):
        ts = float(r.get("ts", 0))
        if not start - 5 <= ts <= end + 5:
            continue
        if "max_tokens" in r or "prompt_est" in r:          # a chat request reached the shim
            opened = ts
        elif "usage" in r and opened is not None:
            u = r["usage"] or {}
            out.append(("LLM", "edge-agent", opened, ts, u))
            opened = None
    for r in rows(run / "ops-api.log"):
        try:
            t0 = clock(r["ts"], start)
        except (KeyError, ValueError):
            continue
        out.append(("TOOL", r.get("tool", "?"), t0, t0 + float(r.get("ms") or 0) / 1000, None))
    return meta, sorted(out, key=lambda s: s[2])


def steps(run):
    meta, sp = spans(run)
    wf = InvocationNode(function_id=str(uuid.uuid4()), function_name=f"incident:{meta.get('case', '?')}")
    node = lambda n: InvocationNode(function_id=str(uuid.uuid4()), function_name=n, parent_id=wf.function_id,
                                    parent_name=wf.function_name)
    st = lambda node_, p: IntermediateStep(parent_id=wf.function_id, function_ancestry=node_, payload=p)
    seq = [st(wf, IntermediateStepPayload(event_type=IntermediateStepType.WORKFLOW_START, event_timestamp=float(meta["start"]),
                                          name=wf.function_name, data=StreamEventData(input=meta.get("case"))))]
    for kind, name, t0, t1, u in sp:
        n = node(name)
        usage = None
        if u:
            usage = UsageInfo(token_usage=TokenUsageBaseModel(prompt_tokens=u.get("prompt_tokens", 0),
                                                              completion_tokens=u.get("completion_tokens", 0),
                                                              total_tokens=u.get("total_tokens", 0)), num_llm_calls=1)
        a, b = ((IntermediateStepType.LLM_START, IntermediateStepType.LLM_END) if kind == "LLM"
                else (IntermediateStepType.TOOL_START, IntermediateStepType.TOOL_END))
        seq.append(st(n, IntermediateStepPayload(event_type=a, event_timestamp=t0, name=name, data=StreamEventData())))
        seq.append(st(n, IntermediateStepPayload(event_type=b, event_timestamp=t1, span_event_timestamp=t0, name=name,
                                                 data=StreamEventData(), usage_info=usage)))
    seq.append(st(wf, IntermediateStepPayload(event_type=IntermediateStepType.WORKFLOW_END, event_timestamp=float(meta["end"]),
                                              span_event_timestamp=float(meta["start"]), name=wf.function_name,
                                              data=StreamEventData(output=meta.get("verify")))))
    return meta, sp, seq


def energy_split(run, meta, sp):
    """Wh of the run split into model generation, tool execution and the rest (trapezoid over power.jsonl)."""
    pts = sorted((float(r["ts"]), float(r["mw"])) for r in rows(run / "power.jsonl") if "ts" in r and "mw" in r)
    if len(pts) < 2:
        return None
    where = lambda t: next((k for k, _, a, b, _ in sp if a <= t <= b), "other")
    split = {"LLM": 0.0, "TOOL": 0.0, "other": 0.0}
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        if t1 < float(meta["start"]) or t0 > float(meta["end"]):
            continue
        split[where((t0 + t1) / 2)] += (t1 - t0) * (p0 + p1) / 2 / 1000 / 3600
    return {k: round(v, 5) for k, v in split.items()}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "logs" / "nat-profile"
    args = [a for a in args if pathlib.Path(a) != out]
    runs = [pathlib.Path(a) for a in args if (pathlib.Path(a) / "meta.json").exists()]
    if not runs:
        sys.exit("give one or more archived runs (logs/runs/<run>/ with meta.json)")
    all_steps, summary = [], []
    for run in runs:
        meta, sp, seq = steps(run)
        all_steps.append(seq)
        llm = [s for s in sp if s[0] == "LLM"]
        tool = [s for s in sp if s[0] == "TOOL"]
        summary.append(dict(run=run.name, case=meta.get("case"), verify=meta.get("verify"), seconds=meta.get("seconds"),
                            llm_calls=len(llm), llm_s=round(sum(b - a for _, _, a, b, _ in llm), 1),
                            tool_calls=len(tool), tool_s=round(sum(b - a for _, _, a, b, _ in tool), 1),
                            prompt_tokens=sum((u or {}).get("prompt_tokens", 0) for *_, u in llm),
                            completion_tokens=sum((u or {}).get("completion_tokens", 0) for *_, u in llm),
                            energy_wh=energy_split(run, meta, sp)))
    # NAT's bottleneck-stack and concurrency analyses found nothing to say on one-agent, one-call-at-a-time incidents
    # (an empty stack, concurrency 0), so only the metrics that are meaningful here are on.
    cfg = ProfilerConfig(base_metrics=True, compute_llm_metrics=True, csv_exclude_io_text=True)
    asyncio.run(ProfilerRunner(cfg, out).run(all_steps))
    (out / "incident_summary.json").write_text(json.dumps(summary, indent=1))
    print(f"NAT profiler output in {out}/ ({len(runs)} runs)")
    print("| run | case | verify | s | LLM calls | LLM s | tool calls | tool s | prompt tok | completion tok | Wh (LLM / tool / other) |")
    print("|" + " --- |" * 11)
    for s in summary:
        e = s["energy_wh"]
        es = f"{e['LLM']} / {e['TOOL']} / {e['other']}" if e else "–"
        print(f"| {s['run']} | {s['case']} | {s['verify']} | {s['seconds']} | {s['llm_calls']} | {s['llm_s']} | {s['tool_calls']} "
              f"| {s['tool_s']} | {s['prompt_tokens']} | {s['completion_tokens']} | {es} |")


if __name__ == "__main__":
    main()
