# Knowledge-pack RAG evaluation (2026-10-13, thinking off)

Held-out benchmark incidents from `kb/artifacts/eval_ground_truth.jsonl`, diagnosed by Nemotron-3-Nano-30B-A3B-NVFP4 on TensorRT-LLM on the DGX Spark, offline. 63 cases: every disk_full / crashloop / dns case in the split plus a per-source slice of the rest ({'crashloop': 8, 'other': 28, 'disk_full': 22, 'dns': 5}; sources {'AgenticOpsEval/AIOps2025': 17, 'AgenticOpsEval/RCA100': 4, 'Cloud-OpsBench': 15, 'RCAEval': 27}). Two arms per input: `none` = telemetry only; `rag` = telemetry plus what the agent's `search_incident_knowledge` returns for the symptoms in it. Same closed-set label vocabulary (train split of each source) for both arms; scored by the pack's own scorer (`blackout_ops_kb.evaluation`). Energy = nvidia-smi power at 1 Hz minus idle (13.4 W), shared among concurrent calls.

Retrieval: v3, LEAVE-OUT: the same fault on the same service hidden from the index per case; query: extracted from the telemetry (incident_query).

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
| rag | 1x | 63 | 24% | 24% | 38% | 27% (17/63) | 20234 | 20.6 s | 0.033 | 0.132 |
| rag | 2x | 63 | 29% | 29% | 35% | 30% (19/63) | 18976 | 11.4 s | 0.033 | 0.132 |
| rag | 4x | 63 | 21% | 21% | 32% | 29% (18/63) | 16640 | 10.4 s | 0.031 | 0.123 |
| rag | 8x | 63 | 27% | 27% | 38% | 32% (20/63) | 14580 | 9.5 s | 0.028 | 0.112 |
| rag | 16x | 63 | 22% | 22% | 32% | 24% (15/63) | 10764 | 8.1 s | 0.024 | 0.095 |
| rag | 32x | 63 | 29% | 29% | 30% | 24% (15/63) | 8908 | 6.7 s | 0.020 | 0.075 |

Working point (none, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 16x, 27%, 10610 tokens.

Working point (rag, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 32x, 29%, 8908 tokens.

## By scenario (lenient accuracy, none → rag)

| scenario | n | 1x | 2x | 4x | 8x | 16x | 32x |
|---|---|---|---|---|---|---|---|
| disk_full | 22 | 36% → 50% | 36% → 59% | 36% → 41% | 41% → 55% | 41% → 41% | 50% → 50% |
| crashloop | 8 | 0% → 0% | 0% → 0% | 25% → 12% | 12% → 0% | 12% → 0% | 0% → 12% |
| dns | 5 | 40% → 40% | 20% → 20% | 40% → 20% | 60% → 80% | 80% → 60% | 40% → 60% |
| other | 28 | 14% → 7% | 11% → 14% | 14% → 7% | 7% → 4% | 11% → 7% | 11% → 11% |

## By source (over all ratios, none → rag)

| source | n | strict | lenient | scenario | service |
|---|---|---|---|---|---|
| AgenticOpsEval/AIOps2025 | 17 | 13% (13/102) → 14% (14/102) | 13% (13/102) → 14% (14/102) | 19% (19/102) → 19% (19/102) | 22% (22/102) → 22% (22/102) |
| AgenticOpsEval/RCA100 | 4 | 0% (0/24) → 0% (0/24) | 0% (0/24) → 0% (0/24) | 0% (0/24) → 0% (0/24) | 8% (2/24) → 8% (2/24) |
| Cloud-OpsBench | 15 | 13% (12/90) → 17% (15/90) | 13% (12/90) → 17% (15/90) | 29% (26/90) → 21% (19/90) | 14% (13/90) → 17% (15/90) |
| RCAEval | 27 | 40% (65/162) → 41% (66/162) | 40% (65/162) → 41% (66/162) | 57% (92/162) → 56% (91/162) | 43% (70/162) → 40% (65/162) |

## What retrieval did (rag arm)


- `matched`: 0% (0/378) of inputs; the matched card's scenario equals the truth scenario on - of those.
- Retrieval's scenario guess equals the truth scenario, target scenarios only: 18% (37/210).
- One of the three similar historical cases carries the true label in its title: 11% (40/378) of inputs.
- Lenient accuracy when a similar case carried the label: 18% (7/40) vs 26% (88/338) when none did.

Errors: {'none': 0, 'rag': 0}. Prompts truncated to the window: {'none': 141, 'rag': 141}. Predictions and the power trace: `logs/kb-eval-v3-unseen/` on the Spark; curves: `docs/kb-eval/curve-*.csv`.
