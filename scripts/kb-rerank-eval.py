"""Offline reranking experiment (docs/rag-rerank-plan-20261021.md, Gates A and B) on the knowledge layers.

  .venv/bin/python scripts/kb-rerank-eval.py --dry-run     Gate A only: BM25 candidate recall, no model, writes nothing large
  .venv/bin/python scripts/kb-rerank-eval.py               Gate B: BM25 top-N -> nvidia/llama-nemotron-rerank-1b-v2 -> top-1

Two questions, both answered on data the ranker never saw:
  1. Community layer: on the held-out half B of datasets/derived/stackexchange_ops_qa_20261022/holdout.jsonl (the same
     split rule as the floor experiment: shuffle with seed 1, second half) and on the 38 probe sentences, does
     reranking the BM25 top-20 raise category@1 above BM25's 0.70, and does the reranker's score separate right from
     wrong well enough to say "no reference" (BM25's lexical coverage could not: docs/kb-community-20261022.md)?
  2. Runbooks: for the in-domain symptoms in scripts/rag.py EVAL versus the 38 out-of-domain probes, do reranker scores
     separate, so that docker_logs could say "no runbook fits" instead of attaching the nearest one?
Thresholds are reported as a table (tuned on half A, read off on half B), never chosen by the script.

Model: nvidia/llama-nemotron-rerank-1b-v2 (not gated; transformers>=4.44 with trust_remote_code; ~2.5 GB in the HF
cache, pass --allow-patterns so pytorch_model.bin is not downloaded twice). Usage follows the model card verbatim:
pairs are formatted "question:{q} \\n \\n passage:{p}", left padding, bf16, logits are the scores.
Written 2026-10-22 03:30 while the DGX root disk had 1.3 GB free: --dry-run was run, the model path was not.
"""
import argparse
import json
import math
import pathlib
import random
import sys
import time
from collections.abc import Sequence
from typing import TypedDict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import community_knowledge as ck  # noqa: E402

HOLDOUT = ROOT / "datasets" / "derived" / "stackexchange_ops_qa_20261022" / "holdout.jsonl"
MODEL = "nvidia/llama-nemotron-rerank-1b-v2"
# vLLM's /rerank returns sigmoid scores in [0, 1] (the transformers route returns raw logits); thresholds are on that scale.
THRESHOLDS = (None, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9)
ROWS_OUT = ROOT / "logs" / "kb-rerank-rows.json"      # per-query (set, category, bm25 top category, rerank top category, score)


class RankMetrics(TypedDict):
    category_mrr: float
    category_ndcg_at_5: float
    category_recall_at_5: float
    category_recall_at_10: float


def rank_metrics(cat: str, ordered_categories: Sequence[str]) -> RankMetrics:
    matches = [candidate == cat for candidate in ordered_categories]
    first_match = next((rank for rank, match in enumerate(matches, 1) if match), None)
    dcg = sum(match / math.log2(rank + 1) for rank, match in enumerate(matches[:5], 1))
    ideal_matches = min(sum(matches), 5)
    ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_matches + 1))
    return {
        "category_mrr": 1 / first_match if first_match is not None else 0.0,
        "category_ndcg_at_5": dcg / ideal_dcg if ideal_dcg else 0.0,
        "category_recall_at_5": float(any(matches[:5])),
        "category_recall_at_10": float(any(matches[:10])),
    }


def aggregate_rank_metrics(metrics: Sequence[RankMetrics]) -> RankMetrics:
    n = max(1, len(metrics))
    return {
        "category_mrr": sum(row["category_mrr"] for row in metrics) / n,
        "category_ndcg_at_5": sum(row["category_ndcg_at_5"] for row in metrics) / n,
        "category_recall_at_5": sum(row["category_recall_at_5"] for row in metrics) / n,
        "category_recall_at_10": sum(row["category_recall_at_10"] for row in metrics) / n,
    }


def percentile(values: Sequence[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def q_gap():
    src = (ROOT / "scripts" / "kb-community.py").read_text(encoding="utf-8")
    ns = {"__file__": str(ROOT / "scripts" / "kb-community.py")}
    exec(src.split("# ---------------------------------------------------------------- HTML")[0], ns)  # Q_GAP, Q_STAGE, constants
    return ns["Q_GAP"], ns["Q_STAGE"]


def halves():
    held = [json.loads(l) for l in HOLDOUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    rng = random.Random(1)
    rng.shuffle(held)
    rows = [(r["category"], " ".join([r["title"], *r["symptom_lines"]])) for r in held]
    return rows[: len(rows) // 2], rows[len(rows) // 2:]


def passage(d):
    return ck.passage(d)          # honours RERANK_PASSAGE, like the live path


class HttpReranker:
    """The model card's vLLM route: a `vllm serve nvidia/llama-nemotron-rerank-1b-v2 --trust-remote-code --chat-template
    nemotron-rerank.jinja` sidecar (scripts/serve-rerank.sh) and its /rerank endpoint. Used when RERANK_URL is set; the
    in-process transformers route below needs transformers 4.x (the remote code breaks on 5.x: no max_position_embeddings)."""

    def __init__(self, url):
        import urllib.request
        self.url, self.request = url, urllib.request

    def score(self, query, passages, max_length=512):
        body = json.dumps({"model": "rerank", "query": query, "documents": passages, "top_n": len(passages)}).encode()
        req = self.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with self.request.urlopen(req, timeout=120) as r:
            results = json.loads(r.read())["results"]
        scores = [0.0] * len(passages)
        for item in results:
            scores[item["index"]] = float(item["relevance_score"])
        return scores


class Reranker:
    def __init__(self, device=None):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        self.tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True, padding_side="left")
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.model = AutoModelForSequenceClassification.from_pretrained(MODEL, trust_remote_code=True, torch_dtype=torch.bfloat16).eval()
        if self.model.config.pad_token_id is None:
            self.model.config.pad_token_id = self.tok.eos_token_id
        self.model = self.model.to(self.device)

    def score(self, query, passages, max_length=512):
        texts = [f"question:{query} \n \n passage:{p}" for p in passages]
        batch = self.tok(texts, padding=True, truncation=True, return_tensors="pt", max_length=max_length)
        batch = {k: v.to(self.device) for k, v in batch.items()}
        with self.torch.inference_mode():
            return self.model(**batch).logits.view(-1).float().cpu().tolist()


def outcome(top_cat, cat, score, threshold):
    if top_cat is None or (threshold is not None and score is not None and score < threshold):
        return "none"
    return "right" if top_cat == cat else "wrong"


def tally(rows_out, thresholds):
    """rows_out: [(cat, bm25_top_cat, rerank_top_cat, rerank_top_score)] -> table per threshold."""
    table = {}
    n = max(1, len(rows_out))
    for th in thresholds:
        c = {"right": 0, "wrong": 0, "none": 0}
        for cat, _, rr_cat, rr_score in rows_out:
            c[outcome(rr_cat, cat, rr_score, th)] += 1
        table["bm25" if th is None and False else (f">={th}" if th is not None else "no floor")] = {k: round(v / n, 3) for k, v in c.items()}
    bm = {"right": 0, "wrong": 0, "none": 0}
    for cat, bm_cat, _, _ in rows_out:
        bm[outcome(bm_cat, cat, None, None)] += 1
    table["bm25 top-1"] = {k: round(v / n, 3) for k, v in bm.items()}
    return table


def run_layer(idx, rows, reranker, n_cand, label):
    out, recall20, lat = [], 0, []
    bm25_rank_metrics, rerank_rank_metrics = [], []
    for cat, q in rows:
        cands = idx.search(q, k=n_cand, min_shared=1)
        bm_cat = cands[0][2]["category"] if cands else None
        bm25_rank_metrics.append(rank_metrics(cat, [d["category"] for _, _, d in cands]))
        if any(d["category"] == cat for _, _, d in cands):
            recall20 += 1
        rr_cat, rr_score = None, None
        reranked_categories = []
        if reranker and cands:
            t0 = time.perf_counter()
            scores = reranker.score(q, [passage(d) for _, _, d in cands])
            lat.append(time.perf_counter() - t0)
            best = max(range(len(cands)), key=lambda i: scores[i])
            rr_cat, rr_score = cands[best][2]["category"], scores[best]
            order = sorted(range(len(cands)), key=lambda i: scores[i], reverse=True)
            reranked_categories = [cands[i][2]["category"] for i in order]
        if reranker:
            rerank_rank_metrics.append(rank_metrics(cat, reranked_categories))
        out.append((cat, bm_cat, rr_cat, rr_score))
    report = {"n": len(rows), f"category_recall@{n_cand} (Gate A)": round(recall20 / max(1, len(rows)), 3)}
    report.update({f"bm25_{name}": round(value, 3) for name, value in aggregate_rank_metrics(bm25_rank_metrics).items()})
    if reranker:
        report.update({f"rerank_{name}": round(value, 3) for name, value in aggregate_rank_metrics(rerank_rank_metrics).items()})
        report["table"] = tally(out, THRESHOLDS)
        report["rerank_ms_per_query"] = round(1000 * sum(lat) / max(1, len(lat)), 1)
        report["rerank_latency_p50_ms"] = round(1000 * percentile(lat, 50), 1)
        report["rerank_latency_p95_ms"] = round(1000 * percentile(lat, 95), 1)
    else:
        report["bm25 top-1"] = tally(out, ())["bm25 top-1"]
    print(f"== {label}", json.dumps(report, ensure_ascii=False, indent=1), flush=True)
    try:
        rows = json.loads(ROWS_OUT.read_text()) if ROWS_OUT.exists() else {}
        rows[label] = out
        ROWS_OUT.write_text(json.dumps(rows))
    except OSError:
        pass
    return out


def run_runbooks(reranker, probes, stage):
    """Score separation between in-domain symptoms (rag.py EVAL + stage sentences) and out-of-domain probes."""
    import rag
    docs = [(name, f"{title}\n{text[:600]}") for name, title, text in rag.documents()]
    ev = getattr(rag, "EVAL", [])
    in_domain = []
    for item in ev:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            in_domain.append((str(item[0]), str(item[1])))
        elif isinstance(item, dict):
            in_domain.append((str(item.get("query") or item.get("q")), str(item.get("runbook") or item.get("name"))))
    in_domain += [(q, "?") for q in stage]
    def best(q):
        scores = reranker.score(q, [p for _, p in docs])
        i = max(range(len(docs)), key=lambda j: scores[j])
        return docs[i][0], scores[i], sorted(scores, reverse=True)[1] if len(scores) > 1 else None
    rows_in = [(q, want, *best(q)) for q, want in in_domain]
    rows_out = [(q, cat, *best(q)) for cat, q in probes]
    hit = sum(1 for q, want, name, s, s2 in rows_in if want in ("?",) or name.startswith(want) or want.startswith(name.split(".")[0]))
    print("== runbooks (reranker)", json.dumps({
        "in_domain_n": len(rows_in), "in_domain_top1_matches_expected": f"{hit}/{len(rows_in)}",
        "in_domain_top_scores": [round(s, 1) for _, _, _, s, _ in rows_in],
        "out_of_domain_top_scores": [round(s, 1) for _, _, _, s, _ in rows_out],
        "in_domain_min": round(min(s for *_, s, _ in rows_in), 1) if rows_in else None,
        "out_of_domain_max": round(max(s for *_, s, _ in rows_out), 1) if rows_out else None,
    }, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Gate A only, no model")
    ap.add_argument("--candidates", type=int, default=20)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()
    probes, stage = q_gap()
    half_a, half_b = halves()
    idx = ck.index()
    import os
    print(json.dumps({"indexed": len(idx), "backend": type(idx).__name__, "half_a": len(half_a), "half_b": len(half_b), "probes": len(probes), "model": None if args.dry_run else MODEL,
                      "tokens": os.environ.get("COMMUNITY_TOKENS", "pack"), "passage": ck.RERANK_PASSAGE, "candidates": args.candidates}), flush=True)
    if ROWS_OUT.exists() and not args.dry_run:
        ROWS_OUT.unlink()
    import os
    url = os.environ.get("RERANK_URL")
    reranker = None if args.dry_run else (HttpReranker(url) if url else Reranker(args.device))
    run_layer(idx, half_a, reranker, args.candidates, "community held-out half A (tune thresholds here)")
    run_layer(idx, half_b, reranker, args.candidates, "community held-out half B (read the chosen threshold here)")
    run_layer(idx, probes, reranker, args.candidates, "community Q_gap (38 probes, descriptive)")
    if reranker:
        run_runbooks(reranker, probes, stage)


if __name__ == "__main__":
    main()
