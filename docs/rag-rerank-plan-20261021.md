# RAG reranking plan — 2026-10-21

## Decision

Reconsider reranking, but **as a retrieval-quality experiment, not a speed optimization**.

The existing handoff correctly rejected reranking as a way to improve latency: prompt/prefill is only a small part of end-to-end recovery time. That conclusion still stands. The new question is different:

> Can reranking reduce retrieval-induced reasoning errors and surface the right repair card more reliably from a larger, increasingly heterogeneous knowledge pack?

This is an evaluation branch only. **Do not change the stage/demo mainline until the offline gate passes.**

## Current repository state

Snapshot: `main` at `80e4f79` after PR #10.

The competition path is already stable on Jetson AGX Thor:

- main inference: `Nemotron-3.5-Lightning-30B-A3B-NVFP4` through vLLM;
- five stage cards: 5/5 on Thor;
- second-act approval flow: 3/3;
- full stage rehearsal: 2/2 after the post-incident fixes;
- NeMo Guardrails: 23/23 in repeated Thor checks;
- Bolt remains a Spark comparison path; it is not the Thor mainline.

Do not destabilize this path for a retrieval experiment.

## What the existing RAG actually does

The current retrieval stack is not a generic vector-RAG pipeline.

`KnowledgeBaseV2` uses an in-process BM25-style ranker over the packaged corpus, with token cleanup, IDF weighting, telemetry indexing for historical cases, phrase gating for repair cards, and fallback behavior for typed queries.

Measured history:

1. **v1 retrieval was harmful.** On the 63-case / 6-ratio model evaluation it broke more correct answers than it fixed.
2. **v3 fixed the false-match problem and became useful for recurrence memory.** With the same fault on the same service visible, it improved the end-to-end arm; with those sibling incidents hidden, the gain mostly disappeared.
3. **v4 repair cards did not show measurable end-to-end benefit** in the blind batches, so v3 remained the default.
4. On the inode-exhaustion drill, a knowledge card did not change behavior in the larger rerun. The decisive levers were the tool surface and refusal text that named the next measurement.
5. Since then, the repository has added curated hard-failure repair knowledge and DGX failure-recovery knowledge. That increases candidate diversity and makes ranking quality more important even though the stage mainline is already stable.

The important implementation detail is in `scripts/agent.py`: a symptom is first matched to a runbook, then `knowledge_note()` may append a repair card. The card path currently relies on the existing candidate ranking plus a confidence/phrase gate. There is **no learned reranker** in the runtime path.

## Why reranking is worth testing now

The previous note in `docs/HANDOFF-kb-retrieval-v2.md` says reranking would not help speed because prefill is a small fraction of total latency. Keep that statement.

What was not tested is whether a cross-encoder reranker improves **candidate ordering and context precision**.

That matters because the failure mode we care about is now:

```text
relevant document exists in candidate set
        ↓
BM25 ranks a generic/adjacent card above it
        ↓
wrong or weak context reaches the agent
        ↓
extra tool calls / wrong hypothesis / longer MTTR
```

A reranker can help only if the correct item is already in the first-stage candidate pool. Therefore the first measurement must be candidate recall, not model quality.

## Candidate model

Primary text-only candidate:

`nvidia/llama-nemotron-rerank-1b-v2`

Why this one:

- text cross-encoder / ranking model, not a VL model;
- about 2.47 GB of safetensors weights;
- small enough to coexist with Lightning on a 128 GB Thor;
- intended for query-document relevance scoring.

Do **not** use a VL reranker unless the knowledge source later includes screenshots/scanned manuals as first-class retrieval objects.

## Proposed retrieval path

Do not replace the current retriever. Add the reranker behind a feature flag.

```text
Incident evidence / symptom query
        ↓
Current BM25 first-stage retrieval
        ↓
Top-N candidates (start with 20)
        ↓
Optional text reranker
        ↓
Top-K candidates (start with 3–5)
        ↓
Existing confidence / phrase / safety gates
        ↓
Repair card / knowledge note
        ↓
Agent
```

The reranker must **not** bypass existing safety or confidence gates. It only changes candidate order.

## Phase 0 — offline gate first

Before loading a reranker on Thor, extend the offline evaluator.

Required measurements:

- Recall@5 / Recall@10 / Recall@20 of the current first-stage retriever;
- MRR and nDCG@5 where a ground-truth repair card exists;
- top-1 repair-card accuracy;
- false hard-repair matches on negative queries;
- latency per query;
- candidate IDs before and after reranking.

Datasets already suitable for this work:

- `datasets/curated/hard_repairs/eval_inputs.jsonl`
- `datasets/curated/hard_repairs/eval_ground_truth.jsonl`
- `datasets/curated/hard_repairs/negative_queries.json`
- existing retrieval dev/eval material in the KB evaluation scripts.

The existing `scripts/eval-hard-repairs.py` is a good starting point because it is offline, answer-isolated, does not invoke an LLM, and already checks false matches.

### Gate A — is reranking even possible to help?

Proceed only if the correct card is usually present in the first-stage pool.

Initial target:

- Recall@20 >= 0.90 on the curated hard-repair set, or a clearly documented reason why the set cannot reach that level.

If Recall@20 is low, **do not add a reranker yet**. Fix first-stage retrieval, query construction, metadata, or card wording first.

### Gate B — does reranking improve ranking without creating false matches?

A reranker candidate passes only if:

- top-1 / MRR improves over the current ordering;
- negative-query false-match rate does not regress;
- the existing phrase/confidence gate remains authoritative;
- added latency is measured and acceptable.

Do not use benchmark claims from the model card as project results.

## Phase 1 — implementation shape

If the offline gate passes, add a narrow interface rather than coupling `KnowledgeBaseV2` directly to Transformers.

Suggested interface:

```python
class Reranker:
    def score(self, query: str, documents: list[str]) -> list[float]: ...
```

Configuration:

```text
KB_RERANKER=none|nemotron1b
KB_RERANK_CANDIDATES=20
KB_RERANK_TOP_K=5
KB_RERANK_DEVICE=auto
```

Rules:

- `none` must preserve the current behavior exactly;
- model load failure must fail open to the current ranking for retrieval, not crash the incident loop;
- no new unrestricted shell/tool capability;
- do not load the reranker inside the main LLM process;
- cache the reranker process/model between queries;
- record latency and selected candidate IDs in evaluation artifacts.

## Phase 2 — end-to-end A/B only after offline success

Use the same main model, same tools, same runbooks and same faults. Change only ranking.

Suggested arms:

| Arm | Retrieval |
|---|---|
| A | No knowledge note / retrieval baseline where applicable |
| B | Current BM25 + current gates |
| C | BM25 Top-20 -> rerank -> current gates |
| D | Gated retrieval + rerank, only if C is beneficial |

End-to-end metrics:

- full recovery success;
- all root causes found for compound incidents;
- wrong-action rate;
- median tool calls;
- median MTTR;
- retrieval calls per incident;
- rerank latency;
- prompt tokens added by knowledge;
- unsafe-action / policy-denial count.

The key question is not whether reranking improves a retrieval benchmark. It is whether it reduces wrong decisions before recovery.

## Demo policy

For the current finals build:

- **keep Lightning + current stable retrieval path on `main`;**
- do not put an unvalidated reranker in the stage path;
- if the offline and end-to-end gates pass before freeze, merge behind `KB_RERANKER=none` by default first;
- only enable it in the live demo after a clean rehearsal confirms no regression.

A successful result would support the claim:

> More context is not better context. Retrieval must earn its place by improving recovery decisions.

A negative result is also useful: it would confirm that, for this system, tool design and evidence acquisition dominate retrieval sophistication.

## External reference

NVIDIA model candidate: `nvidia/llama-nemotron-rerank-1b-v2`

Model page: https://huggingface.co/nvidia/llama-nemotron-rerank-1b-v2
