"""Table for the energy slide from archived runs (logs/runs/*/, written by scripts/run-archive.sh and the watchdog):
per arm, how often it fixed the stack, how long it took, the energy per diagnosis and how much of it Tier-1 carried.
Leaves scripts/score.py, which reads the drill logs, as it is.

  .venv/bin/python scripts/score-runs.py                       # every run, grouped by RUN_LABEL (A/B from overnight.sh)
  .venv/bin/python scripts/score-runs.py logs/runs/2026102[23]-*  # a subset
  .venv/bin/python scripts/score-runs.py --json out.json

Energy is the module rails or the GPU (scripts/power.py), integrated over the run, idle included; subtract the idle
draw (POWER_IDLE_W, default 0) to state it as the cost above idle. Tier-1 share = the fraction of the run's time
spent at Tier-1 according to tier.jsonl (runs that never changed tier count at their arm's starting tier).
"""
import glob
import json
import os
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import power  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
IDLE_W = float(os.environ.get("POWER_IDLE_W", "0"))


def rows(p):
    try:
        return [json.loads(line) for line in open(p) if line.strip()]
    except (OSError, ValueError):
        return []


def tier1_share(d, meta):
    start, end = float(meta["start"]), float(meta["end"])
    changes = sorted(rows(d / "tier.jsonl"), key=lambda r: r.get("ts", 0))
    first = 2 if (meta.get("env") or {}).get("TIER_MODE") == "always2" else 1
    tier, t, at1 = (changes[0].get("frm") or first) if changes else first, start, 0.0
    for c in changes:
        ts = min(max(float(c["ts"]), start), end)
        at1 += (ts - t) if tier == 1 else 0
        tier, t = c.get("tier"), ts
    at1 += (end - t) if tier == 1 else 0
    return at1 / max(end - start, 1e-9), len(changes)


def score(d):
    meta = json.loads((d / "meta.json").read_text())
    e = power.energy(float(meta["start"]), float(meta["end"]), d / "power.jsonl") if (d / "power.jsonl").exists() else None
    share, switches = tier1_share(d, meta)
    approvals = [a for a in rows(d / "approvals.jsonl") if "decision" in a]
    wh = None
    if e:
        wh = e["wh"] - IDLE_W * float(meta["seconds"]) / 3600
    return dict(run=d.name, label=meta.get("label") or "-", case=meta.get("case"), verify=meta.get("verify"),
                seconds=meta.get("seconds"), wh=wh, avg_w=e and e["avg_w"], peak_w=e and e["peak_w"],
                tier1_share=share, tier_switches=switches, approvals=len(approvals),
                approved=sum(a["decision"] == "approve" for a in approvals))


def med(xs, nd=1):
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs):.{nd}f}" if xs else "–"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--json" in sys.argv:
        args = [a for a in args if a != sys.argv[sys.argv.index("--json") + 1]]
    dirs = [pathlib.Path(p) for a in (args or [str(ROOT / "logs" / "runs" / "*")]) for p in glob.glob(a)]
    recs = [score(d) for d in sorted(dirs) if (d / "meta.json").exists()]
    print("| arm | runs | fixed (verify PASS) | median s | median Wh/diagnosis | median avg W | median peak W | Tier-1 share of time | tier switches | approvals (approved) |")
    print("|" + " --- |" * 10)
    for label in sorted({r["label"] for r in recs}):
        rs = [r for r in recs if r["label"] == label]
        ok = sum(r["verify"] == "PASS" for r in rs)
        print(f"| {label} | {len(rs)} | {ok}/{len(rs)} | {med([r['seconds'] for r in rs])} | {med([r['wh'] for r in rs], 4)} "
              f"| {med([r['avg_w'] for r in rs])} | {med([r['peak_w'] for r in rs])} | {100 * statistics.mean(r['tier1_share'] for r in rs):.0f}% "
              f"| {sum(r['tier_switches'] for r in rs)} | {sum(r['approvals'] for r in rs)} ({sum(r['approved'] for r in rs)}) |")
    print()
    print("| run | arm | case | verify | s | Wh | avg W | Tier-1 share |")
    print("|" + " --- |" * 8)
    for r in recs:
        wh = f"{r['wh']:.4f}" if r["wh"] is not None else "–"
        print(f"| {r['run']} | {r['label']} | {r['case']} | {r['verify']} | {r['seconds']} | {wh} | {r['avg_w'] or '–'} | {100 * r['tier1_share']:.0f}% |")
    if "--json" in sys.argv:
        pathlib.Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(recs, indent=1))


if __name__ == "__main__":
    main()
