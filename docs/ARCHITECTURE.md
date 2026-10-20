# Architecture: the Bolt-on-vLLM baseline next to the existing stack

Two inference stacks live in this repository. The first is the one the hackathon drills run on: TensorRT-LLM serving
Nemotron-3-Nano on `:8000`, an OpenAI-compatible shim on `:8001` that fixes what the TRT route cannot accept, a
NemoClaw/OpenClaw sandbox agent, and a host tool API on `:8790`. That stack is described in `docs/current_state.md`
and is not changed by this baseline. The second is the one this document describes: Nemotron-3.5-Bolt on vLLM,
reached through a small Python gateway, exercised by smoke tests, a tool-call benchmark and a minimal incident loop.

```
                      deploy/vllm/start-bolt.sh ──► docker: vllm-bolt (blackbox-vllm-bolt:77154ca)
                                                     vLLM nightly 77154ca · Bolt 74B-A9B IQ2/NVFP4 · :8200 · served name "bolt"
                                                     parsers: qwen3_coder (tools) · ultra_v3 (reasoning)
                                                            ▲ OpenAI-compatible HTTP (BLACKBOX_INFERENCE_BASE_URL)
blackbox/inference ── InferenceGateway.reason() / health() / models()   ◄── the only seam the rest imports
       ▲                       ▲                          ▲
blackbox/agent          blackbox/benchmarks         tests/ (unittest, in-process mock server)
  tool_loop               inference_smoke
  agent_loop              tool_call_smoke / multi_turn_smoke / concurrency_smoke
  incident_state          tool_eval (artifacts/bolt_tool_eval.*)
  profiles (phase 8)      perf_baseline (artifacts/bolt_perf_*.json)
       ▲
blackbox/tools ── ToolRegistry (validate, deny destructive, never raise) ── mock_tools (in-memory world)
```

## Gateway

`blackbox.inference.InferenceGateway` is a client library, not a proxy. `reason(messages, tools, reasoning_effort)`
turns the effort name into the model's chat-template kwargs (`enable_thinking`, `medium_effort`), sets a per-effort
token budget, and returns a `ReasonResult` whose fields the callers can rely on across vLLM versions: `content`,
`reasoning`, `tool_calls` (parsed arguments), `finish_reason`, `truncated`, `reasoning_only`, `usage`, `latency_ms`.
Truncation is reported, never hidden: reasoning and answer share `max_tokens`, so a long think can eat the answer and
the caller must see that. If vLLM's parser misses a tool call that the model wrote as an XML or JSON block in content,
the gateway salvages it and marks the call `raw`, so a benchmark can count how often the parser needed help. The
client retries only connection errors, never HTTP errors, and never retries a bad answer.

Swapping the backend (SGLang, NIM, TensorRT Edge-LLM, the existing TRT route through the shim) is a change of
`BLACKBOX_INFERENCE_BASE_URL` and `BLACKBOX_INFERENCE_MODEL`; no agent or benchmark code knows the engine.

## Mock server

`blackbox.inference.mock_server` is a stdlib OpenAI-compatible server with scripted, deterministic replies: a tool
call when tools are offered and the prompt names one, a final answer after a tool result, exact text and JSON on
request, `finish_reason: length` when `max_tokens` is tiny, an XML tool block on demand to exercise salvage. Every
test and every smoke test runs against it first; the same code then runs unchanged against Bolt. That is what makes
the suite runnable on a laptop and in CI, and what separates "the harness works" from "the model works".

## Tools

`ToolRegistry` holds `Tool(name, description, parameters, handler, risk)`. Execution validates required keys and
primitive types, refuses `destructive` tools in the baseline, converts handler exceptions into `ToolResult` data,
and never executes a shell. The mock tools act on `MockWorld`, an in-memory set of services and containers with
scenarios (`api_down`, `all_healthy`, `web_crashloop`), so a write (`restart_container`) changes what the next read
returns and the loop can verify its own action.

## Agent loop

`run_tool_loop` is the generic OpenAI tool cycle with a turn cap, denial and error counters and a repeat guard (the
same call three times in a row ends the loop). `run_incident` layers the incident shape on it: a system prompt that
states the rules (read before write, one action at a time, verify after every write, never claim success without
a read that shows it), a `finish(status, root_cause, actions)` tool whose `status` is mandatory, an `IncidentState`
that accumulates observations, hypotheses, actions and tool results, and an optional verification hook that turns an
unverified success claim into `failed`. One agent; `profiles.py` only declares the interfaces a second agent would
share (`AgentProfile`, `SharedIncidentState`): one model server, one incident state, one tool layer.

## Benchmarks and artifacts

Every artifact carries `measurement: LOCAL_MEASUREMENT` and an environment block (host, Python, served models,
redacted `BLACKBOX_INFERENCE_*` values, nvidia-smi, git sha). Model-card numbers are quoted only in the
`OFFICIAL_REFERENCE` section of `docs/BOLT_VLLM_BASELINE.md`, never written into an artifact. `tool_eval` scores
every case from its transcript (selection, arguments, unknown-tool avoidance, malformed-argument resistance, error
and denial handling, multi-turn continuation, verification after action); `perf_baseline` measures startup, TTFT,
output tokens/s, ITL, peak memory and token counts at C=1 and C=2.

## Deployment

`deploy/vllm/` pins the image, bakes the model card's two install steps, vendors the reasoning-parser plugin, mounts
the Hugging Face and vLLM caches from the host, reads `HF_TOKEN` only from the environment, and starts with no
restart policy because this box deliberately runs nothing at boot. Context is capped at 32k and concurrency at 1
until measurements justify more; GPU memory utilisation defaults to 0.45 of the 119 GiB unified memory so the
sandbox, the fault stack, the agent and later a RAG process keep their share.

## What is deliberately absent

RAG, multi-agent orchestration, a UI, speculative decoding (Phase 7, measured against this baseline before it is
kept), 256K context, a shell executor, and any edit to the drilled stack.
