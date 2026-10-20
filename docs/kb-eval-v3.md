# Knowledge-pack RAG evaluation (2026-10-13, thinking off)

Held-out benchmark incidents from `kb/artifacts/eval_ground_truth.jsonl`, diagnosed by Nemotron-3-Nano-30B-A3B-NVFP4 on TensorRT-LLM on the DGX Spark, offline. 63 cases: every disk_full / crashloop / dns case in the split plus a per-source slice of the rest ({'crashloop': 8, 'other': 28, 'disk_full': 22, 'dns': 5}; sources {'AgenticOpsEval/AIOps2025': 17, 'AgenticOpsEval/RCA100': 4, 'Cloud-OpsBench': 15, 'RCAEval': 27}). Two arms per input: `none` = telemetry only; `rag` = telemetry plus what the agent's `search_incident_knowledge` returns for the symptoms in it. Same closed-set label vocabulary (train split of each source) for both arms; scored by the pack's own scorer (`blackout_ops_kb.evaluation`). Energy = nvidia-smi power at 1 Hz minus idle (13.4 W), shared among concurrent calls.

Retrieval: v3; query: extracted from the telemetry (incident_query).

## Accuracy vs compression

Strict = the pack's scorer (exact label, or the label inside the answer when it has 6+ characters). Lenient = strict, or truth label inside the answer, or answer inside the truth label. Wh = GPU energy above idle per diagnosis at 4-way concurrency; solo = the same charged in full to each call (upper bound, sequential-equivalent).

Service = the named failing component equals the ground-truth service/pod/node (cases that have one).

| arm | ratio | cases | strict acc | lenient acc | scenario acc | service acc | avg prompt tokens | avg latency | Wh/diagnosis | Wh solo |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 1x | 63 | 22% | 22% | 35% | 29% (18/63) | 20080 | 11.5 s | 0.077 | 0.147 |
| none | 2x | 63 | 19% | 19% | 35% | 35% (22/63) | 18822 | 10.8 s | 0.076 | 0.149 |
| none | 4x | 63 | 25% | 25% | 40% | 33% (21/63) | 16486 | 9.7 s | 0.071 | 0.130 |
| none | 8x | 63 | 24% | 24% | 35% | 25% (16/63) | 14424 | 8.7 s | 0.059 | 0.116 |
| none | 16x | 63 | 27% | 27% | 37% | 29% (18/63) | 10610 | 7.7 s | 0.050 | 0.098 |
| none | 32x | 63 | 25% | 25% | 37% | 19% (12/63) | 8751 | 6.9 s | 0.043 | 0.085 |
| rag | 1x | 63 | 35% | 35% | 38% | 29% (18/63) | 20233 | 12.9 s | 0.079 | 0.166 |
| rag | 2x | 63 | 32% | 32% | 40% | 30% (19/63) | 18975 | 11.2 s | 0.076 | 0.156 |
| rag | 4x | 63 | 33% | 33% | 40% | 29% (18/63) | 16640 | 11.2 s | 0.068 | 0.149 |
| rag | 8x | 63 | 35% | 35% | 38% | 32% (20/63) | 14579 | 9.3 s | 0.061 | 0.124 |
| rag | 16x | 63 | 33% | 33% | 38% | 25% (16/63) | 10764 | 8.1 s | 0.050 | 0.102 |
| rag | 32x | 63 | 33% | 33% | 35% | 29% (18/63) | 8907 | 7.0 s | 0.043 | 0.087 |

Working point (none, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 16x, 27%, 10610 tokens.

Working point (rag, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 8x, 35%, 14579 tokens.

## By scenario (lenient accuracy, none → rag)

| scenario | n | 1x | 2x | 4x | 8x | 16x | 32x |
|---|---|---|---|---|---|---|---|
| disk_full | 22 | 36% → 73% | 36% → 68% | 36% → 68% | 41% → 64% | 41% → 59% | 50% → 59% |
| crashloop | 8 | 0% → 0% | 0% → 0% | 25% → 0% | 12% → 12% | 12% → 12% | 0% → 0% |
| dns | 5 | 40% → 20% | 20% → 20% | 40% → 20% | 60% → 60% | 80% → 60% | 40% → 60% |
| other | 28 | 14% → 18% | 11% → 14% | 14% → 18% | 7% → 14% | 11% → 14% | 11% → 18% |

## By source (over all ratios, none → rag)

| source | n | strict | lenient | scenario | service |
|---|---|---|---|---|---|
| AgenticOpsEval/AIOps2025 | 17 | 13% (13/102) → 15% (15/102) | 13% (13/102) → 15% (15/102) | 19% (19/102) → 20% (20/102) | 22% (22/102) → 21% (21/102) |
| AgenticOpsEval/RCA100 | 4 | 0% (0/24) → 0% (0/24) | 0% (0/24) → 0% (0/24) | 0% (0/24) → 0% (0/24) | 8% (2/24) → 4% (1/24) |
| Cloud-OpsBench | 15 | 13% (12/90) → 18% (16/90) | 13% (12/90) → 18% (16/90) | 29% (26/90) → 21% (19/90) | 14% (13/90) → 17% (15/90) |
| RCAEval | 27 | 40% (65/162) → 59% (96/162) | 40% (65/162) → 59% (96/162) | 57% (92/162) → 65% (105/162) | 43% (70/162) → 44% (72/162) |

## What retrieval did (rag arm)


- `matched`: 0% (0/378) of inputs; the matched card's scenario equals the truth scenario on - of those.
- Retrieval's scenario guess equals the truth scenario, target scenarios only: 18% (37/210).
- One of the three similar historical cases carries the true label in its title: 26% (99/378) of inputs.
- Lenient accuracy when a similar case carried the label: 15% (15/99) vs 40% (112/279) when none did.

Errors: {'none': 0, 'rag': 0}. Prompts truncated to the window: {'none': 141, 'rag': 141}. Predictions and the power trace: `logs/kb-eval-v3/` on the Spark; curves: `docs/kb-eval/curve-*.csv`.
