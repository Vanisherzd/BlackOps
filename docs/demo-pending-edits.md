# Demo: pending edits to existing files (2026-10-20)

On 2026-10-20 a repair experiment was running on the Spark (`ab-drill.sh` → `nemoclaw-drill.sh`). It restarts
`ops_api.py` and `oai_shim.py` from the working tree before every drill, so editing an existing file under
`scripts/`, `runbooks/` or `nemoclaw/workspace/` would have changed the experiment while it ran. Everything added
that day is therefore **a new file**, and either inert or opt-in (see the table). The existing files still need the
edits listed below. Apply them **after the experiment ends**, by or together with the owner of each file. After the
§4 edits (they change the victim), rerun the regression (`faults.py run <case>` for the nine seen cases).

## New files (2026-10-20, untracked, not committed)

| File | Runbook item | Default behaviour | Tested |
|---|---|---|---|
| `scripts/run-archive.sh <case>` | #5 archive | Wraps `nemoclaw-drill.sh` without editing it, then copies this run's logs to `logs/runs/<time>-<case>/` | Syntax only (running it would collide with the experiment on the shared victim) |
| `scripts/preflight.sh [--warm]` | #8 | Read-only checks; while a drill is running it skips `nemoclaw status`, which takes that drill's lock | Run while the experiment was going: tier-2, shim and ops-api PASS |
| `scripts/watchdog.py [--dry-run]` | #4 | Wakes no agent while `nemoclaw-drill.sh` is running | Against a stand-in local HTTP server: open → resolve → archive |
| `scripts/wan.sh down --yes\|up\|status` | #6 | `down` refuses without `--yes` (on a shared host it cuts everyone); needs `sudo -n iptables` | Syntax, `status`, and the refusal only. **Never run down on this machine.** |
| `scripts/dashboard.py` + `dashboard.html` | #7 | Port 8097. Buttons stay off until `DASH_ACTIONS=1`, and stay off while a drill is running; reads logs only | Every endpoint against a copy of the logs; JS passes `node --check`; layout not yet seen in a browser |
| `scripts/power.py log\|energy\|incidents` | #12 | tegrastats on Thor, nvidia-smi on the Spark | 5 s of nvidia-smi, Wh integration, tegrastats line parsing |
| `scripts/approval.py` | #10 | `APPROVAL=off`, so every call passes | manual (approved from the dashboard), timeout = reject, off |
| `scripts/tier.py` | #11 | `TIER_MODE=off`, a no-op | adaptive 1→2→1, upstream switch, the dashboard shows it |
| `scripts/kb-probe.py` | KB regression | Reads kb/ only | 14/70 pushed: 11 right, 7 missed, 2 wrong |
| `victim/board/index.html` | #3 | Not served until the mount below exists | — |

## Edits still needed in existing files

### 1. Prompts and runbooks (runbook #2; small, behaviour-neutral, but change what the agent reads)

`runbooks/upstream-502.md:4`: the sandbox cannot curl the host, so this step can never be followed.
```diff
-Steps: 1) Confirm with `curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8880/api/`. 2) `docker ps -a`: the
+Steps: 1) Confirm with `check_health`. 2) `docker ps -a`: the
```
After this edit, rebuild `logs/rag-index.npz` (see `scripts/rag.py`), or the dense index keeps the old text.

`nemoclaw/workspace/AGENTS.md:13`: the list leaves out the two tools that act two of the stage cases need
(container-removed and upstream-500 both need `recreate_stack`).
```diff
-- Change: `docker_start`, `docker_restart`, `write_config`
+- Change: `docker_start`, `docker_restart`, `write_config`, `restore_config`, `recreate_stack` (a rebuild is reviewed first)
```
The workspace has to be pushed to the sandbox again for this to take effect.

`skills/ops/SKILL.md:10`, `nemoclaw/workspace/AGENTS.md:6`, `TOOLS.md:6` (8880): no change is needed while the
victim runs on the Thor. With the remote victim (#1), change them to "the health URLs are measured by
`check_health`" and leave out the address.

### 2. Human approval (runbook #10): `scripts/agent.py` `reviewed()`

```diff
     ok, reason = reviewer.review(name, args, _incident["trace"], _incident["runbook_seen"])
     if ok:
-        return None
+        import approval                                    # third key: the human (APPROVAL=off by default: passes)
+        approved, text = approval.request(name, args, reason)
+        return None if approved else text
```
The MCP timeout in OpenClaw is 600 s and `APPROVAL_TIMEOUT` defaults to 180 s, so the blocking wait fits.

### 3. Tier controller (runbook #11): `scripts/agent.py` and `scripts/oai_shim.py`

`agent.py`:
- In `with_health()`, in the healthy branch (the `_incident.update(...)` line), and in `check_health` at `:550`:
  `import tier; tier.set_tier(1, "health green")`
- Before `return f"{result}\nhealth now: ... still failing`: `import tier; tier.set_tier(2, "action left the stack failing")`
- At the start of `reviewed()`: `import tier; tier.set_tier(2, f"review of {name}")`
- After `_incident["refusals"] += 1`: `if _incident["refusals"] >= 2: import tier; tier.set_tier(2, "guards refused twice")`

`oai_shim.py:346`:
```diff
-        req = urllib.request.Request(UPSTREAM + self.path, data=data, method=self.command,
+        import tier                                       # TIER_MODE=off by default: returns UPSTREAM unchanged
+        req = urllib.request.Request(tier.upstream(UPSTREAM) + self.path, data=data, method=self.command,
```
To verify first: whether the 4B and the 30B can both be handled by this shim (the tool-call parsing at `LOOSE_CALL_RE`
and `PARAM_RE`, and the reasoning parser) when the model changes mid-conversation. `trtllm-serve` for the 4B needs a
`--reasoning_parser` that fits it (`serve-trt.sh:26` defaults to `nano-v3`).

### 4. Board page (runbook #3): `scripts/faults.py` and `scripts/agent.py`

```diff
 GOOD_CONF = """server {
     listen 80;
     server_name _;
     location / { return 200 "ok\\n"; add_header Content-Type text/plain; }
+    location /board/ { alias /usr/share/nginx/board/; }
     location /api/ { proxy_pass http://edge-victim-app:80/; proxy_connect_timeout 2s; proxy_read_timeout 5s; }
 }
```
SPECS FRONT: add `-v {ROOT / 'victim' / 'board'}:/usr/share/nginx/board:ro`. The container has to be recreated
(`faults.py setup`) for the mount to take effect.

`agent.py:620`, in write_config: refuse a config that drops `/board/`.
```diff
-        if len(content) > 20000 or "proxy_pass" not in content:
+        if len(content) > 20000 or "proxy_pass" not in content or "/board/" not in content:
```
Changing the denial text has to go together with this. After the change, rerun the regression for bad-config,
config-empty and api-path-404, because GOOD_CONF is the reference for those faults.

### 5. Remote victim (runbook #1): about 1–1.5 days, the riskiest of these

As the runbook describes. This changes `faults.py` and `agent.py`, the files every experiment depends on.
Do it on a separate branch and in a separate worktree, not in this shared working tree. Rerun all nine cases.

## How to start them on stage (once the edits above are in)

```bash
.venv/bin/python scripts/power.py log &                        # power curve
.venv/bin/python scripts/watchdog.py &                         # rule-based patrol; wakes the agent on an incident
APPROVAL=manual TIER_MODE=adaptive UPSTREAM_T1=http://127.0.0.1:8010 .venv/bin/python scripts/ops_api.py &
DASH_ACTIONS=1 APPROVAL=manual .venv/bin/python scripts/dashboard.py &
DASHBOARD_URL=http://127.0.0.1:8097 T1_URL=http://127.0.0.1:8010 scripts/preflight.sh --warm
```
`APPROVAL` and `TIER_MODE` must be set in the environment of the process that runs `agent.py`'s tools, which is
`ops_api.py`. `TIER_MODE` must also be set in the shim's environment. The dashboard's `APPROVAL` only sets what it
displays.
