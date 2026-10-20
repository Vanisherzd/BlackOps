# Expert pruning: Nemotron-3-Nano NVFP4, 128 → 112 routed experts per layer

## What was pruned

`scripts/prune-experts.py 112` drops, in each of the 23 MoE layers, the 16 routed experts with the least routing mass
on the workload (drill sessions + compressed telemetry, counted by `scripts/moe-routing-stats.py collect`, see
`docs/moe-routing-stats.md`). It slices the NVFP4 checkpoint directly: the surviving experts' quantised weights and
scales are copied unchanged and renumbered, the router's `gate.weight` and `e_score_correction_bias` keep the same
rows, `n_routed_experts` becomes 112. No requantisation, no fine-tuning, so a drill comparison against the unpruned
checkpoint isolates the pruning.

- Routing mass kept per layer: 93.6 % (layer 1) to 99.6 % (layer 17).
- Checkpoint: 18.5 GiB → 16.1 GiB. Active parameters per token are unchanged (still top-6), so decode speed is not
  expected to improve; the saving is memory.
- TensorRT-LLM 1.3.0rc13 serves it with `trt/nano.yaml` unchanged; the startup autotuner warnings (14 failed
  `fused_moe::gemm2` tactics) are identical to the unpruned model's.

## Drills (2026-10-18, same code, same 25 drills as the unpruned baseline of 2026-10-17)

| | unpruned (`drills-20261017-202633`, `-205425`) | e112 (`drills-20261018-145528`) |
|---|---|---|
| seen, 9 cases × 1 | 9/9, median 45 s | 9/9, median 67 s |
| inode-exhausted × 1 | 1/1, 91 s | 1/1, 267 s |
| blind batch 2, 5 cases × 3 | 11/15, median 128 s | 11/15, median 106 s |
| total autonomous | 21/25 | 21/25 |
| made-worse / false health claims | 0 / 0 | 0 / 0 |
| cause named (scorer regex) | seen 10/10, blind2 11/15 | seen 9/10, blind2 12/15 |

Per case the blind runs moved both ways (network-removed 2/3 → 3/3, front-include-missing 3/3 → 2/3;
upstream-index-deleted 0/3 in both). On the seen cases the pruned model made more calls: efficiency dropped below 1.0
on four cases, and on upstream-detached it restarted the container before recreating it and named the wrong cause.

Reading: no loss of resolution rate is visible at n = 1–3 per case, where reruns of an identical setup already differ
by a drill or two (the knowledge-card A/B: 16/25 vs 15/25). A small efficiency loss on the seen cases cannot be ruled
out. 96 experts per layer (92 % held-out routing coverage) is the next step if a larger saving is wanted.
