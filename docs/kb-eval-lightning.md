# Knowledge-pack RAG evaluation (2026-10-22, thinking off)

Held-out benchmark incidents from `kb/artifacts/eval_ground_truth.jsonl`, diagnosed by Nemotron-3.5-Lightning-30B-A3B-NVFP4 (DSpark) on vLLM on the DGX Spark, offline. 63 cases: every disk_full / crashloop / dns case in the split plus a per-source slice of the rest ({'crashloop': 8, 'other': 28, 'disk_full': 22, 'dns': 5}; sources {'AgenticOpsEval/AIOps2025': 17, 'AgenticOpsEval/RCA100': 4, 'Cloud-OpsBench': 15, 'RCAEval': 27}). Two arms per input: `none` = telemetry only; `rag` = telemetry plus what the agent's `search_incident_knowledge` returns for the symptoms in it. Same closed-set label vocabulary (train split of each source) for both arms; scored by the pack's own scorer (`blackout_ops_kb.evaluation`). Energy = nvidia-smi power at 1 Hz minus idle (12.4 W), shared among concurrent calls.

Retrieval: v1; query: one line written by the model.

## Accuracy vs compression

Strict = the pack's scorer (exact label, or the label inside the answer when it has 6+ characters). Lenient = strict, or truth label inside the answer, or answer inside the truth label. Wh = GPU energy above idle per diagnosis at 4-way concurrency; solo = the same charged in full to each call (upper bound, sequential-equivalent).

Service = the named failing component equals the ground-truth service/pod/node (cases that have one).

| arm | ratio | cases | strict acc | lenient acc | scenario acc | service acc | avg prompt tokens | avg latency | Wh/diagnosis | Wh solo |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 8x | 63 | 6% | 6% | 29% | 40% (25/63) | 14424 | 12.0 s | 0.184 | 0.184 |
| none | 32x | 63 | 3% | 5% | 22% | 35% (22/63) | 8751 | 10.2 s | 0.166 | 0.166 |
| rag | 8x | 63 | 6% | 8% | 17% | 38% (24/63) | 14683 | 10.9 s | 0.161 | 0.165 |
| rag | 32x | 63 | 5% | 5% | 14% | 33% (21/63) | 8997 | 8.4 s | 0.132 | 0.135 |

Working point (none, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 8x, 6%, 14424 tokens.

Working point (rag, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 8x, 6%, 14683 tokens.

## By scenario (lenient accuracy, none → rag)

| scenario | n | 8x | 32x |
|---|---|---|---|
| disk_full | 22 | 5% → 9% | 5% → 5% |
| crashloop | 8 | 12% → 12% | 0% → 0% |
| dns | 5 | 20% → 20% | 20% → 20% |
| other | 28 | 4% → 4% | 4% → 4% |

## By source (over all ratios, none → rag)

| source | n | strict | lenient | scenario | service |
|---|---|---|---|---|---|
| AgenticOpsEval/AIOps2025 | 17 | 12% (4/34) → 12% (4/34) | 12% (4/34) → 12% (4/34) | 26% (9/34) → 15% (5/34) | 35% (12/34) → 38% (13/34) |
| AgenticOpsEval/RCA100 | 4 | 0% (0/8) → 0% (0/8) | 0% (0/8) → 0% (0/8) | 12% (1/8) → 0% (0/8) | 0% (0/8) → 0% (0/8) |
| Cloud-OpsBench | 15 | 3% (1/30) → 3% (1/30) | 3% (1/30) → 3% (1/30) | 23% (7/30) → 10% (3/30) | 17% (5/30) → 13% (4/30) |
| RCAEval | 27 | 2% (1/54) → 4% (2/54) | 4% (2/54) → 6% (3/54) | 28% (15/54) → 22% (12/54) | 56% (30/54) → 52% (28/54) |

## What retrieval did (rag arm)

- Query = a one-line symptom the model wrote from the telemetry (median 4.5 s extra per diagnosis).
- `matched`: 23% (29/126) of inputs; the matched card's scenario equals the truth scenario on 24% (7/29) of those.
- Retrieval's scenario guess equals the truth scenario, target scenarios only: 0% (0/70).
- One of the three similar historical cases carries the true label in its title: 8% (10/126) of inputs.
- Lenient accuracy when a similar case carried the label: 30% (3/10) vs 4% (5/116) when none did.

Errors: {'none': 0, 'rag': 0}. Prompts truncated to the window: {'none': 19, 'rag': 19}. Predictions and the power trace: `logs/kb-eval/` on the Spark; curves: `docs/kb-eval/curve-*.csv`.
