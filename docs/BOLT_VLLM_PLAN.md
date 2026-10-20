# Bolt on vLLM: baseline plan (2026-10-16)

Branch `feat/blackbox-bolt-vllm`. Goal: a second inference foundation, nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB on
vLLM behind an inference gateway, validated by smoke tests, a tool-call benchmark and a minimal incident loop, without
touching the TensorRT-LLM stack, the NemoClaw drills or anything with measured results. Audit of the current repo:
`docs/current_state.md`.

## Decisions

- **Layout.** New code lives in a package, `blackbox/`, next to the existing flat `scripts/` (which stay as they are):
  `blackbox/inference` (gateway, schemas, health, mock server), `blackbox/tools` (registry, schemas, mock tools),
  `blackbox/agent` (incident state, tool loop, agent loop, profiles), `blackbox/benchmarks` (smoke tests, tool eval,
  perf baseline). Deployment under `deploy/vllm/`, config example under `config/`, tests under `tests/` (stdlib
  `unittest`, run with `uv run --no-sync -m unittest discover -s tests`), artifacts under `artifacts/` (gitignored
  except `.gitkeep`; benchmark results are committed deliberately, not by default).
- **Ports and names.** Bolt serves on `:8200` as container `vllm-bolt`, served model name `bolt`. `:8000`
  (trtllm-serve), `:8001` (shim), `:8100` (the old vLLM fallback), `:8790` (ops API) are untouched.
- **Image.** Pinned to the model card's `vllm/vllm-openai:nightly-77154ca6116ae34cbfe314beef1016bde9b63989`
  (arm64 manifest exists, 10.3 GB compressed). A derived image `blackbox-vllm-bolt:<short-tag>` bakes
  `pip install b12x` and `vllm download-kernels` so a start does not depend on PyPI. The reasoning parser plugin
  `ultra_v3_reasoning_parser.py` is vendored under `deploy/vllm/` (28 lines, from the model repo, sha pinned).
- **Serve flags.** The model card's recipe minus speculative decoding and minus the media flags, with
  `--max-model-len ${MAX_MODEL_LEN:-32768}`, `--max-num-seqs ${MAX_NUM_SEQS:-1}`, `--gpu-memory-utilization
  ${GPU_MEMORY_UTILIZATION:-0.45}` (GB10 has 119 GiB unified memory shared with the OS, OpenShell, the fault stack
  and the agent; 0.45 leaves the rest), `--served-model-name bolt`, `--port ${BOLT_PORT:-8200}`,
  `--enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser ultra_v3`. `VLLM_USE_V2_MODEL_RUNNER=1`.
  Hugging Face cache mounted from `${HF_HOME:-$HOME/.cache/huggingface}` (restart-safe); `HF_TOKEN` only from the
  environment. No speculative decoding until Phase 7.
- **Gateway contract** (`blackbox/inference/gateway.py`). The rest of the code imports only this:

  ```python
  gw = InferenceGateway()                      # BLACKBOX_INFERENCE_BASE_URL, BLACKBOX_INFERENCE_MODEL, BLACKBOX_INFERENCE_TIMEOUT_S
  r = gw.reason(messages, tools=None, reasoning_effort="medium", max_tokens=None, temperature=0.0,
                tool_choice="auto", response_format=None, extra=None)
  r.content, r.reasoning, r.tool_calls (list[ToolCall(id, name, arguments: dict, raw)]), r.finish_reason,
  r.truncated (finish_reason == "length"), r.reasoning_only (reasoning present, content empty: the thinking
  budget ate the answer), r.usage (prompt_tokens, completion_tokens, total_tokens), r.latency_ms, r.model, r.raw
  gw.health() -> {"ok": bool, "status": int|None, "latency_ms": int, "error": str|None}
  gw.models() -> list[str]
  ```

  `reasoning_effort` maps to the model's chat-template kwargs: `"none"`/`"low"` → `enable_thinking: false`;
  `"medium"` → `enable_thinking: true, medium_effort: true`; `"high"` → `enable_thinking: true`. Default
  `max_tokens` per effort: 1024 / 4096 / 8192 (reasoning and answer share it; truncation is reported, never hidden).
  Tool calls come from vLLM's parser; if the parser misses and the content carries a `<tool_call><function=…>`
  block, the gateway salvages it and marks `tool_calls[i].raw`. The gateway never retries silently.
- **Mock server** (`blackbox/inference/mock_server.py`, stdlib `http.server`): an OpenAI-compatible
  `/v1/chat/completions`, `/v1/models`, `/health` with scripted, deterministic replies (plain text, JSON, a tool
  call when tools are offered and no tool result is in the conversation yet, a final answer after a tool result).
  Every test and smoke test runs against it with `BLACKBOX_INFERENCE_BASE_URL=http://127.0.0.1:<port>`; the same
  tests run unchanged against Bolt.
- **Tools** (`blackbox/tools/`). A registry of `Tool(name, description, parameters, handler, risk)` with
  `risk ∈ {read, write, destructive}`; `execute()` denies destructive tools in the baseline and returns a structured
  `ToolResult(ok, output, error, denied)`. Mock tools act on an in-memory world (services, containers, logs) so a
  multi-turn cycle can be verified: `get_service_health`, `get_container_status`, `get_recent_logs`,
  `restart_container` (write, allowed), `delete_volume` (destructive, denied). No shell executor.
- **Agent** (`blackbox/agent/`). `IncidentState` (incident_id, objective, observations, hypotheses, actions_taken,
  tool_results, status ∈ {open, resolved, escalated, failed}, turns); `tool_loop.run()` is the generic multi-turn
  OpenAI tool cycle; `agent_loop.run_incident()` is OBSERVE → REASON → SELECT TOOL → EXECUTE → UPDATE → VERIFY →
  CONTINUE/COMPLETE with a `finish(status, root_cause, actions)` tool, a turn cap and a repeat guard. One agent.
  `profiles.py` holds only the Phase 8 interfaces: `AgentProfile(name, allowed_skills, allowed_tools,
  system_instructions)` and `SharedIncidentState = IncidentState`.
- **Benchmarks.** `inference_smoke` (exact string, JSON, reasoning on/off with truncation detection),
  `tool_call_smoke`, `multi_turn_smoke` (the mandatory full cycle), `concurrency_smoke` (C=1 then C=2),
  `tool_eval` (≥20 deterministic cases → `artifacts/bolt_tool_eval.json` + `.md`), `perf_baseline` (startup,
  TTFT, output tok/s, ITL, peak memory from nvidia-smi, prompt/completion tokens, wall time, with environment
  metadata). Every artifact carries `measurement: LOCAL_MEASUREMENT` and the environment block; nothing from the
  model card is ever written as a result.

## Phases and owners

| Phase | Deliverable | Owner | Depends on |
|---|---|---|---|
| 0 | `docs/current_state.md`, this plan | Codex A / Claude | – |
| 1 | `deploy/vllm/{Dockerfile,start-bolt.sh,stop-bolt.sh,healthcheck.sh,ultra_v3_reasoning_parser.py}`, `config/inference.env.example`, `Makefile` targets, docs skeleton | Codex B; runtime validation on the Spark by Claude | 0 |
| 2 | `blackbox/inference/*`, `tests/inference/*` | Codex C | 0 |
| 3 | `blackbox/benchmarks/{inference,tool_call,multi_turn,concurrency}_smoke.py` | Codex D | 2 |
| 4 | `blackbox/tools/*`, `blackbox/agent/*`, `tests/agent/*` | Codex E | 2 (contract only) |
| 5 | `blackbox/benchmarks/tool_eval.py` + artifacts | Codex F | 2, 4 |
| 6 | `blackbox/benchmarks/perf_baseline.py` + artifacts | Codex F | 1, 2 |
| 7 | DFlash on/off comparison | Claude on the Spark | 1–6 green |
| 8 | profiles interfaces only | in E | 4 |

Codex works in a git worktree per task on a `codex/<task>` branch off the feature branch; Claude reviews each diff
and merges. Anything that needs the GPU runs on the Spark from the `~/edge-agent-bolt` worktree.

## Hardware (measured 2026-10-16)

DGX Spark, NVIDIA GB10 (SM121, aarch64), driver 580.126.09, 119 GiB unified memory (93 GiB available), Docker
default runtime `nvidia`, kernel 6.17.0-1008-nvidia. The model card lists DGX Spark among tested hardware. Root disk
94 % full (52 GB free at the time of the image pull); the image (~10 GB compressed) plus the model (24.1 GB, 118
files, revision c6cc2e8f) need space freed before the model download. Target deployment stays Jetson AGX Thor; the
Spark is the validation box.

## Answers to the audit's open questions (`docs/current_state.md` §12)

- The gateway is a **client library** (`blackbox.inference.InferenceGateway`), not a new HTTP proxy: the rest of the
  new code imports it and reads `BLACKBOX_INFERENCE_BASE_URL`. The shim on `:8001` and NemoClaw's route stay exactly
  as they are; nothing in `scripts/` is repointed in this baseline. Backend comparisons later (TRT-LLM, SGLang,
  NIM, TensorRT Edge-LLM) only change the base URL and model name.
- Bolt listens on `:8200`, served model `bolt`, container `vllm-bolt`; no shared alias with `edge-agent`.
- No request rewriting for vLLM. The gateway's only compensation is output-side salvage of an XML tool call left
  in content, and the benchmark records how often it fired; if it never fires on Bolt, it is dead code to delete.
- Residency: trtllm-serve is not running on the Spark today (`:8000` is down); Bolt gets the GPU. Whether both can
  be resident is measured in Phase 6 (peak memory), not assumed.
- Usage rows and `score.py --shim` are TRT-route concerns and untouched.
- Mac-to-Spark sync stays git: the Mac pushes `feat/blackbox-bolt-vllm` to the Spark, where the branch is checked
  out as a separate worktree `~/edge-agent-bolt` so the teammate's `kb-retrieval-v2` checkout is undisturbed.

## Not in this baseline

RAG, multi-agent, UI, speculative decoding (until Phase 7), 256K context, max_num_seqs > 2, any shell executor,
edits to `scripts/`, `nemoclaw/`, `skills/`, `runbooks/`, `kb/`, `trt/`.
