# BlackOps fault-recovery reference audit

Reference check: 2026-10-21. This note checks recovery mechanics against Docker, NGINX, Linux kernel, Linux man-pages maintainer, and curl project documentation. It does **not** establish which experiments exist in the dataset or claim that recovery has been tested locally. No fault injection, container commands, server access, or dataset changes were performed for this note.

## Local capability boundary supplied for this audit

These are **local harness facts supplied by the audit task**, not capabilities established by public documentation:

- Frontend: `edge-victim`; backend: `edge-victim-app`.
- Read tools: `docker_ps`, `docker_logs {name,tail}`, read-only `docker_exec {name,cmd}`, frontend-site-only `read_config`, `check_health`, and `search_runbooks`.
- Repair tools: `docker_start {name}`, `docker_restart {name}`, `network_repair`, frontend-site-only `restore_config`, and reviewer-controlled `recreate_stack {name,replace:true}`. The latter requires a preceding start/restart attempt for the **same named target**, followed by still-failing health. Public Docker documentation does not establish this gate or what the local recreation tool rebuilds.
- Frontend site `default.conf` is bind-mounted. Image-owned `nginx.conf`, `mime.types`, and backend configuration are distinct repair targets. Do not describe `restore_config` as restoring them.
- No mutation via `docker_exec` is authorized by its read-only contract. All CLI commands below are **human/operator procedures**, not additional agent tools. No automatic chmod, copy, update, signal, unpause, inspect, or configuration-test tool is implied.

## Shared recovery rules

1. Use the smallest distinguishing evidence: target state, recent target logs, one relevant file or process/cgroup state if the target can execute commands. A failed health probe alone does not identify a fault family.
2. If a fault requires recreation, record the named start/restart attempt, its result, subsequent failing `check_health`, and reviewer decision before `recreate_stack`. Stop after successful health. These ordering rules come from the supplied harness contract.
3. Restarting retains the existing container filesystem; replacement discards its writable layer. Mounted data persists outside that layer. Therefore replacement can repair image-owned file damage **only if** the replacement image and startup specification reconstruct the intended baseline; it does not repair corrupted bind-mounted configuration. The storage distinction is documented; its application to these named containers is a local inference. [Docker storage](https://docs.docker.com/engine/storage/), [bind mounts](https://docs.docker.com/engine/storage/bind-mounts/), [restart](https://docs.docker.com/reference/cli/docker/container/restart/).
4. A rollback means restoring the pre-experiment known-good configuration, permissions, limits, topology, image, and launch specification. Do not reapply the injected fault. A replacement loses container-layer changes; a dataset row must state this cost rather than call replacement lossless.
5. Every recovery must finish with `check_health` and the originally failing route/request, including its expected status and meaningful content. A tool returning success or a container showing running is insufficient. Preserve the before/after evidence in the experiment record. This is an audit criterion, not an upstream product guarantee.

## Fault families and bounded remedies

### 1. Missing image-owned `mime.types`

**Evidence:** startup/test diagnostics name the missing included file, for example `/etc/nginx/mime.types`; distinguish a missing file from an HTTP content-type mismatch. NGINX includes configuration files and supplies a MIME mapping file. `nginx -t` checks syntax and opens referenced files. [NGINX include](https://nginx.org/en/docs/ngx_core_module.html#include), [types](https://nginx.org/en/docs/http/ngx_http_core_module.html#types), [command parameters](https://nginx.org/en/docs/switches.html).

**Agent path:** named start/restart, failing health, reviewer, then `recreate_stack {name:"edge-victim",replace:true}` if the image and launch baseline restore that file. Frontend `restore_config` cannot restore an image-owned MIME file. Restart alone does not restore missing bytes.

**Human alternative / rollback:** copy the exact image-version-compatible known-good file, using the confirmed path, then start the stopped container or test and reload a running one. `docker cp` works with stopped containers. Verify health and expected content type; retain the baseline file's provenance. [Docker cp](https://docs.docker.com/reference/cli/docker/container/cp/).

### 2. Content permission denied after chmod

**Evidence:** a content-open error naming the path plus observed mode/owner and worker identity. A 403 alone is insufficient: directory search permission is also required, and Linux returns EACCES if it is missing. [Linux path resolution](https://man7.org/linux/man-pages/man7/path_resolution.7.html), [NGINX worker user](https://nginx.org/en/docs/ngx_core_module.html#user).

**Repair / rollback:** human restores the **recorded original** mode on the single affected file or directory. Example below uses 0644 only when that was the baseline. Inspect ACL/security-policy evidence if the mode is already correct; do not prescribe recursive chmod or 0777. `chmod` changes file mode bits. [Linux chmod](https://man7.org/linux/man-pages/man2/chmod.2.html).

**Agent path:** no chmod tool exists. Replacement is a conditional fallback for writable-layer damage after the gate, and cannot reset permissions on an external mount. Verify the same content request and its body, not only a separate health URL.

### 3. Process SIGSTOP

**Evidence:** Docker may still report running. Inspect the relevant `/proc/<pid>/status` when read-only exec is available; `T` indicates a traced/stopped process and must be interpreted with the experiment record. SIGCONT continues a stopped process. [Kernel proc documentation](https://docs.kernel.org/filesystems/proc.html), [Linux signals](https://man7.org/linux/man-pages/man7/signal.7.html).

**Repair / rollback:** human sends SIGCONT to exactly the stopped process or processes. `docker kill --signal=SIGCONT edge-victim-app` reaches the container's **main process** only; it does not resume an independently stopped worker. Do not use an unqualified `docker kill`, whose default is SIGKILL. [Docker signals](https://docs.docker.com/reference/cli/docker/container/kill/).

**Agent path:** one named `docker_restart`, then health; a restart replaces processes but has disruption cost. It is not evidence that the narrower SIGCONT procedure ran. Verify route recovery and process state.

### 4. Docker pause

**Evidence:** paused container state distinguishes this from SIGSTOP. Docker pause suspends all container processes through the Linux freezer; exec on a paused container fails. [Docker pause](https://docs.docker.com/reference/cli/docker/container/pause/), [exec paused behavior](https://docs.docker.com/reference/cli/docker/container/exec/).

**Repair / rollback:** human `docker unpause edge-victim-app`, then state and health. Docker unpause is the documented inverse. [Docker unpause](https://docs.docker.com/reference/cli/docker/container/unpause/).

**Agent path:** no unpause tool exists. Do not invent `docker_unpause`, silently turn read-only exec into a mutation, or promise that start/restart handles paused state. Record the missing capability and human handoff. Any permitted restart/recreation attempt remains subject to the supplied gate and observed results.

### 5. Backend 429/500 caused by its own NGINX config

**Evidence:** read the backend file named by its launch configuration/logs via read-only `docker_exec` while running; frontend `read_config` does not inspect it. `return` can directly issue a status. Rate-limit rejection defaults to **503**, so 429 alone does not prove rate limiting; look for configured `limit_req_status 429` and associated evidence. [NGINX return](https://nginx.org/en/docs/http/ngx_http_rewrite_module.html#return), [limit_req_status](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html#limit_req_status).

**Repair / rollback:** restore the known-good backend file, test, and reload/start. Human commands below are applicable only after confirming the file. Agent replacement is conditional on the application baseline being reconstructed, after the gate. Restart does not remove a valid `return 500;` or `return 429;`. Frontend restoration cannot fix this family. Verify the failing request with its expected status/body; no load test is necessary merely to remove an injected unconditional return.

### 6. Redirect loop

**Evidence:** repeated HTTP 3xx Location values identify an external loop; an internal rewrite cycle is different and NGINX returns 500 after its internal-redirect limit, with a cycle error in its log. [NGINX redirects](https://nginx.org/en/docs/http/ngx_http_rewrite_module.html#return), [internal redirects](https://nginx.org/en/docs/http/ngx_http_core_module.html#internal).

**Repair / rollback:** restore only the config containing the faulty redirect. Use `restore_config` only for the frontend mounted site; backend or main configuration needs human restoration or conditional gated replacement. A syntactically valid loop can pass a syntax test. Verify a bounded redirect chain reaches the expected final route/status. Human `curl --location --max-redirs 5 --max-time 10 --dump-header - --output NUL <approved-health-url>` is a Windows-host example; use `/dev/null` on Linux. curl documents redirect and time limits. [curl manual](https://curl.se/docs/manpage.html#--location).

### 7. Detached Docker network

**Evidence:** loss of connectivity plus the affected target's missing expected network/alias, established through available tool output or human inspect. Do not infer detachment solely from a timeout. Human `docker inspect --format '{{json .NetworkSettings.Networks}}' edge-victim-app` obtains network attachment evidence. [Docker inspect](https://docs.docker.com/reference/cli/docker/container/inspect/).

**Agent repair:** `network_repair` under its existing schema; no new parameters are established here. **Human equivalent / rollback:** reconnect the exact original network and original alias/IP options, if any. `docker network connect --alias <original-alias> <original-network> edge-victim-app` is a template, not a command with confirmed local values. Docker documents network connection and aliases. [Docker network connect](https://docs.docker.com/reference/cli/docker/network/connect/).

Verify frontend-to-backend resolution/connectivity and the end-to-end request. Restart does not establish the original network membership. Retain original topology in the dataset row.

### 8. Removed container

**Evidence:** confirm target absence, rather than only absence from a running-only listing. Human `docker ps --all --filter name=^/edge-victim-app$` is one bounded check. Docker's default listing excludes stopped containers. [Docker ls](https://docs.docker.com/reference/cli/docker/container/ls/).

**Agent path / rollback:** named `docker_start {name:"edge-victim-app"}` records the missing-target result; failing health; reviewer; then `recreate_stack {name:"edge-victim-app",replace:true}` using the intended baseline. Docker start starts an existing stopped container; creating a missing one requires a creation specification. [Docker start](https://docs.docker.com/reference/cli/docker/container/start/), [Docker Compose up](https://docs.docker.com/reference/cli/docker/compose/up/).

Do not substitute an arbitrary `docker run nginx`: image, app content, mounts, ports, environment, resource limits, aliases, and intended command must match the recorded stack. Verify both target existence and the end-to-end response. Reconstruction is not recovery of lost writable-layer data.

### 9. PID cgroup exhaustion

**Evidence:** creation/exec failure is compatible with PID exhaustion but not sufficient alone. When readable, compare the target's `pids.current`, `pids.max`, and change in `pids.events`; file layout depends on cgroup version and namespace. The controller counts tasks/TIDs, can reject fork/clone with EAGAIN, and is hierarchical. An old nonzero event counter alone does not prove current saturation. [Kernel cgroup v2 PID controller](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#pid), [v1 PID controller](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v1/pids.html).

**Human repair / rollback:** if the injected fault lowered the limit, restore the exact recorded original with `docker update --pids-limit <original-limit> edge-victim-app`. Do not choose unlimited as a generic cure. Docker documents this update. [Docker update](https://docs.docker.com/reference/cli/docker/container/update/).

**Agent path:** no update tool exists. A restart may clear excess processes, but retains a bad configured PID limit; gated reconstruction works only if its launch specification restores that limit. Exec itself may fail because it needs a new process: avoid repeated identical exec attempts. Verify recovered process headroom and health without inducing saturation. Parent cgroup constraints may still bind.

### 10. `worker_connections` exhaustion

**Evidence:** correlate the target's relevant log message with its main `events` configuration and the experiment record. NGINX defines a per-worker connection ceiling including upstream connections, bounded also by open-file limits. It is not a client-count ceiling. [NGINX worker_connections](https://nginx.org/en/docs/ngx_core_module.html#worker_connections).

**Repair / rollback:** restore the recorded original main configuration value and any deliberately altered file limit, rather than selecting an arbitrary large ceiling. NGINX's official operational guidance links insufficient worker connections with connection/file-descriptor tuning. [NGINX connection guidance](https://docs.nginx.com/nginx-instance-manager/system-configuration/configure-gateway/#configure-worker-connections).

**Agent path:** frontend-site restoration does not touch main `nginx.conf`. Restart may briefly clear existing connections, but retains the injected setting. Human exact-file restoration or conditional gated replacement is needed for persistent main-file damage. Verify restored effective configuration and normal request behavior; label load-dependent capacity verification separately if it has not been run. Do not inject new load during this audit.

### 11. Backend NGINX syntax crash

**Evidence:** backend exited/restarting state and backend startup log naming its syntax error/file/line. Exec is unavailable when the primary process is stopped. [Docker exec lifecycle](https://docs.docker.com/reference/cli/docker/container/exec/).

**Repair / rollback:** human copies the matching known-good file into the stopped backend, then starts it. Agent path is named start, failing health, reviewer, then conditional recreation of the backend baseline. Frontend restoration is unrelated. Distinguish a startup crash from a rejected reload: NGINX checks a reload and continues the previous configuration if applying the new one fails. [NGINX reload control](https://nginx.org/en/docs/control.html#reconfiguration).

Verify running state, no new startup errors, configuration validity through a permitted operator procedure, and the originally failing route. Avoid repeated unchanged restarts against the same invalid file.

### 12. Deleted backend index (`upstream-index-deleted`)

**Local audit evidence reported by the parent audit:** six measured FAIL runs and zero PASS; the injector removes only `/usr/share/nginx/html/index.html` in `edge-victim-app`; frontend `/` remains 200 while `/api` returns 403 and the backend reports a forbidden directory index. Those counts and exact paths come from the local audit, not the public sources or an independent dataset read by this reference task.

**Mechanism:** NGINX normally resolves a trailing-slash request using `index index.html`; its autoindex module usually receives the request if no index is found and directory listings default to disabled. This supports the missing-index diagnosis when combined with the local log/file evidence. A 403 by itself still does not prove file deletion. [NGINX index](https://nginx.org/en/docs/http/ngx_http_index_module.html), [NGINX autoindex](https://nginx.org/en/docs/http/ngx_http_autoindex_module.html).

**Minimal diagnosis:** backend recent logs, then one read-only file existence/mode check for that exact path if exec works; correlate `/` and `/api` probe results. Frontend configuration edits are unrelated to restoring the deleted backend content. Do not enable directory listing or change an error response to make the probe green.

**Agent repair:** `docker_restart {name:"edge-victim-app"}` once, then `check_health`. If `/api` remains bad, present the evidence to the reviewer and call `recreate_stack {name:"edge-victim-app",replace:true}` only under the existing gate. The restart is a required bounded attempt, not a file-restoration mechanism. Reconstruction must restore the backend image's intended index and original launch specification; confirm that the affected path is in the writable layer, not a persistent mount. File changes and deletions belong to the container layer; its removal leaves the underlying image unchanged. [Docker container layers](https://docs.docker.com/engine/storage/drivers/#container-and-layers), [mount persistence](https://docs.docker.com/engine/storage/).

**Human alternative / rollback:** `docker cp ./baseline-app-index.html edge-victim-app:/usr/share/nginx/html/index.html`, using the recorded intended content and restoring its original mode/ownership. No NGINX configuration change is required solely to restore a static file. Docker copy supports a running or stopped target. [Docker cp](https://docs.docker.com/reference/cli/docker/container/cp/).

**Verification:** confirm file/content restoration, `/api` expected status/body, and continuing frontend `/` health. A healthy `/` alone is explicitly insufficient for this case. Record whether recovery was human-only, reviewer-approved replacement, or unverified. The parent audit reports `card-docker-upstream-content-4xx` already exists in `corpus_v4` while live default retrieval uses v3; validate retrieval reachability and card completeness before adding a duplicate. This is a local routing/coverage finding, not an upstream NGINX fact.

## Human command templates, never automatic `docker_exec` mutations

The paths below are examples for an image-owned file or confirmed content file; choose the actual file identified by the experiment. `<...>` values require the recorded original value/path. The two `docker cp` examples are alternatives for different incidents. Copying a file can also change destination ownership, so verify/restore its recorded ownership and mode. Do not apply `docker cp` to the mounted frontend site as a substitute for restoring its host source. [Docker copy behavior](https://docs.docker.com/reference/cli/docker/container/cp/), [bind-mount persistence](https://docs.docker.com/engine/storage/bind-mounts/).

```text
docker cp ./baseline-mime.types edge-victim:/etc/nginx/mime.types
docker cp ./baseline-app-default.conf edge-victim-app:/etc/nginx/conf.d/default.conf
docker exec --user 0 edge-victim-app chmod 0644 /usr/share/nginx/html/index.html
docker update --pids-limit <original-limit> edge-victim-app
docker unpause edge-victim-app
docker kill --signal=SIGCONT edge-victim-app
```

After file restoration, a **running, confirmed NGINX target** can be checked and reloaded by a human using the following as separate dependent steps. Continue only when the test succeeds. If stopped, restore the file first and start the target; `docker exec` is not a stopped-container validation method. Although `nginx -t` is a configuration check, it opens referenced files and is not assumed to be accepted by the local read-only command allowlist. [NGINX parameters](https://nginx.org/en/docs/switches.html), [NGINX control](https://nginx.org/en/docs/control.html).

```text
docker exec edge-victim-app nginx -t
docker exec edge-victim-app nginx -s reload
```

## Dataset completeness acceptance

For each **actually recorded** unsuccessful experiment in these families, require: experiment/run identifier; target and changed layer/path; observed failure; distinguishing evidence; exact existing tool calls and ordered results; human-only command where needed; original baseline and rollback method; final health/request evidence; recovery status; and any untested conditional fallback. Mark unavailable mutations as capability gaps. Mark recovery as unverified unless its result is recorded. The official sources support the mechanics above; they do not establish local tool implementations, benchmark success, or dataset coverage.

## NemoClaw/OpenShell `Error` recovery blocker (additional reference check)

**Reported local state:** `nemoclaw edge-agent status` describes stopped compute with preserved workspace, `start` receives OpenShell's Stopped-only precondition while its actual phase is `Error`, and the NemoClaw `stop` wrapper says already stopped. The healthy vLLM listener on port 8000 does not establish a healthy sandbox lifecycle. No remote recovery action was performed by this reference task.

**Exact upstream match:** NVIDIA/OpenShell issue [#3308](https://github.com/NVIDIA/OpenShell/issues/3308) reports the same Ready-only stop / Stopped-only start deadlock on 0.0.116. Its initial SSH-disconnect causality was corrected by the reporter, so do not infer that trigger here. NVIDIA's merged [PR #4231](https://github.com/NVIDIA/OpenShell/pull/4231) changes stop to accept infrastructure `Error`, explicitly preserving the sandbox and persistent workspace. The fix is listed in [v0.1.5 release notes](https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.5), released 2026-10-20. These support a lifecycle defect; they do not prove this host's installed versions or condition reason.

**Bounded operator sequence:** first establish versions and the actual OpenShell condition. Use the sandbox's **recorded owning gateway**, adding the documented global `--gateway <owner>` selector if the active gateway is different; do not accidentally operate on a same-named sandbox elsewhere. `openshell status` is a gateway-health query. [OpenShell CLI reference](https://github.com/NVIDIA/OpenShell/blob/main/deploy/man/openshell.1.md).

```text
nemoclaw --version
openshell --version
openshell status
openshell sandbox get edge-agent
openshell sandbox stop edge-agent
openshell sandbox get edge-agent
```

Try that direct stop **once**, rather than looping the NemoClaw shortcut. Continue only if OpenShell actually reports `Stopped` for the same identity:

```text
openshell sandbox start edge-agent
nemoclaw edge-agent status
```

OpenShell v0.1.5 documents infrastructure-Error recovery as repairing the cause, stopping successfully, then starting; the retained workspace survives on backends that support stop/start. A provisioning-timeout condition has a separate cleanup-then-start path, so read the condition rather than generalizing from `Error`. [Versioned sandbox lifecycle documentation](https://docs.nvidia.com/openshell/v0.1.5/how-it-works/sandboxes/overview#stop-and-start-sandboxes).

**If direct stop rejects `Error`:** retain the exact result, condition reason, sandbox identity, and gateway/CLI versions; this confirms that the supported transition is unavailable on the running gateway. A new CLI alone cannot be assumed to change the server precondition. There is no verified legacy force-state CLI remedy in the checked sources. Issue [#3440](https://github.com/NVIDIA/OpenShell/issues/3440) contains a reporter's SQLite/protobuf phase-edit experiment, explicitly discouraged there as a general workaround; it is not a supported recovery procedure and is excluded here.

**Do not treat v0.1.5 as an in-place fix for a managed 0.0.116 installation.** The checked NemoClaw [blueprint](https://raw.githubusercontent.com/NVIDIA/NemoClaw/main/nemoclaw-blueprint/blueprint.yaml) sets both OpenShell minimum and maximum to 0.0.116, and its [installer](https://github.com/NVIDIA/NemoClaw/blob/main/scripts/install-openshell.sh) enforces that pin. NVIDIA's [0.1 upgrade guide](https://docs.nvidia.com/openshell/upgrade/0-1-0) says local 0.0.x installations cannot upgrade in place and their sandboxes require recreation; mixed 0.0.x/0.1 peers are unsupported. An upgrade is therefore a separate compatibility/migration project, not a workspace-preserving command to run under approval to clear only BlackOps `edge-cache` tmpfs.

**After readiness:** verify the same sandbox identity and workspace, then the real in-sandbox inference route and original pipeline. Current NemoClaw documentation keeps OpenShell in charge of stopped-container recovery and explicitly avoids direct Docker fallback; cached older instructions recommending raw `docker start` conflict with that current contract. [NemoClaw stopped-sandbox troubleshooting](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/reference/troubleshooting#sandbox-shows-as-stopped). No sandbox delete, rebuild, database edits, volume operations, gateway replacement, or arbitrary workspace cleanup is proposed by this bounded sequence.
