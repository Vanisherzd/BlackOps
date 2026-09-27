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

## Not yet
Host-level actions (systemctl on the Spark itself), memory of past incidents, embedding-based retrieval, NemoClaw
skill packaging. Add each only when a fault case needs it.
