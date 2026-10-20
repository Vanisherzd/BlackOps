#!/usr/bin/env bash
# One drill, kept: runs scripts/nemoclaw-drill.sh <case> unchanged, then copies what this run wrote into
# logs/runs/<time>-<case>/ for the dashboard's replay mode and the overnight runs. The drill clears logs/ops-api.log at
# its start, so once it ends that file holds this run only; the shared append-only logs are cut at their line count
# before the run. Same victim containers as every other drill: never run it while another drill or A/B is going.
#   scripts/run-archive.sh <case>                    e.g. TRIAGE=1 scripts/run-archive.sh upstream-500
# RUN_LABEL names the arm in meta.json (scripts/overnight.sh sets it); scripts/score-runs.py groups by it.
# nemoclaw-drill.sh always works in ~/edge-agent, so run this from that checkout.
# logs/runs/ is outside agent.CLEANUP_GLOBS, so cleanup_disk never touches it.
cd "$(dirname "$0")/.." || exit 1
CASE="${1:?usage: scripts/run-archive.sh <case>}"
TS=$(date +%Y%m%d-%H%M%S); OUT="logs/runs/$TS-$CASE"; mkdir -p "$OUT"
lines() { [ -f "$1" ] && wc -l < "$1" || echo 0; }
REV0=$(lines logs/reviews.jsonl); SHIM0=$(lines logs/oai-shim.log); T0=$(date +%s.%N)
touch "$OUT/.start"

bash scripts/nemoclaw-drill.sh "$CASE" 2>&1 | tee "$OUT/drill.log"
T1=$(date +%s.%N)

cp logs/ops-api.log "$OUT/ops-api.log" 2>/dev/null
tail -n +$((REV0 + 1)) logs/reviews.jsonl > "$OUT/reviews.jsonl" 2>/dev/null
tail -n +$((SHIM0 + 1)) logs/oai-shim.log > "$OUT/oai-shim.log" 2>/dev/null
# host-loop traces (self_heal, faults.py run) written during this run
find logs/agent -maxdepth 1 -name '*.jsonl' -newer "$OUT/.start" -exec cp {} "$OUT/" \; 2>/dev/null
# rows of the demo's own logs (tier, power, approvals, watchdog), when they exist, cut to this run's time window
python3 - "$OUT" "$T0" "$T1" "$CASE" <<'PY'
import json, pathlib, re, subprocess, sys
out, t0, t1, case = pathlib.Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
for name in ("tier.jsonl", "power.jsonl", "incidents.jsonl", "approvals.jsonl"):
    src = pathlib.Path("logs") / name
    if not src.exists():
        continue
    keep = []
    for line in src.open(errors="replace"):
        try:
            ts = float(json.loads(line).get("ts", 0))
        except (ValueError, AttributeError):
            continue
        if t0 - 5 <= ts <= t1 + 5:
            keep.append(line)
    (out / name).write_text("".join(keep))
drill = (out / "drill.log").read_text(errors="replace")
m = re.findall(r"verify: .*-> (PASS|FAIL)", drill)
head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True).stdout.strip())
calls = sum(1 for _ in (out / "ops-api.log").open()) if (out / "ops-api.log").exists() else 0
import os
meta = dict(case=case, start=t0, end=t1, seconds=round(t1 - t0, 1), verify=m[-1] if m else "UNKNOWN", ops_calls=calls,
            git=head + ("+dirty" if dirty else ""), label=os.environ.get("RUN_LABEL", ""),
            env={k: os.environ[k] for k in ("TIER_MODE", "APPROVAL", "KB_RETRIEVAL", "TRIAGE", "SHIM_THINK") if k in os.environ})
(out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
print(f"=== archived {out} verify={meta['verify']} calls={calls} {meta['seconds']}s")
PY
rm -f "$OUT/.start"
