"""Knowledge-pack RAG evaluation on held-out benchmark incidents, fully offline on the Spark.

  uv run --no-sync scripts/kb-eval.py prepare            # raw telemetry -> compressed, label-free inputs per ratio
  uv run --no-sync scripts/kb-eval.py run [--think 1]    # Nemotron diagnoses every input, with and without the KB
  uv run --no-sync scripts/kb-eval.py report             # curves, breakdowns, retrieval metrics -> docs/kb-eval.md
  uv run --no-sync scripts/kb-eval.py all                # the three in a row (detach it: setsid nohup ... &)

What is measured. The teammate's knowledge pack (kb/) ships 182 held-out cases whose answers never entered the
index, and a compressor that turns a case's raw telemetry into label-free inputs at a chosen ratio. This script
takes a stratified sample of those cases (every disk_full / crashloop / dns case, a per-source slice of the rest),
prepares inputs at several ratios, and asks the local model for the root cause twice per input: telemetry only, and
telemetry plus what `search_incident_knowledge` (the agent's own tool, unchanged) retrieves for the symptoms in it.
Predictions are scored with the pack's own scorer against its ground truth, so the numbers are comparable with the
teammate's smoke run. Alongside: prompt tokens from the server, wall latency, GPU energy from nvidia-smi sampled at
1 Hz with the idle draw subtracted and shared among in-flight calls, and how often retrieval matched at all.

Outputs live in logs/kb-eval/ (inputs, predictions, power trace); the report and curve CSVs go to docs/kb-eval*.
"""
import argparse
import collections
import csv
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "kb" / "src"))
OUT = ROOT / "logs" / "kb-eval"
INPUTS = OUT / "inputs"
DOCS = ROOT / "docs" / "kb-eval"
GT = ROOT / "kb" / "artifacts" / "eval_ground_truth.jsonl"
TRAIN = ROOT / "kb" / "artifacts" / "cases_train.jsonl"
RAW = ROOT / "datasets" / "raw"
BASE = os.environ.get("VLLM_URL", "http://127.0.0.1:8000")
MODEL = os.environ.get("SERVED_NAME", "edge-agent")
RATIOS = [1, 2, 4, 8, 16, 32]
OTHER_PER_SOURCE = {"AgenticOpsEval/AIOps2025": 6, "AgenticOpsEval/RCA100": 4, "Cloud-OpsBench": 10, "RCAEval": 8}
TELEMETRY_CHARS = 60_000        # ~20k tokens of JSON-ish logs; the server's max_seq_len is 32k and the answer needs room
EVENT_CHARS = 1500              # per event line; wide metric rows are pivoted instead (see render)
SCENARIOS = ("disk_full (disk, inode or filesystem exhaustion, DiskPressure, runaway file writes); "
             "crashloop (container or pod crash or restart loop, OOMKilled, pod failure or kill, invalid command); "
             "dns (any name-resolution failure: service DNS, CoreDNS, registry DNS, host resolver); other (anything else)")
SYMPTOM_RE = re.compile(r"\b(error|fail(ed|ure|ing)?|fatal|exception|refused|timeout|timed out|unavailable|oom(killed)?|killed|no space|"
                        r"restarting|crash(loop)?(backoff)?|back-?off|pressure|not found|no such|denied|unreachable|evict(ed|ion)?|5\d\d)\b", re.I)
HEADER_RE = re.compile(r"^(NAME\s|Restart Count: 0|Liveness:|Readiness:)")     # kubectl table headers and healthy probes are not symptoms
SYSTEM = ("You are the on-call SRE for a Kubernetes/Linux system with no internet and no colleague to ask. You get compressed "
          "telemetry from one incident (logs, metrics, traces, alerts, Kubernetes state) and sometimes retrieved knowledge from "
          f"an offline knowledge base. Name the root cause. Scenario categories: {SCENARIOS}. Reply with exactly one JSON object and "
          'nothing else: {"predicted_scenario": "disk_full|crashloop|dns|other", "predicted_label": "<copy exactly one label from the '
          'candidate list; only if none fits, a short phrase>", "predicted_service": "<the failing pod, service, node or component named '
          'in the telemetry, or unknown>", "confidence": <0..1>, "evidence": ["<at most 2 quotes from the telemetry, each under 80 '
          'characters>"]}. predicted_scenario is the category; predicted_label is the fault itself, never a category name.')


def jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- prepare ------------------------------------------------------------------------------------------------------

def select_cases():
    """Every case of the three target scenarios, plus a fixed per-source slice of the rest. ITBench's two cases carry
    labels ('custom(flagd)', 'chaos') that no diagnosis could name, so they stay out."""
    picked, count = [], collections.Counter()
    for t in jsonl(GT):
        if t["source"].startswith("ITBench"):
            continue
        key = (t["scenario"], t["source"])
        cap = None if t["scenario"] != "other" else OTHER_PER_SOURCE.get(t["source"], 0)
        if cap is not None and count[key] >= cap:
            continue
        count[key] += 1
        picked.append(t)
    return picked


def prepare(args):
    from blackout_ops_kb.compression import compress_events
    from blackout_ops_kb.evaluation import DEFAULT_KEYWORDS
    from blackout_ops_kb.evidence import EvidenceUnavailable, load_evidence
    INPUTS.mkdir(parents=True, exist_ok=True)
    cases = select_cases()
    (OUT / "cases.json").write_text(json.dumps([{"id": t["id"], "scenario": t["scenario"], "source": t["source"]} for t in cases], indent=1))
    if args.ids:                                   # top up specific cases into existing input files (after a loader fix)
        cases = [t for t in cases if t["id"] in args.ids]
    streams = {r: open(INPUTS / f"ratio_{r}.jsonl", "a" if args.ids else "w", encoding="utf-8") for r in args.ratios}
    skipped = []
    for i, t in enumerate(cases, 1):
        t0 = time.time()
        try:
            ev = load_evidence(t, RAW, max_events=2000)
        except (EvidenceUnavailable, OSError, ValueError) as e:
            skipped.append({"id": t["id"], "reason": str(e)[:200]})
            print(f"skip {t['id']}: {e}", flush=True)
            continue
        window = dict(ev.get("window") or {})
        if t["source"] == "RCAEval":                       # the injection time is part of the benchmark's input
            f = RAW / t["source_path"] / "inject_time.txt"
            if f.exists():
                window["boundary"] = float(f.read_text().strip())
        # Baseline-vs-window metric summaries (AIOps loader) are already a compressed representation, so the ratio
        # applies to the raw streams around them; otherwise the compressor's keyword bias drops the infra lines first.
        summary = [e for e in ev["events"] if e.get("kind") == "metric" and e.get("source_file") in ("apm", "infra")]
        raw = [e for e in ev["events"] if e not in summary]
        for r, s in streams.items():
            c = compress_events(raw, target_ratio=float(r), keywords=DEFAULT_KEYWORDS,
                                window_start=window.get("start"), window_end=window.get("end"))
            s.write(json.dumps({"id": t["id"], "compression_ratio": r, "window": window, "telemetry": summary + c["events"],
                                "compression_metrics": dict(c["metrics"], summary_events=len(summary))}, ensure_ascii=False) + "\n")
        print(f"[{i}/{len(cases)}] {t['id']} {t['source']} {t['scenario']} events={len(ev['events'])} {time.time() - t0:.1f}s", flush=True)
    for s in streams.values():
        s.close()
    (OUT / "prepare_report.json").write_text(json.dumps({"cases": len(cases), "skipped": skipped, "ratios": args.ratios}, indent=1))
    print(f"prepared {len(cases) - len(skipped)} cases at ratios {args.ratios}, skipped {len(skipped)}")


# --- run ----------------------------------------------------------------------------------------------------------

def wide_rows(events):
    """Metric events whose message is a JSON row of many numeric columns (RCAEval's per-timestamp tables)."""
    rows = []
    for e in events:
        if e.get("kind") not in ("metrics", "metric"):
            continue
        m = str(e.get("message", ""))
        if not m.startswith("{"):
            continue
        try:
            d = json.loads(m)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and sum(isinstance(v, (int, float)) for v in d.values()) >= 5:
            rows.append((e, d))
    return rows


def pivot(rows, boundary, top=30):
    """Per-column before/after summary of wide metric rows. The renderer used to cut each 2 KB row at 600 characters,
    which left the model the first dozen columns, all CPU, and it duly answered 'cpu'."""
    import math
    from statistics import mean
    times = sorted(d.get("time") for _, d in rows if isinstance(d.get("time"), (int, float)))
    median = times[len(times) // 2] if times else None
    split = boundary if boundary is not None else median
    if split is not None and times and (times[0] >= split or times[-1] < split):
        split = median                       # the compressed sample sits on one side of the boundary: compare its halves
    cols = {}
    for _, d in rows:
        after = split is None or not isinstance(d.get("time"), (int, float)) or d["time"] >= split
        for k, v in d.items():
            if k == "time" or not isinstance(v, (int, float)):
                continue
            cols.setdefault(k, ([], []))[1 if after else 0].append(v)
    out = []
    for k, (b, a) in cols.items():
        if not a or not b:
            continue
        mb, ma = mean(b), mean(a)
        ratio = (ma + 1e-9) / (mb + 1e-9)
        out.append((abs(math.log(max(ratio, 1e-9))), f"{k}: before {mb:.4g} -> after {ma:.4g}, peak {max(a):.4g} (x{ratio:.2f})"))
    out.sort(reverse=True)
    return [t for _, t in out[:top]]


def render(events, budget, boundary=None):
    """One line per event; wide metric rows become a per-column before/after pivot. Over budget, symptom-looking
    lines are kept first, then the rest in order, and the kept lines go back into their original order: cutting from
    the head would drop the fault window of a chronological log."""
    lines = []
    wide = wide_rows(events)
    if wide:
        n = len(wide)
        lines.append(f"[metrics] per-column summary of {n} samples, before vs after the anomaly boundary, largest changes first:")
        lines += ["  " + t for t in pivot(wide, boundary)]
    wide_ids = {id(e) for e, _ in wide}
    for e in events:
        if id(e) in wide_ids:
            continue
        msg = str(e.get("message", "")).replace("\n", " ")
        msg = msg if len(msg) <= EVENT_CHARS else msg[:EVENT_CHARS] + "…"
        head = " ".join(x for x in (f"[{e.get('kind')}]", str(e.get("timestamp") or e.get("first_timestamp") or ""), str(e.get("service") or "")) if x)
        lines.append(f"{head}: {msg}" + (f"  (x{e['count']})" if int(e.get("count") or 1) > 1 else ""))
    if sum(len(l) + 1 for l in lines) <= budget:
        return "\n".join(lines), False
    keep, n = set(), 0
    for pass_symptoms in (True, False):
        for i, l in enumerate(lines):
            if i in keep or bool(SYMPTOM_RE.search(l)) != pass_symptoms:
                continue
            if n + len(l) + 1 > budget:
                continue
            keep.add(i)
            n += len(l) + 1
    return "\n".join(lines[i] for i in sorted(keep)), True


def symptom_query(events):
    """What the agent would type into search_incident_knowledge: the lines that look like symptoms, else the busiest."""
    seen = []
    for e in events:
        m = str(e.get("template") or e.get("message") or "").replace("\n", " ")[:160]
        if SYMPTOM_RE.search(m) and not HEADER_RE.match(m) and m not in seen:
            seen.append(m)
        if len(seen) >= 25:
            break
    if len(seen) < 5:
        for e in sorted(events, key=lambda e: -int(e.get("count") or 1)):
            m = str(e.get("template") or e.get("message") or "")[:160]
            if m not in seen:
                seen.append(m)
            if len(seen) >= 12:
                break
    return " | ".join(seen)


_vocab = None


def vocab():
    """Candidate labels per source system, from the train split only: what a deployed system would have seen."""
    global _vocab
    if _vocab is None:
        v = collections.defaultdict(collections.Counter)
        for c in jsonl(TRAIN):
            for x in c.get("labels") or []:
                v[c["source"]][x] += 1
        _vocab = {s: [label for label, _ in cnt.most_common(60)] for s, cnt in v.items()}
    return _vocab


ONE_LINER = ("You are an SRE. From the telemetry, write ONE line (under 25 words) naming the failing component and its symptom, "
             "in the words an operator would type into an incident search. No JSON, no explanation.")


def one_liner(client, events):
    """The query the agent itself would type: a one-line symptom, written by the model from the telemetry. Measured
    against raw telemetry lines and keyword bags on the ratio-8 inputs, it is the only query that clears the pack's
    coverage bar at all (21/63 vs 1-6/63); none of them surfaces the true label in the top-3 cases more than 3/63."""
    text, _ = render(events, 40_000)
    t0 = time.time()
    r = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=60,
                                       messages=[{"role": "system", "content": ONE_LINER}, {"role": "user", "content": text}],
                                       extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    line = ((r.choices[0].message.content or "").strip().splitlines() or [""])[0][:200]
    return line, int(1000 * (time.time() - t0)), getattr(r.usage, "prompt_tokens", None)


def knowledge_for(events, client):
    import agent
    q, ms, toks = one_liner(client, events)
    q = q or symptom_query(events)[:240]
    text = agent.search_incident_knowledge(q)
    meta = {"query": q, "query_chars": len(q), "summary_ms": ms, "summary_tokens": toks}
    try:
        b = agent._kb.retrieve({"query": q}, case_k=3, solution_k=3)
        meta.update(status=b["knowledge_match"]["status"], coverage=b["knowledge_match"]["best_solution_coverage"], scenario=b["scenario"],
                    cards=[s["id"] for s in b["solutions"][:2]], case_titles=[c["title"] for c in b["cases"][:3]])
    except Exception as e:
        meta["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    return text, meta


def prompt(case, row, knowledge, budget):
    text, capped = render(row["telemetry"], budget, (row.get("window") or {}).get("boundary"))
    parts = [f"Source system: {case['source']}.", f"Candidate fault labels: {json.dumps(vocab().get(case['source'], []))}"]
    if knowledge:
        parts.append("Retrieved knowledge (hypotheses from other incidents, not facts about this one):\n" + knowledge)
    note = ", truncated to fit the context window" if capped else ""
    parts.append(f"Telemetry ({len(row['telemetry'])} events, compression {row['compression_ratio']}x{note}):\n{text}")
    parts.append("JSON:")
    return "\n\n".join(parts), capped


def parse_answer(text):
    """The JSON object, or what can be salvaged from one the output limit cut off (scenario and label come first)."""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    pred = {}
    for key in ("predicted_scenario", "predicted_label", "predicted_service"):
        k = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
        if k:
            pred[key] = k.group(1)
    k = re.search(r'"confidence"\s*:\s*([0-9.]+)', text)
    if k:
        pred["confidence"] = float(k.group(1))
    return pred


def ask(client, user, think):
    r = client.chat.completions.create(
        model=MODEL, temperature=0, max_tokens=4000 if think else 600,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        extra_body={"chat_template_kwargs": {"enable_thinking": bool(think)}})
    text = (r.choices[0].message.content or "").strip()
    return parse_answer(text), text, r.usage


class PowerSampler(threading.Thread):
    """GPU power at 1 Hz for the whole run; the report turns it into Wh per call."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path, self.stop_flag = path, threading.Event()

    def run(self):
        with open(self.path, "a") as f:
            while not self.stop_flag.is_set():
                try:
                    w = subprocess.run(["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
                                       capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[0]
                    f.write(f"{time.time():.1f},{float(w)}\n")
                    f.flush()
                except Exception:
                    pass
                self.stop_flag.wait(1.0)

    def stop(self):
        self.stop_flag.set()
        self.join(timeout=8)


def run(args):
    import agent  # noqa: F401  (the tool under test; imported once, before the threads)
    from openai import OpenAI
    client = OpenAI(base_url=f"{BASE}/v1", api_key="none", timeout=600)
    cases = {c["id"]: c for c in json.loads((OUT / "cases.json").read_text())}
    suffix = "-think" if args.think else ""
    done = {}
    for arm in args.arms:
        p = OUT / f"predictions-{arm}{suffix}.jsonl"
        done[arm] = {(r["id"], float(r["compression_ratio"])) for r in jsonl(p)} if p.exists() else set()
    jobs = [(arm, r, row) for r in args.ratios for row in jsonl(INPUTS / f"ratio_{r}.jsonl") for arm in args.arms
            if (row["id"], float(r)) not in done[arm]]
    print(f"{len(jobs)} calls to make ({', '.join(args.arms)}; ratios {args.ratios}; thinking={'on' if args.think else 'off'})", flush=True)
    sampler = PowerSampler(OUT / f"power{suffix}.csv")
    sampler.start()
    time.sleep(6)                                        # a few idle samples first, for the baseline
    lock = threading.Lock()
    files = {arm: open(OUT / f"predictions-{arm}{suffix}.jsonl", "a", encoding="utf-8") for arm in args.arms}

    def work(job):
        arm, r, row = job
        case = cases[row["id"]]
        knowledge, meta = knowledge_for(row["telemetry"], client) if arm == "rag" else (None, {})
        rec = dict(id=row["id"], compression_ratio=float(r), arm=arm, think=int(bool(args.think)), retrieval=meta)
        t0 = time.time()
        for budget in (TELEMETRY_CHARS, TELEMETRY_CHARS // 2, TELEMETRY_CHARS // 4):   # shrink if the server says too long
            user, capped = prompt(case, row, knowledge, budget)
            try:
                pred, text, usage = ask(client, user, args.think)
                rec.update(capped=capped, prompt_chars=len(user), predicted_label=pred.get("predicted_label"),
                           predicted_scenario=pred.get("predicted_scenario"), predicted_service=pred.get("predicted_service"),
                           confidence=pred.get("confidence"), raw=text[:400],
                           input_tokens=getattr(usage, "prompt_tokens", None), output_tokens=getattr(usage, "completion_tokens", None))
                rec.pop("error", None)
                break
            except Exception as e:
                rec.update(capped=capped, prompt_chars=len(user), error=f"{type(e).__name__}: {str(e)[:200]}",
                           predicted_label=None, predicted_scenario=None)
                if not re.search(r"length|token|too long|400", str(e), re.I):
                    break
        rec.update(t0=t0, t1=time.time(), latency_ms=int(1000 * (time.time() - t0)))
        with lock:
            files[arm].write(json.dumps(rec, ensure_ascii=False) + "\n")
            files[arm].flush()
        return rec

    with ThreadPoolExecutor(args.concurrency) as ex:
        for i, rec in enumerate(ex.map(work, jobs), 1):
            print(f"[{i}/{len(jobs)}] {rec['arm']} x{rec['compression_ratio']:g} {rec['id']} -> {rec.get('predicted_scenario')} / "
                  f"{str(rec.get('predicted_label'))[:40]}  tokens={rec.get('input_tokens')} {rec['latency_ms']} ms {rec.get('error', '')}", flush=True)
    sampler.stop()
    for f in files.values():
        f.close()


# --- report -------------------------------------------------------------------------------------------------------

def energy(recs, power):
    """Wh per call. energy_wh: each 1 s sample above the idle draw is split among the calls in flight at that moment
    (the cost per diagnosis at this concurrency). energy_wh_solo: the same sample charged in full to every call in
    flight (what one diagnosis would cost alone; an upper bound). Idle = the lowest sample, which the sampler takes
    before the first call; a percentile does not work because the GPU never idles during the run."""
    for r in recs:
        r["energy_wh"] = r["energy_wh_solo"] = 0.0
    if not power:
        return None
    idle = min(w for _, w in power)
    for t, w in power:
        inflight = [r for r in recs if r["t0"] <= t <= r["t1"]]
        for r in inflight:
            r["energy_wh"] += max(0.0, w - idle) / 3600.0 / len(inflight)
            r["energy_wh_solo"] += max(0.0, w - idle) / 3600.0
    return idle


def pct(vals):
    vals = [v for v in vals if v is not None]
    return f"{100 * sum(vals) / len(vals):.0f}% ({sum(vals)}/{len(vals)})" if vals else "-"


def lenient_correct(expected, predicted):
    """Strict match, or the truth label inside the prediction, or the prediction (3+ chars) inside the truth label: what
    a human grader would accept ("disk_full" for "disk", "pod failure on cartservice" for "pod failure")."""
    from blackout_ops_kb.evaluation import _label_correct, _normalize
    if _label_correct(expected, predicted):
        return True
    p = _normalize(predicted or "")
    return any((e := _normalize(x)) and (e in p or (len(p) >= 3 and p in e)) for x in expected)


def truth_services(t):
    md = t.get("metadata") or {}
    vals = []
    for k in ("service", "instance", "root_cause_service", "fault_object"):
        v = md.get(k)
        vals += v if isinstance(v, list) else ([v] if v else [])
    for e in md.get("entities") or []:
        vals.append(e if isinstance(e, str) else (e.get("name") if isinstance(e, dict) else str(e)))
    return [str(v) for v in vals if v]


def service_correct(truth_vals, pred):
    """The named component is the ground-truth service/pod/node, allowing pod suffixes and path prefixes."""
    import re as _re
    from blackout_ops_kb.evaluation import _normalize
    p = _normalize(pred or "")
    if len(p) < 3 or p in ("unknown", "none", "null"):
        return False
    for v in truth_vals:
        toks = {_normalize(v)} | {_normalize(x) for x in _re.split(r"[/:,\s]+", str(v)) if len(x) >= 3}
        if any(len(t) >= 3 and (t in p or p in t) for t in toks):
            return True
    return False


def report(args):
    from blackout_ops_kb import infer_scenario
    from blackout_ops_kb.evaluation import _label_correct, score_predictions
    truths = {t["id"]: t for t in jsonl(GT)}
    cases = json.loads((OUT / "cases.json").read_text())
    suffix = "-think" if args.think else ""
    ppath = OUT / f"power{suffix}.csv"
    power = [(float(a), float(b)) for a, b in csv.reader(open(ppath))] if ppath.exists() else []
    DOCS.mkdir(parents=True, exist_ok=True)
    arms, curves, idle = {}, {}, None
    for arm in args.arms:
        p = OUT / f"predictions-{arm}{suffix}.jsonl"
        if not p.exists():
            continue
        recs = jsonl(p)
        idle = energy(recs, power)
        for r in recs:
            t = truths[r["id"]]
            if r.get("predicted_label") is None and r.get("raw"):
                r.update({k: v for k, v in parse_answer(r["raw"]).items() if k in ("predicted_label", "predicted_scenario")})
            if r.get("predicted_scenario") not in ("disk_full", "crashloop", "dns", "other"):     # a label in the category slot
                r["predicted_scenario"] = infer_scenario(r.get("predicted_scenario"), r.get("predicted_label"))
            r["correct"] = _label_correct(t.get("labels") or [], r.get("predicted_label"))
            r["lenient"] = lenient_correct(t.get("labels") or [], r.get("predicted_label"))
            r["scenario_ok"] = r.get("predicted_scenario") == t["scenario"]
            r["has_service"] = bool(truth_services(t))
            r["service_ok"] = service_correct(truth_services(t), r.get("predicted_service")) if r["has_service"] else None
            r["truth_scenario"], r["source"] = t["scenario"], t["source"]
        scored = OUT / f"scored-{arm}{suffix}.jsonl"
        with open(scored, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps({k: r.get(k) for k in ("id", "compression_ratio", "predicted_label", "predicted_scenario",
                                                          "input_tokens", "latency_ms", "energy_wh")}) + "\n")
        curves[arm] = score_predictions(GT, scored, DOCS / f"curve-{arm}{suffix}.csv")
        arms[arm] = recs

    lines = [f"# Knowledge-pack RAG evaluation ({time.strftime('%Y-%m-%d')}{', thinking on' if args.think else ', thinking off'})", "",
             f"Held-out benchmark incidents from `kb/artifacts/eval_ground_truth.jsonl`, diagnosed by Nemotron-3-Nano-30B-A3B-NVFP4 on "
             f"TensorRT-LLM on the DGX Spark, offline. {len(cases)} cases: every disk_full / crashloop / dns case in the split plus a "
             f"per-source slice of the rest ({dict(collections.Counter(c['scenario'] for c in cases))}; sources "
             f"{dict(collections.Counter(c['source'] for c in cases))}). Two arms per input: `none` = telemetry only; `rag` = telemetry "
             f"plus what the agent's `search_incident_knowledge` returns for the symptoms in it. Same closed-set label vocabulary "
             f"(train split of each source) for both arms; scored by the pack's own scorer (`blackout_ops_kb.evaluation`). Energy = "
             f"nvidia-smi power at 1 Hz minus idle{f' ({idle:.1f} W)' if idle else ''}, shared among concurrent calls.", ""]
    lines += ["## Accuracy vs compression", "", "Strict = the pack's scorer (exact label, or the label inside the answer when it has 6+ characters). "
              "Lenient = strict, or truth label inside the answer, or answer inside the truth label. Wh = GPU energy above idle per diagnosis "
              f"at {args.concurrency}-way concurrency; solo = the same charged in full to each call (upper bound, sequential-equivalent).", "",
              "Service = the named failing component equals the ground-truth service/pod/node (cases that have one).", "",
              "| arm | ratio | cases | strict acc | lenient acc | scenario acc | service acc | avg prompt tokens | avg latency | Wh/diagnosis | Wh solo |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, rep in curves.items():
        for c in rep["curve"]:
            rs = [x for x in arms[arm] if x["compression_ratio"] == c["compression_ratio"]]
            lines.append(f"| {arm} | {c['compression_ratio']:g}x | {c['cases']} | {100 * c['diagnostic_accuracy']:.0f}% | "
                         f"{100 * sum(x['lenient'] for x in rs) / len(rs):.0f}% | {100 * sum(x['scenario_ok'] for x in rs) / len(rs):.0f}% | "
                         f"{pct([x['service_ok'] for x in rs])} | "
                         f"{c['avg_input_tokens']:.0f} | {c['avg_latency_ms'] / 1000:.1f} s | {c['avg_energy_wh']:.3f} | "
                         f"{sum(x['energy_wh_solo'] for x in rs) / len(rs):.3f} |")
    for arm, rep in curves.items():
        sel = rep.get("selected_working_point")
        if sel:
            lines.append(f"\nWorking point ({arm}, {rep['selection_rule']}): {sel['compression_ratio']:g}x, {100 * sel['diagnostic_accuracy']:.0f}%, "
                         f"{sel['avg_input_tokens']:.0f} tokens.")
    ratios = sorted({r["compression_ratio"] for recs in arms.values() for r in recs})
    lines += ["", "## By scenario (lenient accuracy, none → rag)", "", "| scenario | n | " + " | ".join(f"{r:g}x" for r in ratios) + " |",
              "|---|---|" + "---|" * len(ratios)]
    for sc in ("disk_full", "crashloop", "dns", "other"):
        n = sum(1 for c in cases if c["scenario"] == sc)
        cells = []
        for r in ratios:
            a = [x["lenient"] for x in arms.get("none", []) if x["truth_scenario"] == sc and x["compression_ratio"] == r]
            b = [x["lenient"] for x in arms.get("rag", []) if x["truth_scenario"] == sc and x["compression_ratio"] == r]
            cells.append(f"{100 * sum(a) / len(a):.0f}% → {100 * sum(b) / len(b):.0f}%" if a and b else "-")
        lines.append(f"| {sc} | {n} | " + " | ".join(cells) + " |")
    lines += ["", "## By source (over all ratios, none → rag)", "", "| source | n | strict | lenient | scenario | service |", "|---|---|---|---|---|---|"]
    for src in sorted({c["source"] for c in cases}):
        a = [x for x in arms.get("none", []) if x["source"] == src]
        b = [x for x in arms.get("rag", []) if x["source"] == src]
        lines.append(f"| {src} | {sum(1 for c in cases if c['source'] == src)} | {pct([x['correct'] for x in a])} → {pct([x['correct'] for x in b])} | "
                     f"{pct([x['lenient'] for x in a])} → {pct([x['lenient'] for x in b])} | {pct([x['scenario_ok'] for x in a])} → {pct([x['scenario_ok'] for x in b])} | "
                     f"{pct([x['service_ok'] for x in a])} → {pct([x['service_ok'] for x in b])} |")
    if "rag" in arms:
        rag = arms["rag"]
        lines += ["", "## What retrieval did (rag arm)", ""]
        matched = [x["retrieval"].get("status") == "matched" for x in rag]
        target = [x for x in rag if x["truth_scenario"] != "other"]
        card_ok = [x["retrieval"].get("scenario") == x["truth_scenario"] for x in target]
        title_hit = [any(_label_correct(truths[x["id"]]["labels"], [t]) for t in x["retrieval"].get("case_titles", [])) for x in rag]
        ms = [x["retrieval"].get("summary_ms") for x in rag if x["retrieval"].get("summary_ms")]
        lines += [f"- Query = a one-line symptom the model wrote from the telemetry (median {sorted(ms)[len(ms) // 2] / 1000:.1f} s extra per diagnosis)." if ms else "",
                  f"- `matched` (a remediation card cleared the 0.34 coverage bar): {pct(matched)} of inputs.",
                  f"- Retrieval's scenario guess equals the truth scenario, target scenarios only: {pct(card_ok)}.",
                  f"- One of the three similar historical cases carries the true label in its title: {pct(title_hit)} of inputs.",
                  f"- Lenient accuracy when a similar case carried the label: {pct([x['lenient'] for x, h in zip(rag, title_hit) if h])} vs "
                  f"{pct([x['lenient'] for x, h in zip(rag, title_hit) if not h])} when none did."]
    errs = {arm: sum(1 for x in recs if x.get("error")) for arm, recs in arms.items()}
    capped = {arm: sum(1 for x in recs if x.get("capped")) for arm, recs in arms.items()}
    lines += ["", f"Errors: {errs}. Prompts truncated to the window: {capped}. Predictions and the power trace: `logs/kb-eval/` on the Spark; "
              f"curves: `docs/kb-eval/curve-*.csv`."]
    (ROOT / "docs" / f"kb-eval{suffix}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (DOCS / f"summary{suffix}.json").write_text(json.dumps({"curves": curves, "errors": errs, "capped": capped, "idle_w": idle}, indent=1))
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "run", "report", "all"])
    ap.add_argument("--ratios", default=",".join(map(str, RATIOS)))
    ap.add_argument("--arms", default="none,rag")
    ap.add_argument("--think", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--ids", default="", help="prepare only these case ids, appending to the existing inputs")
    a = ap.parse_args()
    a.ids = {x for x in a.ids.split(",") if x}
    a.ratios = [int(x) for x in a.ratios.split(",") if x]
    a.arms = [x for x in a.arms.split(",") if x]
    OUT.mkdir(parents=True, exist_ok=True)
    for step in ([a.cmd] if a.cmd != "all" else ["prepare", "run", "report"]):
        t0 = time.time()
        {"prepare": prepare, "run": run, "report": report}[step](a)
        print(f"== {step} done in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
