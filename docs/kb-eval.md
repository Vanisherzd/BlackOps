# Knowledge-pack RAG evaluation (2026-09-30, thinking off)

Held-out benchmark incidents from `kb/artifacts/eval_ground_truth.jsonl`, diagnosed by Nemotron-3-Nano-30B-A3B-NVFP4 on TensorRT-LLM on the DGX Spark, offline. 63 cases: every disk_full / crashloop / dns case in the split plus a per-source slice of the rest ({'crashloop': 8, 'other': 28, 'disk_full': 22, 'dns': 5}; sources {'AgenticOpsEval/AIOps2025': 17, 'AgenticOpsEval/RCA100': 4, 'Cloud-OpsBench': 15, 'RCAEval': 27}). Two arms per input: `none` = telemetry only; `rag` = telemetry plus what the agent's `search_incident_knowledge` returns for the symptoms in it. Same closed-set label vocabulary (train split of each source) for both arms; scored by the pack's own scorer (`blackout_ops_kb.evaluation`). Energy = nvidia-smi power at 1 Hz minus idle (12.4 W), shared among concurrent calls.

## Accuracy vs compression

Strict = the pack's scorer (exact label, or the label inside the answer when it has 6+ characters). Lenient = strict, or truth label inside the answer, or answer inside the truth label. Wh = GPU energy above idle per diagnosis at 4-way concurrency; solo = the same charged in full to each call (upper bound, sequential-equivalent).

Service = the named failing component equals the ground-truth service/pod/node (cases that have one).

| arm | ratio | cases | strict acc | lenient acc | scenario acc | service acc | avg prompt tokens | avg latency | Wh/diagnosis | Wh solo |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 1x | 63 | 22% | 22% | 41% | 30% (19/63) | 20080 | 12.2 s | 0.100 | 0.176 |
| none | 2x | 63 | 24% | 24% | 43% | 29% (18/63) | 18822 | 12.3 s | 0.101 | 0.179 |
| none | 4x | 63 | 16% | 16% | 35% | 35% (22/63) | 16486 | 10.6 s | 0.092 | 0.151 |
| none | 8x | 63 | 24% | 24% | 38% | 30% (19/63) | 14424 | 9.9 s | 0.081 | 0.139 |
| none | 16x | 63 | 22% | 22% | 33% | 30% (19/63) | 10610 | 7.9 s | 0.068 | 0.107 |
| none | 32x | 63 | 22% | 22% | 35% | 24% (15/63) | 8751 | 8.0 s | 0.060 | 0.106 |
| rag | 1x | 63 | 10% | 11% | 24% | 27% (17/63) | 20336 | 12.8 s | 0.099 | 0.182 |
| rag | 2x | 63 | 8% | 8% | 19% | 32% (20/63) | 18800 | 11.3 s | 0.093 | 0.164 |
| rag | 4x | 63 | 11% | 14% | 22% | 22% (14/63) | 16726 | 11.2 s | 0.087 | 0.161 |
| rag | 8x | 63 | 13% | 14% | 22% | 19% (12/63) | 14664 | 10.3 s | 0.078 | 0.146 |
| rag | 16x | 63 | 13% | 14% | 22% | 24% (15/63) | 10843 | 9.2 s | 0.065 | 0.124 |
| rag | 32x | 63 | 22% | 22% | 30% | 19% (12/63) | 9035 | 7.3 s | 0.054 | 0.096 |

Working point (none, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 8x, 24%, 14424 tokens.

Working point (rag, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 32x, 22%, 9035 tokens.

## By scenario (lenient accuracy, none → rag)

| scenario | n | 1x | 2x | 4x | 8x | 16x | 32x |
|---|---|---|---|---|---|---|---|
| disk_full | 22 | 45% → 18% | 45% → 5% | 32% → 23% | 36% → 18% | 27% → 23% | 45% → 27% |
| crashloop | 8 | 0% → 12% | 12% → 0% | 0% → 0% | 25% → 0% | 25% → 0% | 0% → 25% |
| dns | 5 | 40% → 20% | 20% → 20% | 40% → 20% | 60% → 40% | 40% → 20% | 60% → 60% |
| other | 28 | 7% → 4% | 11% → 11% | 4% → 11% | 7% → 11% | 14% → 11% | 4% → 11% |

## By source (over all ratios, none → rag)

| source | n | strict | lenient | scenario | service |
|---|---|---|---|---|---|
| AgenticOpsEval/AIOps2025 | 17 | 14% (14/102) → 10% (10/102) | 14% (14/102) → 10% (10/102) | 22% (22/102) → 18% (18/102) | 25% (26/102) → 19% (19/102) |
| AgenticOpsEval/RCA100 | 4 | 0% (0/24) → 0% (0/24) | 0% (0/24) → 0% (0/24) | 4% (1/24) → 0% (0/24) | 8% (2/24) → 8% (2/24) |
| Cloud-OpsBench | 15 | 12% (11/90) → 4% (4/90) | 12% (11/90) → 4% (4/90) | 30% (27/90) → 17% (15/90) | 18% (16/90) → 6% (5/90) |
| RCAEval | 27 | 35% (57/162) → 21% (34/162) | 35% (57/162) → 24% (39/162) | 57% (92/162) → 34% (55/162) | 42% (68/162) → 40% (64/162) |

## What retrieval did (rag arm)

- Query = a one-line symptom the model wrote from the telemetry (median 2.2 s extra per diagnosis).
- `matched` (a remediation card cleared the 0.34 coverage bar): 37% (138/378) of inputs.
- Retrieval's scenario guess equals the truth scenario, target scenarios only: 2% (4/210).
- One of the three similar historical cases carries the true label in its title: 7% (27/378) of inputs.
- Lenient accuracy when a similar case carried the label: 15% (4/27) vs 14% (49/351) when none did.

Errors: {'none': 0, 'rag': 0}. Prompts truncated to the window: {'none': 141, 'rag': 141}. Predictions and the power trace: `logs/kb-eval/` on the Spark; curves: `docs/kb-eval/curve-*.csv`.
