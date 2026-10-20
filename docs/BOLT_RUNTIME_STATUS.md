# Nemotron 3.5 Bolt + vLLM Baseline Status

## Status

PARTIAL — CODE_COMPLETE / NOT_RUNTIME_VERIFIED. Branch `feat/blackbox-bolt-vllm`. The inference foundation is
implemented and validated against a mock OpenAI-compatible endpoint; real Bolt runtime validation is blocked only
by DGX Spark storage capacity.

HEAD: see `git log --oneline -1` on `feat/blackbox-bolt-vllm`.

## Completed

- Repository audit and plan; reproducible Bolt vLLM deployment; OpenAI-compatible inference gateway with
  client, health, and schema layers; four inference smoke tests; minimal `IncidentState` and agent loop; typed
  tool registry and mock operational world; multi-turn tool-loop harness; 27-case tool-call evaluation harness;
  C=1/C=2 performance benchmark harness; architecture and reproduction docs; 33/33 unit tests on macOS and DGX
  Spark.

## Runtime environment verified

- DGX Spark GB10, aarch64, 119 GiB unified memory, NVIDIA driver 580.126.09, and Docker default runtime `nvidia`.
- Derived image `blackbox-vllm-bolt:77154ca` builds and is 24.7 GB. `b12x` 1.5.1 installs.
- `flashinfer-jit-cache` 0.7.0.post1+cu130 with `sm121a` is already in the pinned nightly. The pinned image has an
  arm64 manifest.

## Mock-validated

- Chat endpoint; inference smoke CLI; tool-call and multi-turn smoke; tool evaluation; performance artifact
  generation; destructive tools denied by design; no arbitrary shell executor.

## Not yet runtime-verified

- Bolt startup; health against the real model; reasoning parser; real structured tool calling; full multi-turn
  model/tool loop; real agent-loop behaviour; real tool-eval metrics; C=1/C=2 performance; LiLiCorr/DFlash.

## Current blocker

- DGX root disk has about 35 GB free and is 97% full. The weights are 24.1 GB across 118 files at revision
  `c6cc2e8f`, plus cache headroom.
- Planned cleanup, pending the owner's approval: Docker build cache about 49.8 GB and stopped containers about
  1.9 GB. The Hugging Face model cache, TensorRT-LLM image, hackathon Nemotron-3-Nano assets, lab data, and the
  teammate's `kb-retrieval-v2` checkout stay untouched.

## Runtime acceptance gates

1. `/v1/models` lists `bolt` and `/health` succeeds.
2. Plain chat returns exactly `BLACKBOX_BOLT_OK`.
3. With reasoning effort `high`, `reasoning_content` is present; with `none`, it is absent; truncation is flagged.
4. The model emits one valid structured tool call with the expected name and arguments.
5. The full tool → result → re-reason → final cycle succeeds. This is the decisive gate: one function call is not
   success. The model must consume the result, change its hypothesis, call the next tool, and verify.
6. The minimal incident loop resolves the `api_down` mock world and verifies recovery.
7. All 27 `tool_eval` cases run and their metrics are recorded.
8. The C=1 performance baseline is recorded.
9. The C=2 performance baseline is recorded with `MAX_NUM_SEQS=2`.
10. DFlash A/B is kept only if tool-correctness metrics do not drop.

## Deliberately deferred

- RAG, multi-agent orchestration, UI, and speculative decoding before the baseline is validated. They are deferred
  so model and tool-loop reliability can be measured first.
