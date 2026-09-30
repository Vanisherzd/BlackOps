# Knowledge-pack RAG evaluation (2026-09-30, thinking off)

Held-out benchmark incidents from `kb/artifacts/eval_ground_truth.jsonl`, diagnosed by Nemotron-3-Nano-30B-A3B-NVFP4 on TensorRT-LLM on the DGX Spark, offline. 63 cases: every disk_full / crashloop / dns case in the split plus a per-source slice of the rest ({'crashloop': 8, 'other': 28, 'disk_full': 22, 'dns': 5}; sources {'AgenticOpsEval/AIOps2025': 17, 'AgenticOpsEval/RCA100': 4, 'Cloud-OpsBench': 15, 'RCAEval': 27}). Two arms per input: `none` = telemetry only; `rag` = telemetry plus what the agent's `search_incident_knowledge` returns for the symptoms in it. Same closed-set label vocabulary (train split of each source) for both arms; scored by the pack's own scorer (`blackout_ops_kb.evaluation`). Energy = nvidia-smi power at 1 Hz minus idle (13.6 W), shared among concurrent calls.

## Accuracy vs compression

Strict = the pack's scorer (exact label, or the label inside the answer when it has 6+ characters). Lenient = strict, or truth label inside the answer, or answer inside the truth label. Wh = GPU energy above idle per diagnosis at 4-way concurrency; solo = the same charged in full to each call (upper bound, sequential-equivalent).

| arm | ratio | cases | strict acc | lenient acc | scenario acc | avg prompt tokens | avg latency | Wh/diagnosis | Wh solo |
|---|---|---|---|---|---|---|---|---|---|
| none | 1x | 63 | 25% | 25% | 40% | 23458 | 14.2 s | 0.116 | 0.205 |
| none | 2x | 63 | 21% | 21% | 33% | 20742 | 12.8 s | 0.106 | 0.182 |
| none | 4x | 63 | 27% | 27% | 43% | 18912 | 12.0 s | 0.097 | 0.172 |
| none | 8x | 63 | 32% | 33% | 41% | 15096 | 9.3 s | 0.081 | 0.127 |
| none | 16x | 63 | 29% | 29% | 37% | 9087 | 7.6 s | 0.053 | 0.100 |
| none | 32x | 63 | 22% | 22% | 40% | 5050 | 5.3 s | 0.037 | 0.063 |
| rag | 1x | 63 | 14% | 14% | 27% | 23712 | 14.4 s | 0.114 | 0.208 |
| rag | 2x | 63 | 21% | 21% | 30% | 20996 | 13.0 s | 0.098 | 0.183 |
| rag | 4x | 63 | 16% | 16% | 27% | 19164 | 12.1 s | 0.097 | 0.173 |
| rag | 8x | 63 | 14% | 14% | 17% | 15353 | 11.8 s | 0.081 | 0.163 |
| rag | 16x | 63 | 8% | 8% | 13% | 9371 | 6.9 s | 0.051 | 0.090 |
| rag | 32x | 63 | 11% | 11% | 25% | 5319 | 5.5 s | 0.037 | 0.068 |

Working point (none, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 8x, 32%, 15096 tokens.

Working point (rag, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 2x, 21%, 20996 tokens.

## By scenario (lenient accuracy, none → rag)

| scenario | n | 1x | 2x | 4x | 8x | 16x | 32x |
|---|---|---|---|---|---|---|---|
| disk_full | 22 | 64% → 27% | 55% → 50% | 68% → 36% | 64% → 27% | 55% → 14% | 50% → 23% |
| crashloop | 8 | 12% → 38% | 12% → 25% | 12% → 12% | 38% → 25% | 50% → 12% | 12% → 25% |
| dns | 5 | 20% → 0% | 0% → 0% | 20% → 0% | 60% → 20% | 20% → 0% | 20% → 0% |
| other | 28 | 0% → 0% | 0% → 0% | 0% → 4% | 4% → 0% | 4% → 4% | 4% → 0% |

## By source (over all ratios, none → rag)

| source | n | strict | lenient | scenario |
|---|---|---|---|---|
| AgenticOpsEval/AIOps2025 | 17 | 8% (8/102) → 9% (9/102) | 8% (8/102) → 9% (9/102) | 26% (27/102) → 29% (30/102) |
| AgenticOpsEval/RCA100 | 4 | 0% (0/24) → 4% (1/24) | 0% (0/24) → 4% (1/24) | 21% (5/24) → 8% (2/24) |
| Cloud-OpsBench | 15 | 14% (13/90) → 6% (5/90) | 14% (13/90) → 6% (5/90) | 31% (28/90) → 14% (13/90) |
| RCAEval | 27 | 48% (77/162) → 23% (38/162) | 48% (78/162) → 23% (38/162) | 54% (87/162) → 27% (43/162) |

## What retrieval did (rag arm)

- Query = a one-line symptom the model wrote from the telemetry (median 2.0 s extra per diagnosis).
- `matched` (a remediation card cleared the 0.34 coverage bar): 45% (170/378) of inputs.
- Retrieval's scenario guess equals the truth scenario, target scenarios only: 0% (1/210).
- One of the three similar historical cases carries the true label in its title: 4% (14/378) of inputs.
- Lenient accuracy when a similar case carried the label: 29% (4/14) vs 13% (49/364) when none did.

Errors: {'none': 0, 'rag': 0}. Prompts truncated to the window: {'none': 155, 'rag': 155}. Predictions and the power trace: `logs/kb-eval/` on the Spark; curves: `docs/kb-eval/curve-*.csv`.
