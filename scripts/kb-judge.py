"""LLM-judged relevance for the community layer: are the references it returns actually about the symptom?

  RERANK_URL=http://127.0.0.1:8020/rerank .venv/bin/python scripts/kb-judge.py [--n 240] [--seed 7] [--concurrency 2]

Why. Every community-layer number so far (docs/kb-community-20261022.md) used the question's category as the label: a
cheap proxy that calls an adjacent-category but correct answer "wrong" and a same-category but useless one "right".
This script asks the local model (no thinking, temperature 0, one word) whether a reference addresses the symptom, for
the BM25 top-1 and the reranked top-5 of a fixed sample of the held-out questions, and reports:
  precision@1 of the BM25 order and of the reranked order (judge-labelled), precision@5 (reranked), how often the
  category proxy agreed with the judge, and, with real labels, what a sidecar-score floor would buy (precision and
  coverage per threshold). The judge is itself a model: its labels are "LLM-judged", never "ground truth".
Outputs logs/kb-judge.json (every judgment, re-usable) and a summary on stdout.
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import random
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import community_knowledge as ck  # noqa: E402

HOLDOUT = ROOT / "datasets" / "derived" / "stackexchange_ops_qa_20261022" / "holdout.jsonl"
OUT = ROOT / "logs" / "kb-judge.json"
BASE = os.environ.get("VLLM_URL", "http://127.0.0.1:8000") + "/v1"
MODEL = os.environ.get("SERVED_NAME", "edge-agent")
SYSTEM = ("You judge whether a Q&A reference addresses an operator's symptom. Answer with exactly one word: yes or no. "
          "yes = the reference is about the same problem and its fix or explanation would apply; no = different problem, "
          "only superficially similar words, or no usable fix.")


def judge_one(client, query, doc):
    user = (f"Symptom:\n{query[:700]}\n\nReference title: {doc['title']}\nReference answer excerpt: {doc['answer_excerpt'][:500]}\n\n"
            "Does this reference address the same problem? Answer yes or no.")
    t0 = time.time()
    r = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=3,
                                       messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                       extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    text = (r.choices[0].message.content or "").strip().lower()
    return ("yes" if text.startswith("yes") else "no" if text.startswith("no") else "unclear"), round(time.time() - t0, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=240)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()
    from openai import OpenAI
    client = OpenAI(base_url=BASE, api_key="local")
    held = [json.loads(l) for l in HOLDOUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    random.Random(args.seed).shuffle(held)
    sample = held[:args.n]
    idx = ck.index()
    print(json.dumps({"indexed": len(idx), "backend": type(idx).__name__, "rerank": bool(ck.RERANK_URL), "n": len(sample)}), flush=True)

    rows = []
    for r in sample:
        q = " ".join([r["title"], *r["symptom_lines"]])
        bm = idx.search(q, k=ck.RERANK_TOP_N, min_shared=1)
        if not bm:
            continue
        rr = ck.rerank(q, bm) if ck.RERANK_URL else None
        rows.append({"id": f"{r['site']}-{r['question_id']}", "category": r["category"], "query": q,
                     "bm25_top": bm[0][2], "rerank_top5": [(s, d) for s, _, d in (rr or [])[:5]]})

    def work(row):
        docs = {row["bm25_top"]["id"]: row["bm25_top"]}
        for _, d in row["rerank_top5"]:
            docs.setdefault(d["id"], d)
        verdicts = {}
        for did, d in docs.items():
            try:
                verdicts[did] = judge_one(client, row["query"], d)
            except Exception as e:  # the judge is optional evidence; one failure must not stop the run
                verdicts[did] = ("error", 0.0)
        return row, verdicts

    results = []
    with concurrent.futures.ThreadPoolExecutor(args.concurrency) as ex:
        for i, (row, verdicts) in enumerate(ex.map(work, rows), 1):
            results.append({"id": row["id"], "category": row["category"], "query": row["query"][:200],
                            "bm25_top": {"id": row["bm25_top"]["id"], "category": row["bm25_top"]["category"], "verdict": verdicts[row["bm25_top"]["id"]][0]},
                            "rerank_top5": [{"id": d["id"], "category": d["category"], "score": round(s, 4), "verdict": verdicts[d["id"]][0]}
                                            for s, d in row["rerank_top5"]],
                            "judge_seconds": round(sum(v[1] for v in verdicts.values()), 2)})
            if i % 40 == 0:
                print(f"judged {i}/{len(rows)}", flush=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=0))

    n = len(results)
    yes = lambda v: v == "yes"
    bm_p1 = sum(yes(x["bm25_top"]["verdict"]) for x in results) / n
    rr_rows = [x for x in results if x["rerank_top5"]]
    rr_p1 = sum(yes(x["rerank_top5"][0]["verdict"]) for x in rr_rows) / max(1, len(rr_rows))
    rr_p5 = sum(any(yes(c["verdict"]) for c in x["rerank_top5"]) for x in rr_rows) / max(1, len(rr_rows))
    proxy_agree = sum((x["bm25_top"]["category"] == x["category"]) == yes(x["bm25_top"]["verdict"]) for x in results) / n
    judged = sum(len(x["rerank_top5"]) + 1 for x in results)
    unclear = sum((x["bm25_top"]["verdict"] not in ("yes", "no")) + sum(c["verdict"] not in ("yes", "no") for c in x["rerank_top5"]) for x in results)
    floors = {}
    for th in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9):
        kept = [x for x in rr_rows if x["rerank_top5"][0]["score"] >= th]
        floors[str(th)] = {"coverage": round(len(kept) / max(1, len(rr_rows)), 3),
                           "precision_at_1": round(sum(yes(x["rerank_top5"][0]["verdict"]) for x in kept) / max(1, len(kept)), 3)}
    summary = {"n": n, "judgments": judged, "unclear": unclear, "judged_precision_at_1": {"bm25": round(bm_p1, 3), "rerank": round(rr_p1, 3)},
               "judged_precision_at_5_rerank": round(rr_p5, 3), "category_proxy_agrees_with_judge": round(proxy_agree, 3),
               "sidecar_score_floor_with_judged_labels": floors, "mean_judge_seconds_per_query": round(sum(x["judge_seconds"] for x in results) / n, 2)}
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
