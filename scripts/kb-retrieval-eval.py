"""Retrieval-only evaluation of the knowledge pack: no model call, seconds per run.

  uv run --no-sync scripts/kb-retrieval-eval.py build      # v1 pack -> v2 corpus (logs/kb-retrieval-eval/corpus_v2.jsonl)
  uv run --no-sync scripts/kb-retrieval-eval.py prep-dev   # dev split -> ratio-8 inputs (same pipeline as kb-eval prepare)
  uv run --no-sync scripts/kb-retrieval-eval.py prep-train # step 2: each train case's own telemetry -> observed text
  uv run --no-sync scripts/kb-retrieval-eval.py build-v3   # v2 corpus + observed texts -> corpus_v3*.jsonl
  uv run --no-sync scripts/kb-retrieval-eval.py run        # every system x query x split -> docs/kb-retrieval-eval.md

Why. The end-to-end evaluation (scripts/kb-eval.py) takes 50 minutes and mixes two questions: does retrieval bring
back the right knowledge, and does the model use it. This answers the first one alone, so a retrieval change can be
judged in seconds and only the promising ones go to the model.

Splits. `dev` (the pack's dev split, ITBench excluded as in kb-eval) is where thresholds are chosen; `eval` (the 63
held-out cases kb-eval uses, ratio 8) is reported and never tuned on. Neither split is in the index.

Metrics, per (system, query, split):
- label@k: one of the top-k retrieved cases carries the true fault label (the pack's own label matcher). Baseline:
  the most frequent train label of the case's source system, i.e. what a lookup table with no retrieval gets.
- scenario@1 (target): on disk_full / crashloop / dns cases, the top case's scenario equals the true one. (Over all
  cases it is dominated by `other`, which 85% of eval cases are, so it is not reported.)
- matched: the pack said `matched`. Of those, correct = the first card's scenario equals the true scenario. Reported
  separately on target-scenario cases (disk_full, crashloop, dns: a correct match is possible) and on `other` cases
  (no card is about them, so every match there is a false alarm; the two `other` cards, connection-refused and TLS,
  are counted as false alarms too, which is conservative).
Truth scenarios are the corrected ones (retrieval_v2.scenario_v2) for every system: disk/IO stress is not a full disk.
"""
import argparse
import collections
import importlib.util
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kb" / "src"))
from blackout_ops_kb.api import KnowledgeBase                       # noqa: E402
from blackout_ops_kb.evaluation import _label_correct               # noqa: E402
from blackout_ops_kb import retrieval_v2 as v2                      # noqa: E402

ART = ROOT / "kb" / "artifacts"
OUT = ROOT / "logs" / "kb-retrieval-eval"
CORPUS = OUT / "corpus_v2.jsonl"
DEV_INPUTS = OUT / "dev_ratio_8.jsonl"
TRAIN_OBS = OUT / "train_observed.jsonl"
CORPUS_V3 = OUT / "corpus_v3.jsonl"
CORPUS_V3_NOENT = OUT / "corpus_v3_noent.jsonl"
EVAL_INPUTS = ROOT / "logs" / "kb-eval" / "inputs" / "ratio_8.jsonl"
ONELINERS = ROOT / "logs" / "kb-eval" / "predictions-rag.jsonl"
REPORT = ROOT / "docs" / "kb-retrieval-eval.md"
TARGET = {"disk_full", "crashloop", "dns"}


def jsonl(p):
    return [json.loads(l) for l in pathlib.Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def kbeval():
    spec = importlib.util.spec_from_file_location("kbeval", ROOT / "scripts" / "kb-eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- build / prep ---------------------------------------------------------------------------------------------------

def build(_):
    stats = v2.build_v2_corpus(ART / "blackout_ops_kb.sqlite", CORPUS)
    print(json.dumps(stats, indent=1))


def compressed_telemetry(t, r=8):
    """One case's raw telemetry through kb-eval prepare's pipeline at ratio r: (window, events). Raises on no data."""
    from blackout_ops_kb.compression import compress_events
    from blackout_ops_kb.evaluation import DEFAULT_KEYWORDS
    from blackout_ops_kb.evidence import load_evidence
    raw_root = ROOT / "datasets" / "raw"
    ev = load_evidence(t, raw_root, max_events=2000)
    window = dict(ev.get("window") or {})
    if t["source"] == "RCAEval":
        f = raw_root / t["source_path"] / "inject_time.txt"
        if f.exists():
            window["boundary"] = float(f.read_text().strip())
    summary = [e for e in ev["events"] if e.get("kind") == "metric" and e.get("source_file") in ("apm", "infra")]
    wide = [e for e in ev["events"] if e not in summary and e.get("kind") in ("metrics", "metric")
            and str(e.get("message", "")).startswith('{"time"')]
    rest = [e for e in ev["events"] if e not in summary and e not in wide]
    c = compress_events(rest, target_ratio=float(r), keywords=DEFAULT_KEYWORDS, window_start=window.get("start"),
                        window_end=window.get("end")) if rest else {"events": []}
    return window, summary + wide[::r] + c["events"]


def prep_dev(_):
    """Ratio-8 inputs for the dev split, built exactly as kb-eval prepare builds the eval inputs."""
    from blackout_ops_kb.evidence import EvidenceUnavailable
    OUT.mkdir(parents=True, exist_ok=True)
    cases = [c for c in jsonl(ART / "cases_dev.jsonl") if not c["source"].startswith("ITBench")]
    skipped = 0
    with open(DEV_INPUTS, "w", encoding="utf-8") as s:
        for i, t in enumerate(cases, 1):
            t0 = time.time()
            try:
                window, events = compressed_telemetry(t)
            except (EvidenceUnavailable, OSError, ValueError) as e:
                skipped += 1
                print(f"skip {t['id']}: {str(e)[:120]}", flush=True)
                continue
            s.write(json.dumps({"id": t["id"], "compression_ratio": 8, "window": window, "telemetry": events},
                               ensure_ascii=False) + "\n")
            print(f"[{i}/{len(cases)}] {t['id']} {time.time() - t0:.1f}s", flush=True)
    print(f"dev inputs: {len(cases) - skipped} written, {skipped} skipped -> {DEV_INPUTS}")


def _observe(t):
    try:
        _, events = compressed_telemetry(t)
    except Exception as e:                               # noqa: BLE001  (any loader failure: the case keeps its v1 text)
        return {"id": t["id"], "error": f"{type(e).__name__}: {str(e)[:120]}"}
    return {"id": t["id"], "observed": v2.incident_query(events),
            "observed_noent": v2.incident_query(events, entities=False)}


def prep_train(args):
    """Step 2. Every train case's own telemetry, compressed as the eval inputs are, reduced by the same extractor the
    query uses, so documents and queries share a vocabulary. Train split only: dev and eval never enter the index.
    Resumable: ids already in the output are skipped."""
    from concurrent.futures import ProcessPoolExecutor
    OUT.mkdir(parents=True, exist_ok=True)
    done = {r["id"] for r in jsonl(TRAIN_OBS)} if TRAIN_OBS.exists() else set()
    cases = [c for c in jsonl(ART / "cases_train.jsonl") if c["id"] not in done]
    print(f"{len(cases)} train cases to observe ({len(done)} done), {args.workers} workers", flush=True)
    t0, n = time.time(), 0
    with open(TRAIN_OBS, "a", encoding="utf-8") as f, ProcessPoolExecutor(args.workers) as ex:
        for rec in ex.map(_observe, cases, chunksize=4):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            n += 1
            if n % 25 == 0 or "error" in rec:
                print(f"[{n}/{len(cases)}] {time.time() - t0:.0f}s {rec['id']} {rec.get('error', '')}", flush=True)
    recs = jsonl(TRAIN_OBS)
    print(f"train observed: {sum('observed' in r for r in recs)} ok, {sum('error' in r for r in recs)} failed -> {TRAIN_OBS}")


def build_v3(_):
    recs = [r for r in jsonl(TRAIN_OBS) if r.get("observed")]
    for path, key in ((CORPUS_V3, "observed"), (CORPUS_V3_NOENT, "observed_noent")):
        stats = v2.build_v2_corpus(ART / "blackout_ops_kb.sqlite", path, observed={r["id"]: r[key] for r in recs})
        print(path.name, json.dumps(stats))


# --- run ------------------------------------------------------------------------------------------------------------

def load_split(name):
    if name == "eval":
        truth = {t["id"]: t for t in jsonl(ART / "eval_ground_truth.jsonl")}
        rows = jsonl(EVAL_INPUTS)
    else:
        truth = {t["id"]: t for t in jsonl(ART / "cases_dev.jsonl")}
        rows = jsonl(DEV_INPUTS) if DEV_INPUTS.exists() else []
    out = []
    for r in rows:
        t = truth[r["id"]]
        out.append({"id": r["id"], "source": t["source"], "labels": t.get("labels") or [], "metadata": t.get("metadata") or {},
                    "scenario": v2.scenario_v2(t["scenario"], t.get("labels") or [], t.get("title", "")),
                    "scenario_v1": t["scenario"], "events": r["telemetry"]})
    return out


def queries(split, cases):
    """Three ways to turn an incident into a query: the model-written one-liner kb-eval used (eval split only; it
    needs the model, so it is read back from that run), kb-eval's symptom-line fallback, and v2's deterministic
    metric-and-symptom extractor."""
    ke = kbeval()
    out = {"symptom-lines": {c["id"]: ke.symptom_query(c["events"])[:240] for c in cases},
           "incident-v2": {c["id"]: v2.incident_query(c["events"]) for c in cases},
           "incident-v2-noent": {c["id"]: v2.incident_query(c["events"], entities=False) for c in cases}}
    if split == "eval" and ONELINERS.exists():
        one = {}
        for p in jsonl(ONELINERS):
            if float(p["compression_ratio"]) == 8.0 and p.get("arm") == "rag" and str(p.get("think")) in ("0", "False"):
                q = p["retrieval"].get("query") if isinstance(p["retrieval"], dict) else None
                if q is None:                                   # stored as a Python repr by kb-eval
                    import ast
                    try:
                        q = ast.literal_eval(p["retrieval"]).get("query")
                    except (ValueError, SyntaxError):
                        q = None
                if q:
                    one[p["id"]] = q
        if one:
            out["model-one-liner"] = one
    return out


def majority_labels():
    v = collections.defaultdict(collections.Counter)
    for c in jsonl(ART / "cases_train.jsonl"):
        for x in c.get("labels") or []:
            v[c["source"]][x] += 1
    return {s: [label for label, _ in cnt.most_common(5)] for s, cnt in v.items()}


def case_labels_v1():
    return {c["id"]: c.get("labels") or [] for c in jsonl(ART / "cases_train.jsonl")}


def score(system, retrieve, cases, qmap, v1_labels=None):
    m = collections.Counter()
    for c in cases:
        q = qmap.get(c["id"]) or ""
        b = retrieve(q, c)
        got = b["cases"]
        labs = [(x.get("labels") or (v1_labels or {}).get(x["id"], [])) for x in got]
        for k in (1, 3, 5):
            m[f"label@{k}"] += any(_label_correct(c["labels"], l) for l in labs[:k] if l)
        tgt = c["scenario"] in TARGET
        m["scenario@1_target"] += tgt and bool(got) and got[0]["scenario"] == c["scenario"]
        matched = b["knowledge_match"]["status"] == "matched"
        card = (b["solutions"] or [{}])[0].get("scenario")
        m["n"] += 1
        m["n_target"] += tgt
        m["matched"] += matched
        m["matched_target"] += matched and tgt
        m["matched_correct"] += matched and tgt and card == c["scenario"]
        m["matched_other"] += matched and not tgt
    return m


ALL = ("symptom-lines", "incident-v2", "model-one-liner")
MAIN = ("incident-v2",)


def systems(corpus_ok, thresholds):
    """name -> (retrieve(query), the query types it is scored on). Ablations run on the main query only."""
    v1 = KnowledgeBase(ART / "blackout_ops_kb.sqlite")
    out = {"v1 (current)": (lambda q, c=None: v1.retrieve({"query": q}, case_k=5, solution_k=3), ALL)}
    def mk(corpus, unseen=False, **kw):
        kb = v2.KnowledgeBaseV2(corpus, **kw)
        if not unseen:
            return lambda q, c=None: kb.retrieve({"query": q}, case_k=5, solution_k=3)
        def leave_out(q, c):
            with kb.excluding(siblings(c)):
                return kb.retrieve({"query": q}, case_k=5, solution_k=3)
        return leave_out
    t = thresholds
    if corpus_ok:
        out["v2"] = (mk(CORPUS, minimum_solution_coverage=t["card"]), ALL)
        out["v2, query-side coverage"] = (mk(CORPUS, coverage="query", minimum_solution_coverage=t["query"]), MAIN)
        out["v2 + v1 hard scenario filter"] = (mk(CORPUS, hard_filter=True, minimum_solution_coverage=t["card"]), MAIN)
        out["v2, label-only cases at half weight"] = (mk(CORPUS, no_evidence_weight=0.5, minimum_solution_coverage=t["card"]), MAIN)
        out["v2, label-only cases dropped"] = (mk(CORPUS, no_evidence_weight=0.0, minimum_solution_coverage=t["card"]), MAIN)
    if CORPUS_V3.exists():
        out["v3: cases indexed by observed telemetry"] = (mk(CORPUS_V3, index="observed", minimum_solution_coverage=t["card"]), ALL)
        out["v3: observed + original text"] = (mk(CORPUS_V3, index="both", minimum_solution_coverage=t["card"]), MAIN)
        out["v3, UNSEEN: same fault on same service hidden"] = (mk(CORPUS_V3, unseen=True, index="observed",
                                                                 minimum_solution_coverage=t["card"]), MAIN)
        out["v3: observed, no entity names"] = (mk(CORPUS_V3_NOENT, index="observed", minimum_solution_coverage=t["card"]),
                                                ("incident-v2-noent",))
        if t.get("phrase") is not None:
            out[f"v3 + card by symptom phrase (bar {t['phrase']})"] = (mk(CORPUS_V3, index="observed", coverage="phrase",
                                                                         phrase_bar=t["phrase"]), MAIN)
        if t.get("vote"):
            out[f"v3 + card by case vote (>= {t['vote']}/5)"] = (mk(CORPUS_V3, index="observed", vote=t["vote"]), MAIN)
    return out


_train = None


def siblings(c):
    """Train ids that are this case's fault again on the same service (retrieval_v2.sibling_ids)."""
    global _train
    if _train is None:
        _train = jsonl(ART / "cases_train.jsonl")
    if "_sib" not in c:
        c["_sib"] = v2.sibling_ids(c, _train)
    return c["_sib"]


def pick_thresholds(dev, qmap):
    """On dev only: the coverage bar that maximises correct matches minus wrong ones (wrong = matched with a card of
    another scenario, or any match on an `other` case). Ties go to the higher, more conservative bar."""
    best = {}
    for mode in ("card", "query"):
        kb = v2.KnowledgeBaseV2(CORPUS, coverage=mode)
        cov = []
        for c in dev:
            b = kb.retrieve({"query": qmap.get(c["id"]) or ""}, case_k=1, solution_k=3, minimum_solution_coverage=2.0)
            cand = b["weak_solution_candidates"]
            cov.append((cand[0]["match_coverage"] if cand else 0.0, cand[0]["scenario"] if cand else None, c["scenario"]))
        grid = [round(0.05 * i, 2) for i in range(2, 20)]
        def gain(th):
            return sum((1 if (s == truth and truth in TARGET) else -1) for v, s, truth in cov if v >= th)
        best[mode] = max(grid, key=lambda th: (gain(th), th))
        best[f"{mode}_gain"] = gain(best[mode])
        best[f"{mode}_sweep"] = [(th, sum(1 for v, s, tr in cov if v >= th and s == tr and tr in TARGET),
                                  sum(1 for v, s, tr in cov if v >= th and not (s == tr and tr in TARGET))) for th in grid]
    if CORPUS_V3.exists():
        def vote_gain(v):
            kb = v2.KnowledgeBaseV2(CORPUS_V3, index="observed", vote=v)
            g = c_ok = c_bad = 0
            for c in dev:
                b = kb.retrieve({"query": qmap.get(c["id"]) or ""}, case_k=5, solution_k=3)
                if b["knowledge_match"]["status"] == "matched":
                    ok = b["solutions"][0]["scenario"] == c["scenario"]
                    c_ok, c_bad = c_ok + ok, c_bad + (not ok)
            return c_ok - c_bad, c_ok, c_bad
        kbp = v2.KnowledgeBaseV2(CORPUS_V3, index="observed", coverage="phrase")
        pc = []
        for c in dev:
            b = kbp.retrieve({"query": qmap.get(c["id"]) or ""}, case_k=1, solution_k=3, minimum_solution_coverage=1e9)
            cand = b["weak_solution_candidates"]
            pc.append((cand[0]["match_coverage"] if cand else 0.0, cand[0]["scenario"] if cand else None, c["scenario"]))
        pgrid = [2 * i for i in range(1, 16)]
        psweep = [(t_, sum(1 for v, sc, tr in pc if v >= t_ and sc == tr and tr in TARGET),
                   sum(1 for v, sc, tr in pc if v >= t_ and not (sc == tr and tr in TARGET))) for t_ in pgrid]
        best["phrase_sweep"] = psweep
        gainful = [x for x in psweep if x[1] > x[2]]
        clean = [x for x in psweep if x[2] == 0]
        best["phrase"] = (max(gainful, key=lambda x: (x[1] - x[2], x[0]))[0] if gainful else min(clean)[0] if clean else None)
        best["phrase_rule"] = "most correct minus wrong" if gainful else "lowest bar with no wrong match on dev"
        sweep = {v: vote_gain(v) for v in (2, 3, 4, 5)}
        best["vote_sweep"] = [(v, ok, bad) for v, (_, ok, bad) in sweep.items()]
        v, (g, _, _) = max(sweep.items(), key=lambda kv: (kv[1][0], kv[0]))
        best["vote"] = v if g > 0 else 0
    return best


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "–"


def run(args):
    corpus_ok = CORPUS.exists()
    dev = load_split("dev")
    ev = load_split("eval")
    qs = {"dev": queries("dev", dev), "eval": queries("eval", ev)}
    th = pick_thresholds(dev, qs["dev"]["incident-v2"]) if (corpus_ok and dev) else {"card": 0.34, "query": 0.34}
    print("thresholds chosen on dev:", {k: v for k, v in th.items() if not k.endswith("_sweep")})
    if th.get("vote_sweep"):
        print("vote sweep on dev (votes, correct, wrong):", th["vote_sweep"])
    v1l = case_labels_v1()
    maj = majority_labels()
    lines = ["# Knowledge-pack retrieval, measured without the model (step 0 / step 1)", "",
             f"Generated by `scripts/kb-retrieval-eval.py run` on {time.strftime('%Y-%m-%d %H:%M')}. Definitions in the "
             "script's docstring. Coverage bars for v2 were chosen on dev (incident-v2 query) and fixed before the eval "
             f"split was scored: card-side {th['card']}, query-side {th['query']}. v1 keeps its 0.34.", ""]
    if "card_sweep" in th:
        lines += ["Dev sweep of the card-side coverage bar (incident-v2 query): matches that would be correct / wrong at each bar.",
                  "", "| bar | " + " | ".join(str(b) for b, _, _ in th["card_sweep"][:10]) + " |",
                  "|---|" + "---|" * 10, "| correct / wrong | " + " | ".join(f"{c} / {w}" for _, c, w in th["card_sweep"][:10]) + " |", ""]
    if th.get("phrase_sweep"):
        lines += [f"Dev sweep of the symptom-phrase bar (v3; sum of IDF of a card symptom phrase found whole in the query): "
                  f"correct / wrong matches. Chosen: {th['phrase']} ({th['phrase_rule']}).", "",
                  "| bar | " + " | ".join(str(b) for b, _, _ in th["phrase_sweep"]) + " |", "|---|" + "---|" * len(th["phrase_sweep"]),
                  "| correct / wrong | " + " | ".join(f"{a} / {b}" for _, a, b in th["phrase_sweep"]) + " |", ""]
    if th.get("vote_sweep"):
        lines += ["Dev sweep of the case vote (v3, observed index): matches correct / wrong when at least N of the top 5 "
                  "similar incidents share a target scenario. "
                  + (f"Chosen: {th['vote']}." if th.get("vote") else "No N gives more correct than wrong matches; not used."),
                  "", "| N | " + " | ".join(str(v) for v, _, _ in th["vote_sweep"]) + " |", "|---|" + "---|" * len(th["vote_sweep"]),
                  "| correct / wrong | " + " | ".join(f"{a} / {b}" for _, a, b in th["vote_sweep"]) + " |", ""]
    results = {}
    for split, cases in (("dev", dev), ("eval", ev)):
        if not cases:
            continue
        base = sum(_label_correct(c["labels"], (maj.get(c["source"]) or [None])[0]) for c in cases)
        base5 = sum(_label_correct(c["labels"], maj.get(c["source"]) or []) for c in cases)
        n_t = sum(c["scenario"] in TARGET for c in cases)
        hidden = [len(siblings(c)) for c in cases]
        lines += [f"## {split} split: {len(cases)} cases ({n_t} target-scenario)", "",
                  f"UNSEEN rows hide, per case, the train incidents with the same fault on the same service: "
                  f"{sum(1 for h in hidden if h)} of {len(cases)} cases have any, {sum(hidden) / len(cases):.1f} hidden on "
                  f"average (max {max(hidden)}).", "",
                  f"Lookup-table baseline (the most frequent train labels of the source, no retrieval): label@1 "
                  f"{pct(base, len(cases))}, label@5 {pct(base5, len(cases))}.", "",
                  "| system | query | label@1 | label@3 | label@5 | scenario@1 (target) | matched | correct match (target) | "
                  "match precision | false alarms (other) |", "|---|---|---|---|---|---|---|---|---|---|"]
        for sname, (fn, allowed) in systems(corpus_ok, th).items():
            for qname, qmap in qs[split].items():
                if qname not in allowed:
                    continue
                m = score(sname, fn, cases, qmap, v1l)
                results[f"{split}|{sname}|{qname}"] = dict(m)
                n = m["n"]
                lines.append(f"| {sname} | {qname} | {pct(m['label@1'], n)} | {pct(m['label@3'], n)} | {pct(m['label@5'], n)} | "
                             f"{m['scenario@1_target']}/{m['n_target']} | {pct(m['matched'], n)} | {m['matched_correct']}/{m['n_target']} | "
                             f"{pct(m['matched_correct'], m['matched'])} | {m['matched_other']}/{n - m['n_target']} |")
        lines.append("")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({"thresholds": th, "results": results}, indent=1))
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "prep-dev", "prep-train", "build-v3", "run"])
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    {"build": build, "prep-dev": prep_dev, "prep-train": prep_train, "build-v3": build_v3, "run": run}[a.cmd](a)


if __name__ == "__main__":
    main()
