# Knowledge-pack RAG evaluation (2026-09-30, thinking on)

Held-out benchmark incidents from `kb/artifacts/eval_ground_truth.jsonl`, diagnosed by Nemotron-3-Nano-30B-A3B-NVFP4 on TensorRT-LLM on the DGX Spark, offline. 63 cases: every disk_full / crashloop / dns case in the split plus a per-source slice of the rest ({'crashloop': 8, 'other': 28, 'disk_full': 22, 'dns': 5}; sources {'AgenticOpsEval/AIOps2025': 17, 'AgenticOpsEval/RCA100': 4, 'Cloud-OpsBench': 15, 'RCAEval': 27}). Two arms per input: `none` = telemetry only; `rag` = telemetry plus what the agent's `search_incident_knowledge` returns for the symptoms in it. Same closed-set label vocabulary (train split of each source) for both arms; scored by the pack's own scorer (`blackout_ops_kb.evaluation`). Energy = nvidia-smi power at 1 Hz minus idle (14.7 W), shared among concurrent calls.

## Accuracy vs compression

Strict = the pack's scorer (exact label, or the label inside the answer when it has 6+ characters). Lenient = strict, or truth label inside the answer, or answer inside the truth label. Wh = GPU energy above idle per diagnosis at 4-way concurrency; solo = the same charged in full to each call (upper bound, sequential-equivalent).

| arm | ratio | cases | strict acc | lenient acc | scenario acc | avg prompt tokens | avg latency | Wh/diagnosis | Wh solo |
|---|---|---|---|---|---|---|---|---|---|
| none | 8x | 63 | 10% | 10% | 44% | 15096 | 57.5 s | 0.299 | 0.600 |
| rag | 8x | 63 | 6% | 6% | 41% | 15345 | 54.5 s | 0.298 | 0.567 |

Working point (none, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 8x, 10%, 15096 tokens.

Working point (rag, lowest average input tokens within 1 percentage point of best diagnostic accuracy): 8x, 6%, 15345 tokens.

## By scenario (lenient accuracy, none → rag)

| scenario | n | 8x |
|---|---|---|
| disk_full | 22 | 9% → 5% |
| crashloop | 8 | 12% → 12% |
| dns | 5 | 40% → 0% |
| other | 28 | 4% → 7% |

## By source (over all ratios, none → rag)

| source | n | strict | lenient | scenario |
|---|---|---|---|---|
| AgenticOpsEval/AIOps2025 | 17 | 0% (0/17) → 0% (0/17) | 0% (0/17) → 0% (0/17) | 35% (6/17) → 35% (6/17) |
| AgenticOpsEval/RCA100 | 4 | 0% (0/4) → 0% (0/4) | 0% (0/4) → 0% (0/4) | 75% (3/4) → 100% (4/4) |
| Cloud-OpsBench | 15 | 27% (4/15) → 13% (2/15) | 27% (4/15) → 13% (2/15) | 67% (10/15) → 53% (8/15) |
| RCAEval | 27 | 7% (2/27) → 7% (2/27) | 7% (2/27) → 7% (2/27) | 33% (9/27) → 30% (8/27) |

## What retrieval did (rag arm)

- Query = a one-line symptom the model wrote from the telemetry (median 1.8 s extra per diagnosis).
- `matched` (a remediation card cleared the 0.34 coverage bar): 41% (26/63) of inputs.
- Retrieval's scenario guess equals the truth scenario, target scenarios only: 0% (0/35).
- One of the three similar historical cases carries the true label in its title: 2% (1/63) of inputs.
- Lenient accuracy when a similar case carried the label: 0% (0/1) vs 6% (4/62) when none did.

Errors: {'none': 0, 'rag': 0}. Prompts truncated to the window: {'none': 5, 'rag': 5}. Predictions and the power trace: `logs/kb-eval/` on the Spark; curves: `docs/kb-eval/curve-*.csv`.
