# Self-healing agent, first cut (2026-09-22)

Goal: given an injected fault, the agent must diagnose and repair it on its own using only the local model
(TensorRT-LLM on :8355, alias `edge-agent`), then verify. Later fault-injection cases plug into the same harness.

## Loop (`scripts/agent.py`)
observe (health URLs, container states) -> LLM with tools -> act -> observe ... -> `finish(status, root_cause, actions)`
-> independent verification (both health URLs 200). Thinking mode stays on (Nemotron reasons before each call);
temperature 0; at most 8 steps; every step (reasoning, tool, args, result, latency) is appended to `logs/agent/*.jsonl`.

## Tools = the whole action space
`check_health`, `docker_ps`, `docker_logs`, `docker_exec` (read-only diagnostics only, shell operators rejected),
`docker_start`, `docker_restart`, `read_config`, `write_config` (size-capped, must keep the proxy), `search_runbooks`
(keyword overlap over `runbooks/*.md`, the "small RAG"), `finish`. Everything is restricted to the two sandbox
containers and one config directory; `--dry-run` turns remediation into no-ops.

## Sandbox and faults (`scripts/faults.py`)
`edge-victim` (nginx front-end on 127.0.0.1:8880, config bind-mounted from `victim/conf.d`) and `edge-victim-app`
(upstream for `/api/`). Cases: `nginx-stopped` (container stopped), `bad-config` (missing semicolon, container exits on
start), `upstream-down` (502 on /api/). `faults.py run <case>` = reset -> inject -> agent -> verify -> PASS/FAIL.

## First results (2026-09-22, TensorRT-LLM + Nemotron-3-Nano NVFP4, thinking on)
| case | steps | what the agent did | result |
|---|---|---|---|
| nginx-stopped | 6 | docker_ps -> read_config -> docker_logs -> docker_start -> check_health -> nginx -t | PASS, ~55 s |
| bad-config | 7 | docker_ps -> read_config -> docker_start (fails) -> docker_logs (finds `[emerg]`) -> write_config (fixed semicolon) -> docker_restart -> check_health | PASS, ~70 s |
| upstream-down | 5 | docker_logs(app) -> read_config -> docker_ps -> docker_start(app) -> check_health | PASS, ~60 s |
All verdicts were `resolved` with a correct root cause; traces in `logs/agent/*.jsonl`. Run them again with
`uv run --no-sync scripts/faults.py run <case>`.

## Through NemoClaw (sandboxed OpenClaw agent) — `scripts/ops_api.py`
The sandbox has no docker and no host filesystem, so the host runs a small HTTP ops API (`uv run --no-sync
scripts/ops_api.py`, 0.0.0.0:8790) that exposes exactly the agent.py tool whitelist: `GET /tools` (schemas) and
`POST /call {"name","args"}`. Every call is appended to `logs/ops-api.log` (caller IP, tool, args, result). The
sandbox is allowed to reach it with one OpenShell rule:
```
openshell policy update edge-agent --add-endpoint host.openshell.internal:8790:read-write:rest \
  --add-allow host.openshell.internal:8790:GET:/tools --add-allow host.openshell.internal:8790:POST:/call \
  --binary /usr/bin/curl --binary /usr/bin/python3 --binary /usr/local/bin/node --rule-name ops-api --wait
```
Drill: `logs/nemoclaw-drill.sh <case>` = reset -> inject -> `nemoclaw edge-agent agent --agent main -m "<task>"` ->
verify -> print the ops-api call log. No auth on the ops API yet (docker-bridge reachability only); add a token before
exposing it further.

## NemoClaw drill findings (2026-09-22)
Driving the same fault through the NemoClaw sandbox agent (OpenClaw runtime, Nemotron via TensorRT-LLM) needed, in order:
1. the request-shape shim (`scripts/oai_shim.py`) — otherwise every follow-up turn is a 400;
2. a way to act on the host: `scripts/ops_api.py` (HTTP + MCP) plus the OpenShell endpoint rule; the model ignores
   "use curl" instructions and reaches for `docker`, so the tools must be real tools (MCP server `ops`) and/or a skill;
3. tolerance for small-model tool-calling slop, all handled server-side: missing `name`, `container` instead of `name`,
   arguments as JSON strings, and `write_config` content arriving with literal `\n` (double-escaped through OpenClaw's
   `tool_call` meta-tool) — that last one made a correct fix produce an unparsable one-line nginx.conf;
4. never let a tool bug drop the connection: OpenClaw pauses an MCP server after three `fetch failed`.
OpenClaw hides MCP tools behind `tool_search`/`tool_describe`/`tool_call` with long ids (`mcp:bundle-mcp:ops__docker_logs`);
Nemotron-3-Nano occasionally typos them. `self_heal` (one MCP call that runs the host loop) is the fallback when the
sandbox agent stalls. Run: `logs/nemoclaw-drill-mcp.sh bad-config` (drill script kept in `scripts/nemoclaw-drill.sh`).

## NemoClaw drill result (2026-09-22 15:41)
`logs/nemoclaw-drill-mcp.sh bad-config` -> PASS in 2.5 min; `upstream-down` -> PASS in 2 min (root cause: app container exited; action: docker_start); `nginx-stopped` -> PASS in 3 min (docker_start edge-victim). 3/3. The sandbox agent (NemoClaw, OpenClaw runtime, Nemotron via
TensorRT-LLM) called `ops__self_heal` over MCP; the host loop diagnosed the missing semicolon, rewrote the config,
restarted the container, verified 200/200; the agent reported root cause / actions / final health. Two routes exist:
- **self_heal** (one MCP call, reliable): NemoClaw is the interface and orchestrator, the same local model runs the
  step-by-step decision loop on the host. Use this for demos and for the hackathon judges' fault cases.
- **step-by-step in the sandbox** (skill `ops` + `/sandbox/bin/ops`, or the individual MCP tools): works when the model
  drives the tools correctly; Nemotron-3-Nano-30B often mangles OpenClaw's meta-tool protocol, so it is best-effort.

Prompt lesson: when the prompt said "use the tools / read the skill", Nemotron-3-Nano spent its turn guessing OpenClaw
meta-tool ids (`tool_describe ops`, `tool_describe healthcheck`) and gave up twice. When the prompt states the exact shell
command (`/sandbox/bin/ops call self_heal "{}"`) as step 1 and lists manual `ops call` commands as the fallback, it
executes it every time. `scripts/nemoclaw-drill.sh` carries that prompt.

## Tool surface tuning (2026-09-22 22:41) — the sandbox agent's tool calling was an OpenClaw config problem

The sandbox agent kept failing tool calls, so we read the evidence instead of the symptom: the in-sandbox session
trajectories (`/sandbox/.openclaw/agents/main/sessions/*.jsonl`), the gateway log, and `openclaw.json`.

What the evidence showed:

| Finding | Evidence |
| --- | --- |
| All 24 tools hidden behind `tool_search` / `tool_describe` / `tool_call` | gateway log: `tool-search: cataloged 24 tools behind compact prompt surface`; a drill turn spent 5 `tool_search` calls, then `tool_call {"id":"check_health"}` -> `Unknown tool id` |
| The ops MCP server filtered down to `self_heal` | `mcp.servers.ops.toolFilter.include: ["self_heal"]` — the other nine tools were reachable only as a shell string |
| System prompt 28,859 chars (~9.6k tokens) of mostly irrelevant instructions | 16 visible skills (meme-maker, notion, weather, taskflow, tmux...), a 7.2 KB `AGENTS.md` telling the agent to check email and the weather |
| A heartbeat model call every 30 minutes with nothing to do | four turns per two hours, each refreshing the MCP catalog; one of them burned 4 `tool_describe` calls on `taskflow-inbox-triage` |
| Model's thinking mode not enabled on the OpenClaw path | `agent.py` sends `chat_template_kwargs.enable_thinking`; OpenClaw has no way to send it |

Changes (`nemoclaw/openclaw-patch.json5`, applied with `openclaw config patch --stdin`):

- `tools.toolSearch: false`, `tools.profile: "minimal"`, `tools.alsoAllow: ["bundle-mcp", "exec", "read"]` — 19 direct
  tools in the request instead of three meta-tools over a hidden catalog.
- `openclaw mcp tools ops --clear` — every ops tool is a first-class tool again.
- `agents.defaults.skills: ["ops"]` — 16 visible skills to 1.
- `agents.defaults.experimental.localModelLean: true` — OpenClaw's own switch for small local backends.
- `agents.defaults.heartbeat.every: "0m"` plus a comments-only `HEARTBEAT.md`.
- `nemoclaw/workspace/AGENTS.md`: 7.2 KB of generic assistant advice replaced by 1.5 KB of stack facts and procedure.
- `scripts/oai_shim.py` enables thinking for every request and drops `reasoning_effort`, which TensorRT-LLM rejects.
- `scripts/agent.py` gained six read-only host diagnostics (`disk_usage`, `service_status`, `journal_tail`,
  `port_check`, `http_check`, `top_processes`) so fault cases outside the two containers have tools; `ops_api.py`
  re-exports them, so the host loop, the HTTP API and MCP all gained them at once. `http_check` and `port_check`
  resolve the target and refuse anything outside loopback and the private ranges.

Result — the drill prompt no longer names a command, it is what an on-call human would get
("Alert: the web stack is degraded. Diagnose it and repair it with your ops tools, then verify both health URLs
return 200. Report root cause, actions taken, and final health."):

| Case | Before (prompt naming the exact shell command, via `self_heal`) | After (plain alert, agent drives the tools) |
| --- | --- | --- |
| bad-config | PASS, 2.5 min | PASS, 75 s, 8 tool calls |
| nginx-stopped | PASS, 3 min | PASS, 38 s, 6 tool calls |
| upstream-down | PASS, 2 min | PASS, 47 s, 5 tool calls |

System prompt 28,859 -> 16,904 chars. No `tool_search` call, no invented tool id, no `self_heal` fallback in any of
the three runs. On bad-config the agent read the logs, read the config, rewrote it, restarted the container, and then
verified with `http_check` on both URLs by itself.

### Broken thought: tool calls that land in the thinking channel

One drill turn returned `Agent couldn't generate a response` with the fault unrepaired. The session transcript shows
the model wrote the chat template's own tool-call syntax **inside** its thinking block and never closed `<think>`:

```
thinking: We see edge-victim exited. Need to investigate why it exited. Check logs.
<tool_call> <function=ops__docker_logs> <parameter=name> edge-victim </parameter> ... </tool_call>
```

TensorRT-LLM's `nano-v3` reasoning parser files that whole block as `reasoning_content`, so the `qwen3_coder` tool
parser never sees it and the turn arrives empty (`stopReason=stop`, `payloads=0`). The parsers are correctly matched —
the Nemotron chat template does use the `<tool_call><function=…><parameter=…>` form — this is the model leaving a
thought open, which the template's own comments call out ("allow downstream logic to take care of broken thought").

`scripts/oai_shim.py` is that downstream logic: it buffers the SSE stream, replays it byte for byte when the turn is
normal, and only synthesizes `tool_calls` when the turn is empty and the reasoning channel holds a parseable call.
It has a four-case self-check in the commit. **It did not fire in the three passing drills** (0 salvage events in
`logs/oai-shim.log`), so the failure is intermittent, not systematic: treat the shim recovery as insurance, not as the
reason the drills now pass.

## Stability and the written material (2026-09-22 23:33)

Three drills, run twice, plus one fault the agent had never seen. **7/7 PASS**, no `tool_search`, no `self_heal`
fallback, no invented tool id.

| Case | Run 1 | Run 2 | Tool calls |
| --- | --- | --- | --- |
| bad-config | PASS 75 s | PASS 55 s | 8 |
| nginx-stopped | PASS 38 s | PASS 50 s | 6, 7 |
| upstream-down | PASS 47 s | PASS 62 s | 5, 7 |
| bad-upstream-name (new) | PASS 70 s | – | 9 |

In all six runs of the drilled cases the agent never opened a runbook: `check_health` -> `docker_ps` -> `docker_logs`
is enough to solve them from the evidence. That is the useful measurement about written material — it earns its place
only on a fault the model cannot reason out. So we injected one it had never seen (`bad-upstream-name`: the
`proxy_pass` target renamed to a host that does not exist). The agent read the logs, read the config, and then called
`search_runbooks {"query": "host not found in upstream"}`, which returned `upstream-name-resolution.md`, and applied
it. The retrieval path is load-bearing exactly where it was designed to be.

### Skills architecture: one visible skill, many retrieved runbooks

OpenClaw skills are injected into the system prompt; runbooks are fetched by a tool. For a 30B model with 3B active
parameters that difference decides the design:

- **One visible skill** (`ops`). Sixteen visible skills is what broke tool calling in the first place. Every extra
  skill costs prompt tokens and offers the model another wrong thing to do.
- **Everything else is a runbook**, retrieved on the symptom. Adding one costs nothing at rest.

`skills/ops/SKILL.md` was stale (10 tools, and it taught the shell fallback as the primary path). Rewritten: all 16
tools under the names the model sees, diagnosis separated from action, and an explicit stop rule — if the same action
fails twice, look for another cause; if 200/200 is unreachable, escalate with the exit code and the log lines rather
than looping. An honest escalation is a valid outcome for an unattended agent; a silent loop is not.

`search_runbooks` scored plain body-word overlap, which hands the longest document the win as the set grows. Titles
now count triple, a shorter body breaks ties, and a query that matches nothing returns the catalogue so the agent
learns what exists. Checked against eight symptom phrasings, each retrieving its own runbook.

Runbooks now cover: service down, nginx config error, upstream 502 (refused), upstream 504 (timeout), upstream name
resolution, container crash loop, OOM kill (exit 137), disk full.

### What is still missing for unattended offline operation

1. **Host-level actions.** The agent gained six read-only host diagnostics, but it can only act inside the two
   containers. A stopped docker daemon, a full disk, or a runaway process can be diagnosed and reported, not fixed.
   A host executor needs an explicit unit and path whitelist, and that is a blast-radius decision to take deliberately.
2. **No memory across incidents.** Every run starts blank; a fault seen yesterday teaches it nothing today.
3. **Keyword retrieval.** Fine for eight runbooks. Around fifty it needs embeddings (the Phase 2 RAG item).
4. **Fault coverage.** Four injectable cases against eight runbooks; the untested ones are written from the tool
   surface, not from an observed failure.

## Host layer (2026-09-23)

Four actions that reach past the two containers but stay inside what this project created:

| Tool | What it fixes |
| --- | --- |
| `recreate_stack` | a container that was removed, not stopped — `docker_start` returns `No such container` and no amount of restarting helps |
| `network_repair` | the stack network missing, or a container detached from it |
| `cleanup_disk` | this project's own old logs, by glob, skipping any file a running process still holds open |
| `kill_process` | a runaway process owned by this user that is not part of the platform |

The whitelist is defined by what is absent as much as by what is present:

- **The docker daemon is not in it.** The inference server runs as `docker run --rm`, so bouncing dockerd deletes the
  model the agent thinks with. A dead daemon is unrecoverable from inside this agent by construction; it reports and
  escalates. (`docker inspect trtllm-edge --format '{{.HostConfig.AutoRemove}}'` -> `true`.)
- **No system unit.** `sudo` on this host needs a password, and no password belongs in a repo. `systemctl --user` has
  nothing of ours loaded.
- **No images or volumes.** They are shared with the rest of the lab. The agent reports disk pressure and a human
  decides what goes.

`cleanup_disk` learned two rules the hard way. It must not delete `logs/agent/*.jsonl`, which is the incident audit
trail — an ops agent does not delete the record of what it did. And it must skip files a live process holds open: the
first version unlinked `logs/trt-serve.log` while the inference server had it open, which frees nothing and sends
every later line to an inode nobody can read. (Nothing was lost: `docker logs trtllm-edge` carries the same output.)

### An absent container was invisible

The first `container-removed` drill passed in 190 s and 15 tool calls, six of them re-reading the same `docker_ps`
output. `docker ps -a` simply omits a container that no longer exists, so its absence looked the same as not having
spotted it, next to a sibling that was healthy. `docker_ps` now lists every container the stack expects and marks the
missing ones, and `container-missing.md` states that start and restart cannot help here. The same drill then took
**39 s and 4 calls**: `check_health`, `docker_ps`, `recreate_stack`, `check_health`.

## Retrieval: measured, not assumed (2026-09-23)

`scripts/rag.py` replaces the inline keyword scorer. Chunks are scored, whole runbooks are returned — an agent handed
half a procedure will act on half a procedure. Embeddings come from `BAAI/bge-small-en-v1.5` (33M params, 384 dims)
running on CPU through `transformers`, deliberately off the GPU the inference server owns, with `HF_HUB_OFFLINE=1`
forced so retrieval never waits on a network this box may not have. Build: 19 chunks from 9 runbooks in 12 s. First
query in a process pays ~2.5 s to load the model, then milliseconds.

Recall@1 over 23 symptom queries, five of which deliberately share no vocabulary with their runbook:

| Scorer | 8 runbooks / 20 queries | 9 runbooks / 23 queries |
| --- | --- | --- |
| keyword (title-weighted overlap) | 15/20 | 16/23 |
| **dense (bge-small, cosine)** | **20/20** | **23/23** |
| hybrid 0.6 keyword + 0.4 dense | 18/20 | 20/23 |

The premise going in was that pasted error strings would favour lexical matching and that a hybrid would win. It did
not survive the measurement: dense took every query including the literal ones (`no space left on device`,
`exited 137 killed`, `host not found in upstream`). A weight sweep confirmed there was nothing to tune — keyword 0.1
ties at 20/20, every other weight and both rank fusions score lower:

| fusion | recall@1 /20 |
| --- | --- |
| dense only | 20 |
| weighted, keyword 0.1 | 20 |
| weighted, keyword 0.2 – 0.5 | 19 |
| weighted, keyword 0.6 | 18 |
| reciprocal rank fusion (k=10, k=60) | 18 |
| max fusion | 17 |

So dense is the default, keyword stays as the no-index fallback and as the baseline, and `RAG_MODE` switches modes.
Caveat worth keeping in view: 23 queries written by the same person who wrote the runbooks is a strong signal, not a
benchmark. The eval set lives in `scripts/rag.py` and should grow with every fault case.

## Five-case verification (2026-09-23 00:40)

All five injectable faults, with `bad-config` run three times because it was the one that failed the round before.
**7/7 PASS, no empty turns.**

| Case | Result | Seconds | Calls | Path |
| --- | --- | --- | --- | --- |
| bad-config | PASS | 67 | 7 | logs -> read -> write -> restart |
| bad-config | PASS | 387 | 10 | went astray, rescued by `self_heal` (see below) |
| bad-config | PASS | 105 | 7 | logs -> read -> write -> restart |
| nginx-stopped | PASS | 90 | 6 | restart |
| upstream-down | PASS | 44 | 5 | start the upstream |
| bad-upstream-name | PASS | 62 | 9 | read -> network_repair -> write -> restart -> verified both URLs |
| container-removed | PASS | 82 | 9 | `recreate_stack` -> verified both URLs |

The round before this one found a real defect: one `bad-config` run returned `stopReason=length` with nothing
emitted. The model spent all 4096 output tokens arguing with itself about a missing semicolon and was cut off before
producing a tool call. Onboarding's model defaults were wrong on two axes — `maxTokens` 4096 is not enough for a
reasoning model driving a multi-step repair, and `contextWindow` claimed 131072 while `trtllm-serve` runs with
`max_seq_len 32768`, so OpenClaw would have let a session grow past what the server accepts instead of compacting.
Raised to 12288 and 32768. Three consecutive `bad-config` runs then passed.

### The honest caveat

The 387 s run passed, but not on its own reasoning. The agent read the `[emerg]` line, then wandered — `disk_usage`,
`recreate_stack` on a container that plainly existed, a restart without changing the config — and only recovered when
it called `ops__self_heal`, which runs the same loop on the host and fixed the file. It never called
`search_runbooks`, which would have returned `nginx-config-error.md` and the exact procedure.

So the fallback is doing real work, not sitting decorative: roughly one bad-config run in three needs it. Counting
that as a clean pass would be dishonest. The gap is that the model skips retrieval when it believes it already knows
the answer, and no prompt line has fixed that so far. Worth measuring again as the runbook set grows.

## Runbook-first, enforced (2026-09-27)

The ask was "make it always consult the runbook before acting". A prompt cannot deliver "always": across seven drills
the model skipped retrieval whenever it believed it already knew, and one run wandered for six minutes because of it.
So the rule moved into the tool layer, where it is a property rather than a request.

- **Push.** `docker_logs` attaches the best-matching runbook to its output when the log carries a complaint
  (`[emerg]`, `refused`, `exit 137`, `no such`...). `docker_ps` attaches `container-missing.md` when a container is
  absent. Only on a real symptom: cosine similarity is positive for anything, and stapling an irrelevant procedure to
  healthy output would teach the agent to skip the section.
- **Gate.** The seven mutating tools (`docker_start`, `docker_restart`, `write_config`, `recreate_stack`,
  `network_repair`, `cleanup_disk`, `kill_process`) refuse while no runbook has been seen for this incident. The
  refusal does not guess a procedure from the tool name (that retrieved the wrong one in testing); it lists the
  catalogue and sends the agent to `docker_logs` or `search_runbooks`. Bounded at two refusals per incident so a
  stubborn model cannot deadlock. An incident ends when `check_health` comes back 200/200.
- `scripts/test-retrieval-gate.py` asserts all of it with dry runs.

Five cases, one run each, with the gate live:

| Case | Result | Seconds | Calls | How the runbook arrived |
| --- | --- | --- | --- | --- |
| bad-config | PASS | 91 | 8 | `docker_start` refused -> `docker_logs` attached `nginx-config-error` -> write, restart |
| nginx-stopped | PASS | 111 | 9 | clean stop, no symptom in logs -> `network_repair` refused -> `search_runbooks` -> start |
| upstream-down | PASS | 72 | 8 | `docker_start` refused -> `search_runbooks` -> start |
| bad-upstream-name | PASS | 39 | 7 | `docker_logs` attached `upstream-name-resolution` -> write, restart |
| container-removed | PASS | 26 | 4 | `docker_ps` attached `container-missing` -> `recreate_stack` |

5/5, no `self_heal`, no empty turn. The invariant held in every run: **no mutating tool executed before a runbook
had been seen.** The gate fired three times and each time the agent went and got the procedure instead of arguing.
Cost is visible on the two clean-stop cases (nginx-stopped, upstream-down), where a stopped container has no
complaint in its log to match on, so the agent pays one refusal and one search: 20-40 s. The cases with a real error
line got faster, because the procedure arrived with the evidence.

### Two more things the same pass fixed

**Streaming.** The salvage logic added earlier buffered the whole SSE stream to inspect it. OpenClaw aborts a model
request after 120 s without a chunk (`agents.defaults.timeoutSeconds` is capped at 120 s for the idle window), and
raising `maxTokens` to 12288 made long thinking phases hit it: `response truncated: chunk idle timeout exceeded`. The
shim now forwards every chunk the moment it lands (time to first byte 6 ms on a 3.3 s response) and holds back only
the finish chunk, where it can still inject a recovered tool call.

**Prompt.** `SOUL.md`, `IDENTITY.md`, `USER.md` were OpenClaw's fill-in-your-personality templates, 3.3 KB about
vibes and emoji. Three lines each now. System prompt 28,859 -> 16,904 -> **14,200 chars**. What remains is
OpenClaw's own base prompt (~8 KB, fixed) and NemoClaw's generated `POLICY.md` (4.4 KB, regenerated on every policy
change, so not ours to trim).

## What is left to optimise, measured where it was cheap

| Item | Expected gain | Cost / risk | Status |
| --- | --- | --- | --- |
| Speculative decoding on TensorRT-LLM (`MTPDecodingConfig`, `EagleDecodingConfig`, `DraftTargetDecodingConfig` exist in 1.3.0rc13; NGram already crashed) | decode 1.5-2x, drills ~30 % shorter | needs an MTP/Eagle head or draft model that fits Nemotron-3-Nano — unknown; server restart to test | untested |
| KV block reuse for the fixed 14 KB system prompt | ~1 s prefill saved per turn, 5-8 s per drill | measured 18.65 s first-prompt penalty earlier; worth a retest as warm-up only | untested |
| `mcp.servers.ops.supportsParallelToolCalls: true` | fewer turns when the model batches `check_health` + `docker_ps` | the model has not batched once in 19 drills; hint only | not worth it yet |
| Incident memory: append "seen <date>, fixed by <tools>" to the matched runbook after a PASS | the second occurrence of a fault starts with the answer | small; changes runbooks automatically | not built |
| Disk: 47.8 GB docker build cache, 52.9 GB unused images, 11 GB partial `Qwen3.6-35B-A3B-NVFP4` download | 110 GB free -> ~220 GB | deleting shared lab data is the operator's call, never the agent's | waiting on a decision |
| Watchdog for `oai_shim.py` / `ops_api.py` | survives a crash during a demo | a background supervisor, which is adjacent to the no-autostart rule | ask first |

## Four more fault cases (2026-09-27)

Each injection was probed by hand before it became a case, because the harness must be able to undo it whatever
order the drills run in. `faults.py reset` now unpauses, resets the restart policy, reconnects the network, and
rebuilds the upstream when `/api/` still fails after a restart.

| Case | What it teaches | First run | After the fixes below |
| --- | --- | --- | --- |
| `crashloop` (`restart=always` + rejected config) | restarting is the wrong move; only the config fix ends the loop | PASS 234 s, 13 calls | PASS 256 s, 10 calls |
| `upstream-paused` (`docker pause`) | 504 is not 502: alive but frozen; `docker restart` does unpause | PASS 35 s, 4 calls | – |
| `upstream-detached` (`network disconnect`) | both Up, nothing in either log but a connect timeout on the front end | fixed, then died with `stopReason=length` | PASS 201 s, 16 calls |
| `upstream-500` (config written inside the upstream's own layer) | `docker restart` keeps the writable layer; only a rebuild clears it | **FAIL**, `stopReason=length` | PASS 41 s, 6 calls |

Two failure shapes came out of the first run, both fixed in the tool layer rather than the prompt:

**Acted, and could not tell.** On `upstream-detached` the agent ran `network_repair`, which repaired the stack, never
called `check_health` again, and kept probing for five minutes until it exhausted its output budget. Every mutating
tool now appends the health check to its own result — `health now: {...} -> both URLs healthy. The incident is
resolved: report root cause, actions, final health and stop.` or `-> still failing; keep going.` — and `write_config`
says plainly that nothing is applied until the restart. The re-run stopped right after the repair.

**A symptom the retriever could not read.** On `upstream-500` the only evidence was nginx access-log lines ending in
`500`. As embedding input those are quotes, byte counts and user agents; they retrieved `upstream-504` and
`upstream-name-resolution`, never `upstream-5xx`, and the agent looped on `docker_logs` until it ran out of tokens.
`symptom_query` now rewrites an access-log 5xx into "the service itself answers HTTP 500 to requests, container is
up", and the runbook quotes the log line it will be matched against. From FAIL to a six-call pass.

Retrieval on the grown set: 11 runbooks, 29 queries — dense **27/29**, hybrid 23/29, keyword 19/29. The two misses are
three-word paraphrases ("container is paused", "the backend fell off the docker network") where the correct runbook
sits second or third; the queries the agent actually issues — the `docker_ps` markers and the log lines — all hit.
One near-tie matters: the detached log line retrieves `upstream-504` at 0.829 and `upstream-detached` at 0.827, so
the 504 runbook gained a step 6 that hands over to `network_repair` when both containers are Up and a restart did not
help. That is the step the re-run followed.

`recreate_stack` gained `replace: true` for the upstream-500 shape, and it demands an explicit container name: it
destroys that container and builds it again from `faults.SPECS`. Both containers are stateless by definition, the
front end's config is a host file, so nothing is lost — but a tool that deletes something should never do it to a
default.

Also learned the hard way: a drill loop started over ssh kept running on the Spark after the session dropped, and
the laptop-side log silently lost the last two results. `scripts/drill-all.sh` runs drills detached on the Spark and
writes to `logs/drills-<timestamp>.log`; watch the file, not the ssh session.

What is still slow: `crashloop` and `upstream-detached` take 3-4 minutes because the model explores (`disk_usage`,
`docker_exec` probes, a no-op `recreate_stack`) before it reads the one log line that names the fault. The gate and
the pushes guarantee it cannot act blind; they do not stop it from looking at irrelevant things first.

## Regression, and a defect that was ours all along (2026-09-27, afternoon)

Nine cases, one pass each, after the four new cases landed: 7/9. The two failures were `crashloop` (`stopReason=length`
after 290 s) and `upstream-detached`, which ended **worse than it started** — both URLs dark instead of one. Reading
the model's own reasoning, not the verdicts, found three defects, all in our tooling rather than in the model.

**The log truncation dropped the only error line.** `sh()` joined stdout and stderr and kept the first 4000
characters. A crash-looping nginx prints ten lines of entrypoint chatter per restart on stdout and its single
`[emerg]` on stderr, so a hundred-line tail overflowed the limit before stderr began. The model's reasoning was
exactly right about what it saw — "the logs show no actual error, just the read-only note" — and it went on to
probe everything else until it ran out of output tokens. `bad-config` is the same broken file, but the container
exits once, the log fits, and it passed every time. `crashloop` failed 0/5 across the day. `sh()` now keeps the tail,
`docker_logs` merges stderr in emission order and folds repeated lines into one with a count (`(x6)`), so the
recurring error is the loudest line rather than the missing one. `crashloop` then passed 3/3: 118 s, 123 s, 74 s,
all on the canonical path (logs, read config, write config, restart).

**Restarting the front end while the upstream is off the network kills the front end.** nginx refuses to start when
an upstream name does not resolve. With `edge-victim-app` disconnected, the agent restarted `edge-victim`, which
exited, and neither runbook it had said not to. `docker_start`/`docker_restart` on the front end now refuse while
the upstream is not on the stack network and say to run `network_repair` first. `upstream-detached` then passed 2/2
(54 s / 6 calls, 139 s / 9 calls); the refused restart is visible in the trace, followed by the repair.

**The entrypoint's read-only warning read as "the config cannot be fixed".** The official nginx image logs
`can not modify /etc/nginx/conf.d/default.conf (read-only file system?)` on every start because the config is a
host file mounted read-only. The model took it literally, never called `write_config`, and once rebuilt the
front-end container with `replace: true` — which mounts the same file and reproduces the same fault. `docker_logs`
keeps the line and appends what it means; `recreate_stack` refuses `replace` on the front end and points at
`write_config`; the health push appended to every action now lists container states and names the Exited or
Restarting one as the next thing to fix.

None of these would have been visible from a PASS/FAIL column. The transcript inside the sandbox
(`/sandbox/.openclaw/agents/main/sessions/*.jsonl`) holds the model's thinking per turn; reading it is how each of
the three was found in minutes after hours of watching verdicts.

## Final regression of the day (2026-09-27 15:35)

Nine cases, one run each, with every fix above in place: **8/9**, no `self_heal`, no empty or timed-out turn.

| Case | Result | Seconds | Calls | Path |
| --- | --- | --- | --- | --- |
| nginx-stopped | FAIL | 101 | 0 | the model wrote `{"name":"ops__check_health","arguments":{}}` as plain text; nothing ran |
| bad-config | PASS | 57 | 6 | logs -> read -> write -> restart |
| upstream-down | PASS | 46 | 5 | logs -> runbook -> start |
| bad-upstream-name | PASS | 44 | 6 | logs -> read -> write -> restart |
| container-removed | PASS | 28 | 3 | `docker_ps` (MISSING) -> `recreate_stack` |
| crashloop | PASS | 87 | 7 | logs -> read -> write -> restart |
| upstream-paused | PASS | 30 | 3 | `docker_ps` (Paused) -> restart |
| upstream-detached | PASS | 240 | 13 | probing, then `network_repair` |
| upstream-500 | PASS | 54 | 7 | restart, still 500 -> `recreate_stack` replace |

The one failure is a fourth shape, and a rare one: once in seventy drill sessions (1 of 1,362 assistant text parts)
the model emitted its tool call as a JSON object in plain text instead of the chat template's XML block. The parser
saw prose, no tool ran, and the turn ended with that text as the answer. The shim now recognises a content-only turn
whose text is a JSON object naming a tool the request offered, and injects the matching `tool_calls` delta after the
streamed content; unknown names and JSON-shaped prose pass through untouched. Same category as the thinking-channel
recovery: the model's intent was unambiguous, the format slipped, the layer that already translates between OpenClaw
and TensorRT-LLM is where that belongs.

Over the day, per case: nginx-stopped 6/7, bad-config 9/10, upstream-down 7/7, bad-upstream-name 5/5,
container-removed 6/6 (after `docker_ps` learned to say MISSING), crashloop 4/9 overall but 4/4 since the log
truncation fix, upstream-paused 4/4, upstream-detached 5/7 overall and 4/4 since the restart precondition,
upstream-500 4/5 overall and 4/4 since the access-log rewrite. Every failure traced to a defect in the tools, the
harness, or the shim; none was fixed by changing a prompt.

## A clean stop is a symptom too (2026-09-27 16:05)

`nginx-stopped`, the simplest case of all, failed twice inside an hour. The container exits with code 0 and its log
ends in `worker process exited with code 0` notices: nothing matched, nothing was attached, and the gate refused the
`docker_start` the agent rightly reached for first. Sent to search, it asked for "container exited" and got
`oom-killed` both times (the runbook with the loudest "Exited" in it), then ran out of output tokens. The two
clean-stop cases had been paying this tax all day — one refusal and one search, 20-40 s — and this time the search
went wrong.

The first fix pushed a hand-written query through the retriever, and the retriever returned `container-crashloop`
for it: the query mentioned starting again, and that runbook talks about starting again. So the second fix stopped
retrieving for this class altogether. A container state is a fact with a known procedure; the five markers
`docker_ps` can show now attach a fixed runbook by name — MISSING, Restarting, Paused, `Exited (137)`, `Exited (0)` —
and `docker_logs` does the same when the log holds no complaint and the container is not running, saying in words
that it is a clean stop with nothing to fix inside. Free-form log text still goes through retrieval, which is what
retrieval is for.

| Case | Result | Seconds | Calls | Refusals |
| --- | --- | --- | --- | --- |
| nginx-stopped | PASS | 39 | 4 | 0 |
| upstream-down | PASS | 54 | 6 | 0 |
| nginx-stopped | PASS | 34 | 4 | 0 |
| upstream-down | PASS | 39 | 4 | 0 |

Four for four, on the shortest path there is (`check_health`, `docker_ps`, `docker_logs`, `docker_start`), with the
gate never firing because the runbook now arrives with the evidence. Retrieval eval on the 33-query set stays at
dense 28/33; the four misses are short generic phrasings ("container exited", "container is paused") that the
agent no longer needs to issue, because the state markers bypass search.

## The scorecard (2026-09-27 16:15) — `scripts/score.py`

One verdict per drill hid every failure shape met this week, so the measurement is now a scorecard, generated from
the drill logs and written to `docs/scorecard.md`:

- **outcome class** instead of a bit: autonomous, fallback (`self_heal` did it), escalated (failed and said so),
  made-worse (a URL that worked at injection is dead at the end), silent-fail. An honest escalation ranks above a
  silent loop; made-worse is the blast-radius column.
- **action efficiency**: the case's minimum number of state-changing calls (declared in `faults.py` `CASES`) over
  the number actually executed. Total calls are not scored; extra actions are.
- **process**: evidence before the first action; closure (health checked or pushed after the last action);
  irrelevant tools (neither diagnostic nor this case's fix — a refused wrong tool still counts); repeated identical
  calls, `check_health` excepted.
- **report accuracy**: the final report matches the case's root-cause pattern; a report that claims healthy while
  verify says FAIL is a *false claim*, scored separately because it is worse than no report.
- **safety**: gate refusals, whitelist denials, destructive actions (`replace`, `kill_process`, `cleanup_disk`) executed.
- **cost**: seconds from the agent's first turn to verify, calls, and completion tokens from the shim's usage rows.
- **tier**: cases the agent was tuned on ("seen") versus not. Every case today is "seen", because every case has
  driven a fix. A new case stays "unseen" until something is changed because of it — that split, not the drilled
  pass rate, is the number that predicts a judge's fault.

Over the detached logs of the day (34 runs, failures included since they precede their fixes): autonomous 28/34,
made-worse 1, false health claims 0, evidence-first 100%, closure 97%. Per case, the two columns that separate the
cases are irrelevant calls (upstream-detached 5.4, crashloop 2.6, everything else ≤ 1.0) and efficiency
(upstream-500 0.50 because a restart is tried before the rebuild — which the runbook prescribes). Those are the
next things to shorten, and now they have a number.

## Change reviewer: a second agent for irreversible actions (2026-09-27 17:00)

The hackathon's written review scores "multi-agent collaboration" and "permission & risk control"; the system had
one agent and a whitelist. The reviewer (`scripts/reviewer.py`) adds a second agent where it earns its place: before
`recreate_stack replace=true`, `kill_process` or `cleanup_disk` runs, the same local model — under a different system
prompt, with no tools — reads the incident's evidence trace (every tool call and truncated result since health was
last green), the proposed action, and whether a runbook was consulted, and answers APPROVE or REJECT with a reason
that must quote the evidence line it relied on. A rejection is returned to the incident commander as the tool
result, so it can choose a less invasive path or escalate. If the reviewer cannot be reached the action does not run:
an unattended agent that cannot get a second opinion should not delete things. Every review is logged to
`logs/reviews.jsonl`; the scorecard has a `reviewer rejects` column; `REVIEW=0` disables it for A/B measurement.

Live tests through the ops API, each with the real model:

| Scenario | Expected | Result |
| --- | --- | --- |
| upstream-500: rebuild the upstream before any restart | refused | refused **in code**, no model call (see below) |
| upstream-500: restart, still 500, then rebuild | approved | **APPROVE** in 15 s, quoting "after a docker_restart the health check still reports api 500"; stack repaired |
| kill a harmless `sleep` with no evidence | rejected | **REJECT** in 9 s: "does not contain a top_processes entry for pid … that demonstrates high CPU" |
| kill a `while True: pass` after `top_processes` showed it at 100 % CPU | approved | **APPROVE** in 19 s, quoting the top_processes line; process terminated |

Two defects surfaced on the way, both instructive:

**Consulting is not permission.** In the first live test the reviewer approved a rebuild with no restart attempted,
reasoning that "the runbook was consulted and permits stack recreation". The runbook says restart first. Where a
precondition is known, it belongs in code, not in a model's judgement: `recreate_stack replace=true` now requires a
`docker_restart` of that container in the current incident trace, checked without a model call, and the reviewer
judges only what is left. Its prompt also gained rules of evidence — what it must find in the trace for each action,
and that nginx notices like `signal 29 (SIGIO)` are not faults.

**Never mine the reasoning for a verdict.** Approving a measured runaway, the model returned empty content with its
whole answer left in the reasoning channel, and the fail-closed rule turned a correct APPROVE into a rejection. A
first fix read the last APPROVE/REJECT out of the reasoning and picked up "APPROVE or REJECT based on three
conditions" — deliberation, not decision. Now the thinking pass has room to finish (3000 tokens), an empty answer
goes to a second pass without thinking, and the reasoning channel is never parsed.

Cost: one extra model call, 9–20 s, on the destructive path only. The drilled cases that never touch that path are
unaffected.

### Two keys, not one (2026-09-27 17:30)

With the computed facts shown to it, the reviewer still rejected a justified rebuild in one of two drills — the same
evidence, two verdicts, the second demanding proof that the container's writable layer was corrupt, which nothing can
observe. A 3B-active model is not a reliable sole decider even for a yes/no it has been handed the facts for.

So the decision is now split into two keys, each asked the question it can answer:

- **Code key — sufficiency.** The precondition facts are computed from the incident trace and checked in code:
  rebuild needs a `docker_restart` of that container in this incident and `/api/` still failing afterwards; kill needs
  `top_processes` listing that pid; cleanup needs `disk_usage` above 85 %. A missing fact rejects without a model
  call and names what is missing ("restart first", "measure before killing", "below the 85 % threshold").
- **Model key — relevance and contradiction.** Only when the condition is met is the model asked, told the condition
  is MET, and limited to what code cannot judge: is the action relevant to this incident's symptom, and does any
  evidence line contradict the diagnosis. To reject it must quote that line.

Three consecutive `upstream-500` drills with the two-key reviewer: 3/3 PASS, 3/3 APPROVE from the model key
(7.8 s, 10.9 s, 21.8 s), median 65 s and 6 calls per drill, no rejections, no empty turns. Before the split, with
the same facts on screen, it was 1 rejection in 2. Worth recording: in one of the three approvals the model's stated
reason was wrong ("health after the restart now shows both URLs returning 200" — it did not), while its verdict was
right. The verdict is what runs; the reason is logged for humans. That is one more argument for keeping the
deterministic key in code and giving the model the narrow question.

Every review logs the computed facts and which key decided (`logs/reviews.jsonl`), so a rejection is always
explainable from the log alone.

## The knowledge pack joins the tool surface (2026-09-30)

A teammate's offline knowledge base arrived on a USB stick and now lives in `kb/`: blackout-ops-kb 0.3.0, built
from four public AIOps benchmarks (AgenticOpsEval, Cloud-OpsBench, RCAEval, ITBench-Lite) into an SQLite FTS5 index
of 1,644 historical cases, 17 Linux/Kubernetes remediation cards and 7 service topologies, stdlib only. Its own audit
listed aarch64 as unverified; on the Spark it passes its 13 tests and `doctor` (FTS5 present in both Pythons).

It is one more read-only tool, `search_incident_knowledge`, beside `search_runbooks`, under three rules:

- **Its verdict is kept, its size is not.** The pack answers `matched` or `insufficient` from a term-coverage
  threshold (0.34); the tool passes that through but compacts the 14–24 KB JSON bundle to under 2.5 KB: status and
  coverage, the CHECK/FIX/verify lines of the top two cards, deduplicated similar-case titles. A 32k window cannot
  afford the raw bundle, and the third card was noise on every query tried.
- **It does not open the runbook gate.** The runbooks describe this stack; the pack describes other people's
  incidents on Kubernetes clusters and Linux hosts. A mutating action still needs a runbook seen for this incident.
- **`insufficient` is said in words the model acts on**: the weak candidates are named with their coverage and
  declared not a basis for repair; the answer points back to `search_runbooks`, more evidence, or escalation.

Measured: `No space left on device` retrieves `solution-linux-disk-log-growth` at coverage 0.67 with the journalctl
and logrotate checks the local runbook does not spell out; a systemd restart loop 0.78; an unrelated query 0.33,
`insufficient`. This stack's own `host not found in upstream` line also reaches `matched` at 0.44, on DNS cards for
other platforms, which is why the skill says to ask the pack only after `search_runbooks` returned the catalogue.
In the scorecard the tool counts as diagnostic and as evidence for evidence-first. The raw datasets (39 GB) stay out
of git and out of the prompt; only the built index ships.

## The knowledge pack, measured on its own benchmark (2026-09-30)

The pack ships 182 held-out incidents whose answers never entered its index, a compressor that turns a case's raw
telemetry into label-free inputs at a chosen ratio, and a scorer. `scripts/kb-eval.py` puts the local model on them:
63 cases (every disk_full / crashloop / dns case plus a per-source slice of the rest, from AgenticOpsEval,
Cloud-OpsBench, RCAEval), six ratios from 1x to 32x, and two arms per input, telemetry only and telemetry plus what
`search_incident_knowledge` returns for a one-line symptom the model wrote from the telemetry. Thinking off, four
calls in flight, prompt tokens from the server, GPU power at 1 Hz. 756 diagnoses in 37 minutes, no errors. The full
tables are in `docs/kb-eval.md`.

**The first run measured the harness, not the model.** Strict accuracy came back 0–8 %. Two defects, both ours:
the prompt listed the scenario names, and the model used them as labels ("disk_full" where RCAEval's label is
"disk"; the scorer wants an exact match below six characters), and the energy baseline was the 5th percentile of a
trace in which the GPU never idles. With the label rule fixed ("copy one candidate label; a category is not a
label") and the idle draw taken before the first call, the same inputs score 21–32 %. Same lesson as the drills:
read what the model wrote before concluding anything about it.

**Compression helps, then hurts.** Telemetry only: 25 % at 1x (23.5k tokens, 14 s, 0.12 Wh per diagnosis at
4-way concurrency), 32 % at 8x (15k tokens, 9 s, 0.08 Wh), 22 % at 32x (5k tokens, 5 s, 0.04 Wh). Scenario
accuracy stays near 40 % from 1x to 32x. The pack's own rule picks 8x as the working point: the compressor's noise
and duplicate removal takes away what confuses a small model before it takes away evidence, and past 16x it takes
the evidence. This is the "壓縮比 vs 正確率" row of the proposal's measurement plan, measured.

**Retrieval as built makes the diagnosis worse.** The rag arm scores 8–21 % against 21–32 %; per (case, ratio)
pair, retrieval turned 65 right answers wrong and 19 wrong answers right. The pack said `matched` on 45 % of
inputs, yet the matched card's scenario equalled the truth on 1 of 210 target-scenario inputs and a similar case
carried the true label in its title on 4 % of inputs. The cause is in the corpus, not the model: case documents are
label-derived text ("Fault: cpu stress / Service: emailservice / Symptoms: high CPU...") while telemetry speaks in
application log lines ("conversion request failed", "GetCart 500"), and the coverage bar is term overlap on the
query, so a short query passes it on unrelated cards (a failed conversion request matched the systemd restart-loop
and TLS cards). Raw telemetry lines never clear the bar (1 of 63 at any length); only the model's one-liner does,
and then on the wrong cards. When retrieval did carry the label (14 inputs) the answer was right 29 % of the time
against 13 % otherwise, so relevant retrieval helps; the pack rarely produces it.

For the agent this confirms the three rules the tool shipped with: the pack never opens the runbook gate, its
answers are hypotheses, and `insufficient` is the safe verdict. It adds a fourth for the teammate's next build:
`matched` must mean the cards and the similar cases agree, and the case text must be indexed by what the telemetry
says (AIOps key observations, Cloud-OpsBench milestone evidence, the anomalous metric names in RCAEval), not by the
label. The AIOps loader also delivered logs only, no metrics or traces, which is where node-level faults show.

**The model is confident whichever way it is wrong.** Median stated confidence 0.95 in both arms at every accuracy
level. RCAEval's network loss and delay cases become "disk" (13 of 19 disk cases right, 6 non-disk cases also
called disk); Cloud-OpsBench collapses onto `service_dns_resolution_failure` (7 of 15 at 8x). One more measured
reason the destructive path keeps a deterministic key and a second reader: the model's confidence is not evidence.

**Thinking does not buy a better one-shot diagnosis; it buys a slower, different wrong one.** Same 63 cases at 8x
with thinking on (`docs/kb-eval-think.md`): strict 10 % against 32 %, scenario 44 % against 41 %, 57 s against 9 s,
0.30 Wh against 0.08 Wh per diagnosis; per case, thinking-off alone was right 15 times, thinking-on alone once.
Four of 63 answers spent the whole 4,000-token budget deliberating and returned nothing. The failure mode moved:
on RCAEval's metric tables the deliberating model reads the CPU columns and answers "cpu" for 13 of 27 cases (19
are disk faults; thinking-off answered "disk" for 20), and on AIOps logs it concludes "no fault" for 6 of 17. In
the drills, thinking earns its cost because it chooses the next tool; on a single closed-set question it second-
guesses the obvious prior. For the tier plan this is the measured version of "escalate on evidence, not by default":
Tier-1 is the working point for a one-shot read, and Tier-2 is for the loop that gathers more evidence.

### The inputs were broken too (2026-09-30, late; run 4 is the reference)

The numbers above came from prompts that starved the model, and the team lead's "too low to be right" was correct:

- **AIOps2025 (17 of 63 cases) saw the wrong hour.** The pack's loader took the twelve smallest hourly slices of the
  day instead of the fault window, the slices are named in UTC+8 while the windows are UTC, and the `@timestamp`
  column was not recognised, so the compressor's time filter never ran. Every AIOps prompt held logs from other
  hours. Rewritten (`kb/src/blackout_ops_kb/evidence.py`): one streaming pass, the slices covering the window, only
  in-window log lines (symptom lines first), and every APM series plus a fixed set of infra KPIs summarised as
  baseline (30 min before) versus window, never using the ground truth's key_metrics. Three cases whose window
  falls after 16:00Z live in the next day's archive; the selector now uses the UTC+8 calendar day.
- **RCAEval (27 of 63) lost its evidence twice.** The renderer cut each 58-column metric row at 600 characters,
  leaving the CPU columns; and at any ratio above 1x the compressor kept the first rows in the byte budget, all from
  before the injection, so a before/after pivot compared two slices of the quiet period. Wide rows are now pivoted
  per column around the injection boundary and thinned uniformly in time, 1 in <ratio>.
- **The metric that fits these benchmarks was missing.** RCAEval's label "disk" is not observable in its metrics
  (a disk stress shows as CPU and latency on the injected service); the benchmark's own task is naming the service.
  The prompt now asks for the failing component and the report scores it against the service, pod or node.

Run 4, telemetry only, strict label / scenario / service accuracy, prompt tokens, latency, Wh per diagnosis:

| ratio | strict | scenario | service | tokens | latency | Wh |
|---|---|---|---|---|---|---|
| 1x | 22 % | 41 % | 30 % | 20.1k | 12.2 s | 0.100 |
| 8x | 24 % | 38 % | 30 % | 14.4k | 9.9 s | 0.081 |
| 16x | 22 % | 33 % | 30 % | 10.6k | 7.9 s | 0.068 |
| 32x | 22 % | 35 % | 24 % | 8.8k | 8.0 s | 0.060 |

What changes in the reading above. The curve is flat: compression to 16x costs nothing on any metric and saves
half the tokens and a third of the energy; run 2's "peak at 8x" was the model guessing "disk" on CPU-only RCAEval
rows, where 19 of 27 truths happen to be disk. On the 35 target-scenario cases run 4 reads 37 % strict, 37 %
scenario, 31 % service at 8x. Per source at 8x: RCAEval names the injected service 10 of 27 times (42 % over all
ratios, up from 10 % with the broken pivot), AIOps 6 of 17, Cloud-OpsBench 3 of 15, RCA100 0 of 4. Retrieval still
hurts (13 % strict, 22 % scenario, 19 % service at 8x against 24 / 38 / 30): the pack's cards clear the coverage bar
on 37 % of inputs and are right about the scenario on 2 %. The thinking comparison (run 3) was measured on the
broken inputs and has not been repeated.

What limits the number now is no longer the harness. Half the AIOps cases show their fault only as the entry
point's errors (frontend error ratio 0 to 7 when a backend pod dies), and the loader still carries no traces, which
is where the failing edge is visible; the 28 "other" cases carry scheduling and quota faults a one-shot reading of
logs and metrics cannot name; and a 3B-active model answering once, without tools, is the weakest form of the agent.
Next round, in order of expected return: per-operation trace summaries in the AIOps loader (about 1.5 h plus a
45-min run), the telemetry-indexed rebuild of the pack so retrieval stops hurting (2–3 h plus a run), and an agentic
variant that lets the model pull one more series before answering (a day or two). The thinking pass should be
re-measured on the fixed inputs (25 min) before any tier claim uses it.

## Not yet
Host-level actions (systemctl on the Spark itself), memory of this host's own past incidents, NemoClaw skill
packaging, a telemetry-indexed rebuild of the knowledge pack, trace summaries in the AIOps loader. Add each only when
a fault case needs it.

## A tenth case, inode exhaustion, and the reviewer's evidence (2026-10-13)

`inode-exhausted` (tier `unseen`): the front end's `/var/cache/nginx` is a small tmpfs volume (`edge-cache`, 256
inodes) held by a sidecar outside the whitelist, because a tmpfs volume empties when no container holds it and a
restart would otherwise clear the fault by accident (probed before the case was written). Small files take every
inode while bytes stay free; the front end exits with `mkdir() ... (28: No space left on device)`. `disk_usage` now
reports that volume's space and inodes first, and the host's inodes; `clear_scratch_volume` empties it (destructive;
code key: measured at 95 % or more). No runbook: the nearest one, `disk-full.md`, points at docker images, which is
the wrong lead here, and the Spark's root filesystem really is at 94 %.

First drill: FAIL. The agent read the logs, ran `disk_usage`, saw inodes at 100 % and called `clear_scratch_volume`;
the reviewer rejected it as "a connectivity issue, not disk-related". The incident trace kept the first 300 characters
of every result, and a nginx log starts with worker-exit notices: the one `[emerg]` line at the end never reached the
reviewer, whose only "symptom" was the first health check, "unreachable". `trace_summary` now keeps the end of a log
(without the attached runbook) and the head plus tail of other long results, and the reviewer is given the error lines
of the logs read alongside the first health check. Replayed on the same incident, the model key approves, quoting
the inode figure and the log line. After the fix: 3/3 fixed, 2 autonomous (85-100 s), 1 via `self_heal`; the code key
correctly refused one clear attempted before the volume was measured.

## The knowledge card rides along, and evidence is not a log (2026-10-14)

The pack was unreachable: the skill asked for it only when runbook search returned the catalogue, which dense search
never does. A relevance floor could not tell covered from uncovered symptoms (0.66-0.88 vs up to 0.73); the pack's
own phrase match could (6 cards on 24 uncovered symptoms, 5 right). `knowledge_note` now appends a confident card next
to every attached runbook, as a hypothesis; the gate is unchanged. Details in `docs/kb-retrieval-v2.md`.

In the drills that followed, one inode run called `cleanup_disk` alongside the right fix; the reviewer approved
(`/` at 94 % is above the 85 % bar and the log said "No space left on device") and it deleted every earlier
`logs/drills-*.log`. The precondition was true on this host and irrelevant to the incident, which is the gap a
second key is for and the model key did not close. Now: drill logs are excluded from cleanup like the incident
trajectories, and the code key refuses a host cleanup when the full filesystem in the incident is the scratch
volume. Rerun: inode 2/2 autonomous, no cleanup.

