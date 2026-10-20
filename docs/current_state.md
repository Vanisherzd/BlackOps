# Current repository state before a second inference baseline

This is a static audit of the tracked repository as of 2026-10-16. Primary evidence is `README.md`, `docs/agent-design.md`, and `scripts/`. Runtime state was not queried; services and drills were not run. The proposed `nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB` baseline and an inference-gateway implementation are **not found** in the tracked sources.

## 1. Current services and ports

Nothing is intended to start at boot; the checked-in systemd units exist but are deliberately not enabled (`README.md:178-193`, `docs/plan-trtllm-nemoclaw.md:100-105`).

| Long-running component | Start command / owner | Bind or port | Environment/configuration | Health check |
|---|---|---|---|---|
| TensorRT-LLM inference container `trtllm-edge` | `HOST=0.0.0.0 nohup scripts/serve-trt.sh > logs/trt-serve.log 2>&1 &`; internally `docker run ... trtllm-serve serve` (`scripts/serve-trt.sh:19-27`) | default `127.0.0.1:8000`; demo uses `HOST=0.0.0.0` | `MODEL`, `IMAGE`, `YAML`, `NAME`, `FORCE`, `PORT`, `SERVED_NAME`, `HF_HUB_OFFLINE`, `TRTLLM_ENABLE_PDL`, `REASONING_PARSER`, `TOOL_PARSER`; `trt/nano.yaml` | `GET /health`; `GET /v1/models`; the script avoids replacing an already healthy container unless `FORCE=1` (`scripts/serve-trt.sh:15-17`) |
| OpenAI compatibility shim | `nohup uv run --no-sync scripts/oai_shim.py > logs/oai-shim.out 2>&1 &` | `0.0.0.0:8001` -> default `127.0.0.1:8000` | `SHIM_PORT`, `UPSTREAM`, `SHIM_THINK`, `SHIM_DEBUG` (`scripts/oai_shim.py:17-22,266-269`) | No shim-specific health endpoint; `GET` is proxied upstream. Bootstrap validates it with `VLLM_URL=http://127.0.0.1:8001 scripts/smoke.py` (`scripts/bootstrap-spark.sh:39-41`) |
| Host ops HTTP/MCP API | `nohup uv run --no-sync scripts/ops_api.py > logs/ops-api.out 2>&1 &` | `0.0.0.0:8790` | `OPS_PORT`, `OPS_DRY_RUN`, and inherited `VLLM_URL`/`SERVED_NAME` for `self_heal` (`scripts/ops_api.py:30-35,122-125`) | No `/health`; `GET /tools` is the documented probe (`README.md:117-123`) |
| NemoClaw/OpenShell gateway and sandbox `edge-agent` | Provisioned by `scripts/bootstrap-spark.sh nemoclaw`; restarted with `nemoclaw edge-agent gateway restart`; after reboot: `nemoclaw edge-agent start` (`scripts/bootstrap-spark.sh:43-69`, `README.md:178-188`) | Repo design documents gateway `8080`, UI `18789`; sandbox reaches host aliases `:8001` and `:8790` (`docs/plan-trtllm-nemoclaw.md:17-31`) | Onboarding uses `NEMOCLAW_PROVIDER=custom`, `NEMOCLAW_ENDPOINT_URL`, `NEMOCLAW_MODEL`, `NEMOCLAW_PROVIDER_KEY=none`, `NEMOCLAW_REASONING`, `NEMOCLAW_SANDBOX_NAME`; provider is patched to `http://host.openshell.internal:8001/v1` | `nemoclaw edge-agent status | grep Inference`; one-word agent probe in `README.md:143-156` |
| Victim front end `edge-victim` | `uv run --no-sync scripts/faults.py setup`; exact spec is `docker run -d ... -p 127.0.0.1:8880:80 -v victim/conf.d:/etc/nginx/conf.d:ro nginx:latest` (`scripts/faults.py:71-76,98-106`) | host `127.0.0.1:8880`; container `80` | Docker network `edge-net`; generated host config `victim/conf.d/default.conf`; image is unpinned `nginx:latest` | `GET /` and `GET /api/` must both return 200 (`scripts/faults.py:61-68,162-165`) |
| Victim upstream `edge-victim-app` | Same setup command; `docker run -d --name edge-victim-app --network edge-net --restart no nginx:latest` (`scripts/faults.py:71-75`) | container-only port `80`; no host publish | Docker network `edge-net`; image is unpinned `nginx:latest` | Observed through front-end `GET /api/`; no separate health endpoint is defined |
| vLLM fallback process | `nohup scripts/serve.sh > logs/serve.log 2>&1 &`; optional dormant user unit `systemd/vllm-edge.service` runs the same script | default `127.0.0.1:8100` | `MODEL`, `SERVED_NAME`, `HOST`, `PORT`, `MAX_MODEL_LEN`, `MAX_NUM_SEQS`, `GPU_MEM_UTIL`, `TOOL_PARSER`, `SPEC`, plus CUDA/JIT/offline variables (`scripts/serve.sh:7-33`) | `GET /health`; `scripts/smoke.py` waits up to 900 seconds (`scripts/smoke.py:17-30`) |

The TensorRT and vLLM systemd units conflict, and the repository states that the two large models cannot be loaded together (`systemd/trtllm-edge.service:5`, `README.md:169-176`). Whether that remains true for the proposed Bolt baseline is not established.

## 2. Current inference backend

The current primary backend is `trtllm-serve` from `nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13`, serving `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` as stable model ID `edge-agent` (`scripts/serve-trt.sh:6-8,20-27`). The Hugging Face cache is mounted into the container, a local tokenizer snapshot is passed for offline operation, and the launch enables `nano-v3` reasoning parsing and `qwen3_coder` tool parsing.

`trt/nano.yaml` is production: 32,768-token sequence length, 8,192 token batch budget, chunked prefill, batch size 8, block reuse off, 50% of free memory for KV cache, float32 Mamba SSM cache, and CUDA-graph padding (`trt/nano.yaml:1-13`). Experimental alternatives are `nano-kvfp8.yaml` (FP8 KV), `nano-ngram.yaml` (NGram speculative decoding with overlap scheduler disabled), and `nano-reuse.yaml` (block reuse); measured regressions/failures leave them non-production (`README.md:237-239`).

### OpenAI-compatible shim

Only `/v1/chat/completions` request bodies are normalized; other methods/paths are proxied (`scripts/oai_shim.py:248-260`). `normalize()`:

- rewrites `developer` role to `system`, flattens text-part content arrays, converts non-assistant null content to `""`, and strips assistant/tool messages to TensorRT-LLM's accepted keys (`scripts/oai_shim.py:25-51`);
- drops `store`, `metadata`, and `reasoning_effort`, then injects `chat_template_kwargs.enable_thinking=true` unless `SHIM_THINK=0` (`scripts/oai_shim.py:17-19,52-59`);
- repairs tool argument values to strings in assistant history.

The shim salvages two malformed model-output forms: XML `<tool_call><function=...><parameter=...>` left in `reasoning_content`, and plain JSON `{name, arguments}` emitted as answer text (`salvage_calls()` and `json_text_calls()`, `scripts/oai_shim.py:63-134`). Non-streaming responses receive `message.tool_calls` and `finish_reason=tool_calls` (`salvage_json()`, lines 195-211). Streaming remains incremental: data is forwarded immediately, only the first finish chunk and following usage/`[DONE]` chunks are held, and a tool-call delta plus rewritten finish reason is injected only when salvage is needed (`stream_with_salvage()`, lines 137-192). Token usage is appended to `logs/oai-shim.log` for completed JSON responses and usage-bearing SSE chunks; debug mode additionally logs request shapes and upstream 4xx bodies (`scripts/oai_shim.py:67-79,157-175,224-229`).

### Existing vLLM path

`scripts/serve.sh` is a bare-metal `.venv` launch, not a container. It defaults to `Qwen/Qwen3.6-35B-A3B-FP8`, MTP with three speculative tokens, 32k context, eight sequences, 45% GPU-memory utilization, automatic tool choice with `qwen3_coder`, and the native `qwen3` reasoning parser (`scripts/serve.sh:16-33`). The lock resolves vLLM 0.29.0, torch 2.13.0, FlashInfer 0.6.18, transformers 5.17.0, and OpenAI 3.16.2 (`uv.lock:1324-1325,3510-3511,5902-5903,6074-6075,6280-6281`). It is documented as a manual fallback and is dormant by default; historical benchmark results exist, but no autostart is enabled. `scripts/try.sh` provides a temporary `:8101` vLLM experiment path and runs benchmark plus smoke checks (`scripts/try.sh:1-29`). Configuration for the proposed Bolt model is **not found**.

## 3. Existing APIs

### Inference

TensorRT-LLM and vLLM expose OpenAI-compatible `/v1` APIs plus `/health`. The shim forwards all paths and normalizes only chat completions. Repository clients construct `OpenAI(base_url=f"{VLLM_URL}/v1", api_key="none")`; no API authentication or authorization is configured in this repo. NemoClaw/OpenShell adds its own sandbox endpoint policy, but that is not HTTP API authentication.

### Host tool HTTP API

`scripts/ops_api.py` derives its public list from `agent.TOOLS`, removes `finish`, and appends `self_heal` (`scripts/ops_api.py:18-27`). There is explicitly no auth for the lab drill (`scripts/ops_api.py:8-9`).

- `GET /tools` -> `{"tools": [<name, description, parameters>...]}`.
- `POST /call` body `{"name":"<tool>","args":{...}}` -> HTTP 200 `{"result":"<string>"}`; malformed/unknown calls return HTTP 400 `{"error":"..."}` (`scripts/ops_api.py:88-116`).
- `POST /mcp` accepts one JSON-RPC 2.0 request or a batch. Supported methods are `initialize`, `tools/list`, `tools/call`, `ping`, and notifications. `tools/call` returns `result.content=[{"type":"text","text":"..."}]` plus `isError`; unknown tools use JSON-RPC `-32602` (`scripts/ops_api.py:54-83`).
- `GET /mcp` returns 405; `DELETE /mcp` returns 200 for session teardown. The implementation is request/response Streamable HTTP with no server-initiated stream (`scripts/ops_api.py:47-52,85-98`).

Every call is logged to `logs/ops-api.log` with source, tool, args, latency, and truncated result. `OPS_DRY_RUN=1` is passed to agent mutations; `self_heal` instead starts `agent.py --case self-heal --max-steps 8` with a 900-second timeout.

### Sandbox helper

`scripts/ops-sandbox-helper.sh` is uploaded as `/sandbox/bin/ops`. `ops tools` reads `/tools`; `ops call <tool> '<json>'` posts `/call` and prints `result`. `OPS_API` defaults to `http://host.openshell.internal:8790`, with 20-second list and 120-second call timeouts (`scripts/ops-sandbox-helper.sh:1-24`). It is the shell fallback when first-class MCP tools are absent (`skills/ops/SKILL.md:66-68`).

## 4. Current agent implementation

`scripts/agent.py` implements observe -> retrieve -> decide/tool call -> act -> verify. It records initial health/container state, sends the full `TOOLS` schema to an OpenAI client, runs up to `--max-steps` (default 8), dispatches every returned call, asks again when the model returns no call, and ends when the model calls `finish` or the step budget expires (`scripts/agent.py:558-611`). The process exit code is based on final 200/200 health, not on the model's declared `finish.status`.

`run_tool()` normalizes common argument aliases, validates required arguments, restricts container names to `edge-victim`/`edge-victim-app`, restricts `docker_exec` to a read-only command prefix without shell operators, and constrains network probes to loopback/private non-link-local addresses (`scripts/agent.py:301-362,393-460`).

The module-global `_incident={runbook_seen, refusals, trace}` carries incident-local gate state and the last 30 truncated tool results (`scripts/agent.py:34-44,301-310`). All seven mutating tools are refused until a runbook has been seen; the third identical attempt is allowed to avoid deadlock. Matching runbooks are pushed into container evidence, known container states map to fixed runbooks, and green health resets the incident (`scripts/agent.py:77-122,348-392`). `search_incident_knowledge` deliberately does not set `runbook_seen`.

Destructive operations use a two-key reviewer. Code first checks sufficiency: rebuild requires a prior restart plus `/api/` still failing, kill requires the PID in `top_processes`, cleanup requires root usage >=85%. Only then does the no-tools reviewer model judge relevance/contradiction; inability to obtain a verdict rejects the action (fail closed). A second no-thinking pass is used if the thinking pass has no content; reasoning text is never parsed as a verdict (`scripts/reviewer.py:64-132,145-171`). `REVIEW=0` disables this for A/B measurement. Reviews go to `logs/reviews.jsonl`.

The model's `finish` tool accepts `status=resolved|unresolved|escalate`, `root_cause`, and `actions` (`scripts/agent.py:191-213`). The host loop logs each decision to `logs/agent/*.jsonl` and prints final health.

The sandbox agent is driven by:

- `skills/ops/SKILL.md`: stack facts, read-first procedure, tool contract, scope/stop rules, and `/sandbox/bin/ops` fallback;
- `nemoclaw/workspace/AGENTS.md`, `TOOLS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`: concise role, endpoints, evidence-first behavior, and required three-line report; `HEARTBEAT.md` is comments-only so scheduled calls are skipped;
- `nemoclaw/openclaw-patch.json5`: direct tool exposure (`toolSearch:false`), minimal profile plus MCP/exec/read, only the `ops` skill, 32k context, 12,288 output tokens, reasoning on, and heartbeat off (`nemoclaw/openclaw-patch.json5:5-40`).

## 5. Current tools

This is the full union of the host-loop whitelist and ops API surface (`scripts/agent.py:191-213`, `scripts/ops_api.py:23-27`).

| Tool | Class | Arguments | What it touches |
|---|---|---|---|
| `check_health` | read-only | none | HTTP GET to victim `/` and `/api/`; may reset in-memory incident state when green |
| `docker_ps` | read-only | none | Docker metadata for the two victim containers |
| `docker_logs` | read-only | `name`, optional `tail=40` | Logs of one allowed victim container; may read and attach a runbook |
| `docker_exec` | read-only | `name`, `cmd` | Allowed diagnostic command inside one victim container |
| `docker_start` | mutating | `name` | Starts one allowed victim container |
| `docker_restart` | mutating | `name` | Restarts one allowed victim container |
| `read_config` | read-only | none | Reads host `victim/conf.d/default.conf` |
| `write_config` | mutating | `content` | Replaces that host file; requires <20 KB and `proxy_pass` |
| `search_runbooks` | read-only | `query` | Reads `runbooks/*.md` and, when available, `logs/rag-index.npz`; opens the runbook gate |
| `search_incident_knowledge` | read-only | `query`, optional `scenario=disk_full|crashloop|dns` | Reads the offline SQLite knowledge pack; does not open the runbook gate |
| `disk_usage` | read-only | none | Host `df` and Docker disk-usage metadata |
| `service_status` | read-only | `unit` | Host systemd active/status output; unit name is regex-limited |
| `journal_tail` | read-only | `unit`, optional `lines=30` (clamped 1..200) | Host journal for one validated unit |
| `port_check` | read-only | `port`, optional `host=127.0.0.1` | TCP connect to loopback/private, excluding link-local/public targets |
| `http_check` | read-only | `url` | HTTP(S) GET to loopback/private, first 600 response bytes |
| `top_processes` | read-only | none | Host process list sorted by CPU |
| `recreate_stack` | mutating; destructive with `replace=true` | optional `name`, optional `replace=false` | Recreates allowed containers from `faults.SPECS`; replace may `docker rm -f` only the named container |
| `network_repair` | mutating | none | Creates `edge-net` if absent and attaches both victim containers |
| `cleanup_disk` | destructive | none | Deletes only old, unopened project `logs/*.log`, `logs/*.out`, and `/tmp/edge-agent-*`; not agent traces, images, or volumes |
| `kill_process` | destructive | `pid`, optional `force=false` | Signals only same-UID, non-platform processes; SIGTERM or SIGKILL |
| `finish` | control/no host mutation | `status`, `root_cause`, `actions` | Ends only the in-repo loop; omitted from the ops API |
| `self_heal` | mutating orchestrator | optional `note` | Ops-API-only subprocess running the full host agent loop and the same whitelist |

All seven state-changing tools dispatched by `agent.run_tool()` are runbook-gated. `self_heal` is an outer orchestrator whose internal state-changing calls pass through that gate. `recreate_stack replace=true`, `cleanup_disk`, and `kill_process` additionally require reviewer approval; dry-run paths do not mutate (`scripts/agent.py:39-54,348-359,463-554`).

## 6. Test and fault harness

`scripts/faults.py` owns both the mock stack definition and nine `CASES` ground-truth records (`min_actions`, acceptable `fix_tools`, final-report cause regex, and current `tier="seen"`; lines 35-50):

| Case | Injection | Ground-truth repair |
|---|---|---|
| `nginx-stopped` | stop front container | one start/restart |
| `bad-config` | remove nginx semicolon, restart fails | write config + restart |
| `upstream-down` | stop upstream | one start/restart |
| `bad-upstream-name` | proxy to nonexistent backend name | write config + restart |
| `container-removed` | remove front container | one `recreate_stack` |
| `crashloop` | bad config plus restart-always | write config + restart |
| `upstream-paused` | pause upstream | one restart |
| `upstream-detached` | disconnect upstream from `edge-net` | one `network_repair` |
| `upstream-500` | rewrite upstream's internal nginx config to return 500 | one replacement `recreate_stack` (after the enforced restart precondition) |

`setup`, `reset`, `inject`, `verify`, and `run` are command branches; `run` performs reset -> inject -> `agent.py` -> 200/200 verification and exits nonzero on failure (`scripts/faults.py:168-186`).

- `scripts/drill-all.sh`: detached, sequential NemoClaw drills with a 900-second timeout each; writes `logs/drills-<timestamp>.log` ending in `ALL_DONE`.
- `scripts/nemoclaw-drill.sh`: resets/injects one case, truncates the ops call log, gives a single unassisted alert to the sandbox agent, verifies 200/200, and prints the calls made (`scripts/nemoclaw-drill.sh:1-19`).
- `scripts/score.py`: parses those logs and optional shim usage rows; reports outcome class, minimal-action efficiency, evidence-first, closure, cause naming, false claims, irrelevant/repeated calls, gate/whitelist/reviewer events, destructive actions, latency/calls/tokens, per case and tier. Markdown goes to stdout; `--json` writes records (`scripts/score.py:1-23,176-208,232-247`).
- `scripts/test-retrieval-gate.py`: assertion-based self-check for cold-action refusal, bounded third-attempt release, read-only bypass, KB compactness/non-authority, symptom extraction, log folding/tail retention, reviewer ordering, fixed state-runbook mapping, and green-health reset (`scripts/test-retrieval-gate.py:15-94`).
- `scripts/kb-eval.py`: prepares label-free compressed telemetry at 1x/2x/4x/8x/16x/32x, runs telemetry-only and knowledge-augmented diagnoses, samples GPU power, and reports strict/lenient/scenario/service accuracy, tokens, latency, energy, retrieval match quality, and breakdowns. Outputs are `logs/kb-eval/`, `docs/kb-eval*.md`, and `docs/kb-eval/*` (`scripts/kb-eval.py:1-17,459-583`).
- `scripts/smoke.py`: waits for `/health`, checks a plain `pong`, then asserts a parsed `restart_service` tool call; prints `SMOKE OK` (`scripts/smoke.py:17-65`).
- `scripts/probe.py`: eight exact-answer needles in 250 synthetic near-duplicate runbooks, with thinking off/on; prints each hit and total score (`scripts/probe.py:1-47`).
- `scripts/bench.py`: warm-up, single-stream decode, `CONC` aggregate decode, cold and prefix-hit long prefill, long-context decode, thinking decode, and a sample answer; stdout only (`scripts/bench.py:1-53`).

## 7. Retrieval and knowledge

`scripts/rag.py` reads complete Markdown runbooks, chunks them at line boundaries around 450 characters, and returns whole runbooks rather than partial procedures. Dense retrieval is default: BAAI `bge-small-en-v1.5`, 384-dimensional CLS embeddings on CPU, index at `logs/rag-index.npz`. Keyword is a title-weighted term-overlap fallback when the index/dependencies are absent; hybrid min-max normalizes and combines 0.6 keyword + 0.4 dense (`scripts/rag.py:1-13,24-29,33-54,59-68,97-153`). Its embedded evaluation measures recall@1 across keyword/dense/hybrid.

The 11 runbooks are: `container-crashloop.md`, `container-missing.md`, `disk-full.md`, `nginx-config-error.md`, `oom-killed.md`, `upstream-502.md`, `upstream-504.md`, `upstream-5xx.md`, `upstream-detached.md`, `upstream-name-resolution.md`, and `web-service-down.md`.

`kb/` is `blackout-ops-kb` 0.3.0: Python 3.10+ standard library plus SQLite FTS5, with historical cases, remediation cards, topology documents, telemetry compression, evaluation, and a read-only `KnowledgeBase.retrieve()` API (`kb/README.md:1-13,81-99`; `kb/src/blackout_ops_kb/api.py:28-96`). Current Git tree metadata shows 28 tracked files under `kb/artifacts/` totaling 9.27 MiB: SQLite, case train/dev/eval splits, eval ground truth, manifests, RAG JSONL, solutions, topologies, and validation samples. Three packaged duplicates under `kb/src/blackout_ops_kb/data/` total 5.78 MiB. Thus the tracked knowledge payload is about 15.05 MiB; rebuild outputs/vendor data are ignored (`.gitignore:8-15`). Artifact contents were not opened for this audit.

`search_incident_knowledge()` loads `kb/artifacts/blackout_ops_kb.sqlite`, requests three cases/cards, preserves the pack's `matched`/`insufficient` verdict, and compacts output to <=2,500 characters: coverage/status, up to two cards' symptoms/checks/fixes/verify, and deduplicated case titles (`scripts/agent.py:256-298`). It is read-only hypothesis support and never satisfies the local runbook gate.

## 8. Reusable components for the new baseline

- Stable OpenAI client seam: set `VLLM_URL` and keep served model `edge-agent`; no agent logic depends on an engine-specific SDK (`scripts/agent.py:178-180,564-581`; `scripts/reviewer.py:19-23,145-157`).
- Minimal agent loop: reuse `agent.main()`, `TOOLS`, `run_tool()`, retrieval gate, and final 200/200 verification unchanged; point only `VLLM_URL` at the gateway (`scripts/agent.py:191-213,301-310,558-611`).
- Reviewer: reuse deterministic `computed_facts()`/`sufficient()` and its second OpenAI call. It already supports any OpenAI-compatible backend selected by environment (`scripts/reviewer.py:64-132`).
- Tool-call compatibility: reuse `salvage_calls()`, `json_text_calls()`, `stream_with_salvage()`, and `salvage_json()` as a gateway middleware or TRT-only adapter (`scripts/oai_shim.py:63-211`).
- Backend smoke contract: reuse `scripts/smoke.py` for `/health`, plain chat, and parsed tool calls; parameterize only `VLLM_URL`/`SERVED_NAME` (`scripts/smoke.py:12-14,17-65`).
- Tool-call/model probes: reuse `probe.py` for similar-context precision and the exact OpenAI tool request in `smoke.py:42-64`.
- Serving benchmark: reuse `bench.py` for decode, concurrency, long prefill/prefix reuse, context, and thinking; `scripts/try.sh` already wraps an arbitrary vLLM model and flags for temporary comparison (`scripts/bench.py:27-53`; `scripts/try.sh:8-29`).
- Fault isolation: reuse `faults.SPECS`, `CASES`, `reset/inject/verify`, and the victim containers. They are backend-independent (`scripts/faults.py:35-50,71-165`).
- Comparable scoring: reuse `score_block()`/`scorecard()` and the `CASES` ground truth so gateway/backend runs retain the existing operational metrics (`scripts/score.py:65-126,176-208`).
- Ops surfaces and NemoClaw assets are inference-independent if the gateway continues to expose the current OpenAI contract and model ID.

## 9. Components to replace or wrap

The gateway should become the stable client-facing endpoint currently occupied by the shim at `:8001/v1`: NemoClaw's provider, `ops_api.self_heal`, and explicit `VLLM_URL` users should point there. To preserve the current demo, move the shim behind the gateway as the TensorRT-specific adapter, then route:

```text
OpenAI clients / NemoClaw -> gateway -> TRT route -> oai_shim -> trtllm-serve :8000
                                  `-> vLLM route ----------------> vLLM backend
```

For the TensorRT route, preserve developer-role/content normalization, dropped unsupported fields, explicit thinking injection, malformed tool-call salvage, incremental SSE forwarding, finish-reason repair, and usage rows. These are observable compatibility behaviors, not only transport (`scripts/oai_shim.py:25-60,63-211`).

For vLLM, the current native flags `--enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3` make TensorRT's strict input-shape workarounds and parser compensation unnecessary **if** the new model/parser combination accepts the same requests and emits valid tool calls (`scripts/serve.sh:24-33`). Do not apply TRT-only request rewriting blindly to vLLM. Salvage is still reusable as a gateway output guard, but whether Bolt needs it is **not found** and must be measured. Native parser/flag choices for Bolt are **not found**.

Direct defaults currently bypass the shim (`agent.py`, `reviewer.py`, and `kb-eval.py` default to `:8000`), while NemoClaw and `ops_api.self_heal` use `:8001`. Gateway adoption should unify these through deployment environment variables; changing the agent/tool code is not required.

## 10. Components not to touch

- `scripts/serve-trt.sh`, `trt/nano.yaml`, current image/model pins, and current `:8000` route: they are the working TensorRT baseline and source of published comparisons.
- `scripts/oai_shim.py` behavior and `:8001/v1` client contract until the gateway reproduces it: NemoClaw depends on its input normalization, thinking enablement, streaming behavior, and tool salvage.
- `scripts/agent.py`, `scripts/reviewer.py`, `scripts/ops_api.py`, tool names/schemas, and `/sandbox/bin/ops`: changing them would mix agent/tool changes into an inference-only comparison and can break drills.
- `skills/ops/SKILL.md`, `nemoclaw/workspace/*.md`, and `nemoclaw/openclaw-patch.json5`: these define the demonstrated direct MCP surface, 32k context, output budget, and reporting behavior.
- `scripts/faults.py` `CASES`/`SPECS`, `scripts/nemoclaw-drill.sh`, `scripts/drill-all.sh`, and `scripts/score.py`: ground truth, injection semantics, logs, and scoring must remain identical for comparable baseline results.
- `runbooks/*.md`, `scripts/rag.py` evaluation queries/weights, and `logs/rag-index.npz`: edits change retrieval behavior and invalidate retrieval-gate/drill comparisons.
- `scripts/smoke.py`, `probe.py`, and `bench.py` while comparing engines: use identical requests; add a separate gateway-specific test only if needed.
- `docs/bench-2026-09-22.md`, `docs/serving-metrics-2026-09-22.md`, `docs/scorecard.md`, and `docs/kb-eval*`: these are measured historical results, not live configuration.
- `logs/`, `datasets/`, and `kb/artifacts/`: they contain audit trails, raw/evaluation data, and the tracked knowledge/eval split. Editing them would invalidate evidence or risk large-data churn. `victim/conf.d/default.conf` is generated and drill-owned, not source configuration.
- Do not enable either checked-in systemd unit as part of the baseline addition; no-autostart is an explicit current operational choice.

## 11. Repo conventions

- Root package: `pyproject.toml` requires Python >=3.11 and declares OpenAI, vLLM, lm-eval, and tenacity; `.python-version` selects 3.12 and uv is configured to use managed Python because Triton JIT needs headers (`pyproject.toml:1-14`, `.python-version:1`). `uv.lock` is committed and supplies exact transitive pins.
- Normal execution uses `uv run --no-sync scripts/<name>` after initial `uv sync`; server scripts that need an activated PATH source `.venv/bin/activate` (`README.md:98-104`, `scripts/serve.sh:7-14`).
- Operational entry points live in `scripts/`; Python filenames use underscores and shell orchestration often uses hyphens. Shell scripts generally `cd` to the repository root and use environment variables rather than config generators.
- Logs/results live under ignored `logs/`: inference nohup/container logs, shim/ops/reviewer JSONL, agent traces, drill logs, RAG index, benchmarks, and KB evaluation (`README.md:194`). Generated `victim/conf.d/default.conf` is also ignored.
- `.gitignore` excludes `.venv`, logs, Python cache, `.env`, generated victim config, raw datasets, KB builds/dist/egg-info, and vendored integration data; KB artifacts and packaged data are intentionally tracked (`.gitignore:1-15`).
- Repository copies are private GitHub `Vanisherzd/dgx-spark-edge-agent`, Spark `hsnl@192.168.2.60:~/edge-agent`, and Mac `~/Desktop/HSNL/edge-agent`. The documented constrained-network flow pushes from Spark to GitHub using a Mac-provided `gh` token over stdin (`README.md:374-378`). An automated or bidirectional Mac/Spark sync mechanism is **not found**.
- Root operational tests are executable self-checks and assertion-based `__main__` programs (`score.py --selfcheck`, `test-retrieval-gate.py`, smoke/probe/bench), not pytest. No root pytest suite/config is found. The separate `kb/tests` package uses `unittest` (`kb/README.md:171-179`).

## 12. Open questions

- Gateway code, image, version, route configuration, listen port, auth policy, health schema, and observability format: **not found**.
- Bolt launch configuration (vLLM image vs current bare metal, parser names, context limit, memory utilization, quantization/runtime flags, and served model alias): **not found**.
- Whether Bolt and the current TensorRT model can be resident concurrently on the DGX Spark: **not found**; the current Qwen vLLM and TensorRT models are documented as mutually exclusive.
- Whether Bolt emits native tool calls/reasoning correctly under a supported vLLM parser, and whether shim salvage is needed: **not found**.
- Which route-selection contract is intended (header, model name, path, or static default) and whether `edge-agent` remains the shared alias: **not found**.
- Whether the gateway must reproduce `logs/oai-shim.log` usage rows exactly so `score.py --shim` remains comparable: **not found**.
- Whether the gateway may take `:8001` and move the shim internally, or must use a new external port: **not found**.
- A current command for Mac-to-Spark code synchronization (apart from Git/GitHub and the documented Spark-side push): **not found**.
