# Bolt vLLM baseline

This is the reproducible Phase 1 service definition for
`nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB` on the DGX Spark. Run all commands from the repository root. Docker,
the NVIDIA Container Toolkit, GPU access, and enough local storage for the image and model cache are prerequisites.
`HF_TOKEN` is optional and is read only from the launch environment.

## Reproduce

1. Build the pinned derived image. This is the networked step that installs `b12x` and downloads the vLLM kernels:

   ```sh
   make bolt-build
   ```

2. If the model is gated, export `HF_TOKEN` in the current shell, then start the service:

   ```sh
   make bolt-up
   ```

3. Wait through the multi-minute model load and verify both `/health` and `/v1/models`:

   ```sh
   make bolt-wait
   ```

4. Run the repository tests (33, all against the in-process mock server, no GPU needed), then the smoke tests,
   the tool-call benchmark and the performance baseline against Bolt. `BLACKBOX_INFERENCE_BASE_URL` defaults to
   `http://127.0.0.1:8200` and the model name to `bolt` (`config/inference.env.example`):

   ```sh
   uv run --no-sync -m unittest discover -s tests
   uv run --no-sync -m blackbox.benchmarks.inference_smoke   --out artifacts/bolt_inference_smoke.json
   uv run --no-sync -m blackbox.benchmarks.tool_call_smoke   --out artifacts/bolt_tool_call_smoke.json
   uv run --no-sync -m blackbox.benchmarks.multi_turn_smoke  --out artifacts/bolt_multi_turn_smoke.json
   uv run --no-sync -m blackbox.benchmarks.concurrency_smoke --out artifacts/bolt_concurrency_smoke.json
   uv run --no-sync -m blackbox.benchmarks.tool_eval --out-json artifacts/bolt_tool_eval.json --out-md artifacts/bolt_tool_eval.md
   uv run --no-sync -m blackbox.benchmarks.perf_baseline --concurrency 1 --startup-log logs/bolt-startup.log --out artifacts/bolt_perf_c1.json
   uv run --no-sync -m blackbox.benchmarks.perf_baseline --concurrency 2 --out artifacts/bolt_perf_c2.json
   ```

   The existing engine smoke test also runs unchanged: `VLLM_URL=http://127.0.0.1:8200 SERVED_NAME=bolt uv run --no-sync scripts/smoke.py`.

5. When measurement is complete, stop and remove only the Bolt container:

   ```sh
   make bolt-down
   ```

The effective vLLM argument vector is recorded one argument per line in `$BOLT_LOG_DIR/bolt-args.txt`. The baseline
deliberately excludes speculative decoding and media flags.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `BLACKBOX_INFERENCE_BASE_URL` | `http://127.0.0.1:8200` | OpenAI-compatible endpoint read by application clients. |
| `BLACKBOX_INFERENCE_MODEL` | `bolt` | Model alias read by application clients. |
| `BLACKBOX_INFERENCE_TIMEOUT_S` | `300` | Client request timeout in seconds. |
| `BOLT_IMAGE_TAG` | `nightly-77154ca6116ae34cbfe314beef1016bde9b63989` | Pinned upstream vLLM image tag. |
| `BOLT_IMAGE` | `blackbox-vllm-bolt:77154ca` | Local derived image name. |
| `BOLT_CONTAINER` | `vllm-bolt` | Docker container name. |
| `MODEL_ID` | `nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB` | Hugging Face model identifier. |
| `MODEL_REVISION` | empty (`main`) | Optional Hugging Face revision; the plan documents `c6cc2e8f`. |
| `SERVED_MODEL_NAME` | `bolt` | Stable API model alias. |
| `BOLT_HOST` | `0.0.0.0` | vLLM bind address. |
| `BOLT_PORT` | `8200` | vLLM service port. |
| `MAX_MODEL_LEN` | `32768` | Maximum context length. |
| `MAX_NUM_SEQS` | `1` | Baseline concurrent sequence limit. |
| `GPU_MEMORY_UTILIZATION` | `0.45` | vLLM GPU-memory reservation fraction. |
| `HF_HOME` | `$HOME/.cache/huggingface` | Host Hugging Face cache mounted into the container. |
| `VLLM_CACHE_DIR` | `$HOME/.cache/vllm-bolt` | Host vLLM cache mounted into the container. |
| `BOLT_LOG_DIR` | `./logs` | Host directory for launch metadata. |
| `BOLT_EXTRA_ARGS` | empty | Additional whitespace-delimited vLLM arguments. |
| `HF_TOKEN` | unset | Optional Hugging Face token inherited only when exported. |
| `HF_HUB_OFFLINE` | unset | Optional Hugging Face offline-mode setting inherited only when exported. |

## Running on the Spark from the feature worktree

The branch is checked out on the Spark as a separate git worktree, `~/edge-agent-bolt`, so the main checkout
(`~/edge-agent`) can stay on whatever branch the team is drilling with. A worktree has no `.venv` of its own and
`uv run --no-sync` would otherwise create an empty one; link the project environment once:

```bash
cd ~/edge-agent-bolt && ln -s ~/edge-agent/.venv .venv
uv run --no-sync -m unittest discover -s tests        # 12 gateway tests, measured 2026-10-16: OK
```

The derived image is built on the Spark with `make bolt-build` (measured 2026-10-16: `blackbox-vllm-bolt:77154ca`,
24.7 GB). `vllm download-kernels` cannot run inside `docker build` (no GPU to probe), so the Dockerfile performs the
two pip installs it stands for, pinned.

## Results

### OFFICIAL_REFERENCE

Source: [NVIDIA Nemotron 3.5 Bolt 74B A9B 24GB model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB), accessed 2026-10-16.

The model card's installation recipe states:

> `pip install b12x`
>
> `vllm download-kernels`

Those commands and the card's vLLM serve flags are baked into this baseline image and launcher. This section records
reference instructions only; it does not treat model-card statements as local measurements.

### LOCAL_MEASUREMENT

Measured 2026-10-17 on the DGX Spark (`artifacts/bolt_perf_c1.json`, `artifacts/bolt_perf_c2.json`; medians over six
requests, all six successful at each concurrency).

| C | startup_time_s | TTFT_ms | output_tok_s | ITL_ms | peak_memory | prompt_tokens | completion_tokens | wall_time_ms | environment |
|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 1 | 300 ¹ | 742 (p90 753) | 32.8 | 33.3 | n/a ² | 1368 | 24 | 1506 | GB10, `blackbox-vllm-bolt:77154ca`, model rev `c6cc2e8f`, `HF_HUB_OFFLINE=1`, `BOLT_HOST=127.0.0.1`, util 0.45, 32k ctx |
| 2 | 295 ¹ | 1420 (p90 5254) | 25.0 per request | 46.0 | n/a ² | 1368 | 24 | 2232 | same, `MAX_NUM_SEQS=2` |

¹ From `make bolt-wait` (start to healthy, weights already in the cache); the artifacts' `startup_time_s` is empty
because `perf_baseline` did not parse it from `logs/bolt-startup.log`.
² `nvidia-smi` reports memory as `[N/A]` on GB10 unified memory, so `peak_gpu_memory_mib` is empty in both artifacts.

Gate results on this run: 1 ✓ (healthy after 300 s); 2–3 ✓ (6/6 inference smoke); 4 ✓ (single parsed tool call);
6 ✓ (`api_down` resolved: logs → `restart_container` → health read → finish, 7 turns); 7 ✓ (27/27 cases ran, 23
passed, `artifacts/bolt_tool_eval.md`); 8–9 ✓ for the request rows, with the two empty fields above. Gate 5 is not
closed: `multi_turn_smoke` passes, but `tool_eval` continuation is 1/3 and verification 2/3. Two of those failures did
the work (logs → restart → health read) and ran out of the 4-turn case cap before the final answer; one never found
the database container; `unsupported_rotate_cert` kept investigating instead of saying no tool can rotate a
certificate. Phase 7 (DFlash) was not run, since it waits on Gates 1–9.

### NOT_YET_TESTED

- Peak memory on GB10 (not readable through `nvidia-smi`).
- Co-residency with the existing TensorRT-LLM service.
