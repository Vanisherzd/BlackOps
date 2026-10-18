"""Fault-injection harness on two sandbox containers (never touches the Spark host services).

  uv run --no-sync scripts/faults.py setup            create edge-victim (nginx front, 127.0.0.1:8880) + edge-victim-app (upstream)
  uv run --no-sync scripts/faults.py inject <case>    cases: nginx-stopped | bad-config | upstream-down | bad-upstream-name | container-removed
                                                          | crashloop | upstream-paused | upstream-detached | upstream-500
                                                          | inode-exhausted
                                                          blind (tier "blind", 2026-10-14): upstream-oom | upstream-wrong-port
                                                          | api-path-404 | config-empty | upstream-renamed
                                                          blind2 (2026-10-17): upstream-sigkill | front-paused
                                                          | upstream-index-deleted | network-removed | front-include-missing
  uv run --no-sync scripts/faults.py verify           exit 0 when / and /api/ both return 200
  uv run --no-sync scripts/faults.py reset            restore config, start both containers
  uv run --no-sync scripts/faults.py run <case>       inject -> agent.py -> verify   (the end-to-end test)
"""
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONF_DIR = ROOT / "victim" / "conf.d"
# Canonical config lives in the script, not on disk: the agent may rewrite the file in its own style, and the fault
# injection must still produce a broken file afterwards.
GOOD_CONF = """server {
    listen 80;
    server_name _;
    location / { return 200 "ok\\n"; add_header Content-Type text/plain; }
    location /api/ { proxy_pass http://edge-victim-app:80/; proxy_connect_timeout 2s; proxy_read_timeout 5s; }
}
"""
FRONT, APP, NET = "edge-victim", "edge-victim-app", "edge-net"
# The front end's /var/cache/nginx (temp files, created at start) is a small tmpfs volume with few inodes, so inode
# exhaustion can be injected without touching the host's own filesystems. A tmpfs volume empties when no container
# holds it, so a sidecar outside the agent's whitelist holds it: restarting the stack does not clear the fault by
# accident, and the scratch tools reach the volume through the sidecar.
CACHE, CACHE_VOL = "edge-victim-cache", "edge-cache"
CACHE_VOL_CREATE = (f"docker volume create --driver local --opt type=tmpfs --opt device=tmpfs "
                    f"--opt o=size=16m,nr_inodes=256 {CACHE_VOL}")
BAD_CONF = GOOD_CONF.replace('return 200 "ok\\n";', 'return 200 "ok\\n"')  # drop one semicolon -> [emerg]
# A fault the agent has not been drilled on: the upstream name is wrong, so nginx exits with "host not found in
# upstream". Fixing it needs the runbook (or real reasoning), not the docker_start reflex that solves the other cases.
BAD_NAME_CONF = GOOD_CONF.replace("http://edge-victim-app:80/", "http://edge-victim-backend:80/")
# Blind cases: valid config, wrong upstream path (the upstream answers 404 for /v2/).
BAD_PATH_CONF = GOOD_CONF.replace("http://edge-victim-app:80/", "http://edge-victim-app:80/v2/")
# Blind batch 2: a valid-looking config that includes a file the image does not have.
BAD_INCLUDE_CONF = GOOD_CONF.replace("    server_name _;\n", "    server_name _;\n    include /etc/nginx/snippets/proxy.conf;\n")
assert BAD_INCLUDE_CONF != GOOD_CONF
assert BAD_CONF != GOOD_CONF and BAD_NAME_CONF != GOOD_CONF


# Ground truth per case, for scripts/score.py. min_actions is the fewest state-changing calls that fix it; fix_tools
# are the tools those calls use; cause is a regex the final report must match to count as naming the root cause;
# tier separates cases the agent was tuned on ("seen") from ones it was not — every case here has driven a fix, so all
# are "seen" today. A new case stays "unseen" until something is changed because of it.
CASES = {
    "nginx-stopped":     dict(min_actions=1, fix_tools={"docker_start", "docker_restart"}, cause=r"edge-victim\b.*(stop|exit|down|not running)|(stop|exit)[a-z]* .*edge-victim\b", tier="seen"),
    "bad-config":        dict(min_actions=2, fix_tools={"write_config", "docker_restart", "restore_config"}, cause=r"config|semicolon|syntax|emerg|return directive", tier="seen"),
    "upstream-down":     dict(min_actions=1, fix_tools={"docker_start", "docker_restart"}, cause=r"(edge-victim-app|upstream|backend).*(exit|stop|down|not running)", tier="seen"),
    "bad-upstream-name": dict(min_actions=2, fix_tools={"write_config", "docker_restart", "restore_config"}, cause=r"edge-victim-backend|resolv|proxy_pass|upstream name|host not found|wrong (host|name)", tier="seen"),
    "container-removed": dict(min_actions=1, fix_tools={"recreate_stack"}, cause=r"missing|removed|deleted|does not exist|no such container|recreat", tier="seen"),
    "crashloop":         dict(min_actions=2, fix_tools={"write_config", "docker_restart", "restore_config"}, cause=r"config|emerg|syntax|return directive|restart(ing)? loop|crash", tier="seen"),
    "upstream-paused":   dict(min_actions=1, fix_tools={"docker_restart"}, cause=r"paus|frozen|504|timed? ?out", tier="seen"),
    "upstream-detached": dict(min_actions=1, fix_tools={"network_repair"}, cause=r"network|detach|disconnect|edge-net|unreachable", tier="seen"),
    "upstream-500":      dict(min_actions=1, fix_tools={"recreate_stack"}, cause=r"500|recreat|rebuil|upstream.*(config|broken|misconfig)", tier="seen"),
    # 2026-10-13: no runbook covers it on purpose; the knowledge pack has an inode-exhaustion card. Tests whether the
    # agent reaches for the pack and whether it helps.
    # Blind test (2026-10-14): written after the agent was frozen; nothing in the agent, tools, runbooks or prompts was
    # changed because of them. Ground truth written with the injections, before any run.
    "upstream-oom":        dict(min_actions=2, fix_tools={"docker_restart", "recreate_stack"}, cause=r"oom|out of memory|memory|137|killed", tier="blind"),
    "upstream-wrong-port": dict(min_actions=2, fix_tools={"docker_restart", "recreate_stack"}, cause=r"8080|port|listen|refused|upstream.*(config|layer)", tier="blind"),
    "api-path-404":        dict(min_actions=2, fix_tools={"write_config", "docker_restart", "restore_config"}, cause=r"404|/v2|path|proxy_pass", tier="blind"),
    "config-empty":        dict(min_actions=2, fix_tools={"write_config", "docker_restart", "restore_config"}, cause=r"empty|no server|missing.*(config|server)|config.*(empty|blank|missing)", tier="blind"),
    "upstream-renamed":    dict(min_actions=2, fix_tools={"recreate_stack", "docker_start", "docker_restart"}, cause=r"renam|missing|edge-victim-app-old|not found|no such container|resolv", tier="blind"),
    # Blind batch 2 (2026-10-17, after the memory-limit and restore_config tools): written with the agent frozen
    # (logs/blind2-freeze-20261017.txt); nothing in the agent, tools, runbooks or prompts changes because of them.
    "upstream-sigkill":       dict(min_actions=1, fix_tools={"docker_start", "docker_restart"}, cause=r"kill|sigkill|137|stopped|exited|not running", tier="blind2"),
    # scratch-space-full (the volume's bytes filled) was dropped before any run: on tmpfs a directory costs an inode
    # and no data page, so nginx still created its temp directories and the stack stayed 200/200.
    "front-paused":           dict(min_actions=1, fix_tools={"docker_restart"}, cause=r"paus|frozen|freez|unresponsive", tier="blind2"),
    "upstream-index-deleted": dict(min_actions=2, fix_tools={"docker_restart", "recreate_stack"}, cause=r"index|403|html|deleted|missing|layer|forbidden", tier="blind2"),
    "network-removed":        dict(min_actions=1, fix_tools={"network_repair", "docker_start", "docker_restart"}, cause=r"network|edge-net|removed|deleted|detach", tier="blind2"),
    "front-include-missing":  dict(min_actions=2, fix_tools={"write_config", "restore_config", "docker_restart"}, cause=r"include|snippet|proxy\.conf|no such file|config", tier="blind2"),
    "inode-exhausted":   dict(min_actions=2, fix_tools={"clear_scratch_volume", "docker_start", "docker_restart"}, cause=r"inode|scratch|edge-cache|/var/cache/nginx|tiny files|too many files", tier="unseen",
                              diag_tools={"disk_usage", "docker_exec"}),
}
MUTATING = {"docker_start", "docker_restart", "write_config", "recreate_stack", "network_repair", "cleanup_disk", "kill_process",
            "clear_scratch_volume", "restore_config"}
DIAGNOSTIC = {"check_health", "docker_ps", "docker_logs", "read_config", "search_runbooks", "search_incident_knowledge", "http_check"}


def sh(cmd, check=False):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        sys.exit(f"command failed: {cmd}\n{r.stderr}")
    return r


def http(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8880{path}", timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


# One definition of each container, shared with the agent's host-layer repair tools (scripts/agent.py).
SPECS = {
    APP: f"docker run -d --name {APP} --network {NET} --restart no nginx:latest",
    FRONT: (f"docker run -d --name {FRONT} --network {NET} --restart no -p 127.0.0.1:8880:80 "
            f"-v {CONF_DIR}:/etc/nginx/conf.d:ro -v {CACHE_VOL}:/var/cache/nginx nginx:latest"),
    CACHE: f"docker run -d --name {CACHE} --network none --restart no -v {CACHE_VOL}:/cache nginx:latest sleep infinity",
}


def exists(name):
    return bool(sh(f"docker ps -aq --filter name=^{name}$").stdout.strip())


def ensure(name):
    """Create a container that is gone entirely. Start/restart is the agent's job; this is the missing-container case."""
    if name not in SPECS:
        return f"denied: {name!r} is not part of this stack"
    if exists(name):
        return f"{name} already exists"
    if name == FRONT:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        if not (CONF_DIR / "default.conf").exists():
            (CONF_DIR / "default.conf").write_text(GOOD_CONF)
    sh(f"docker network create {NET} 2>/dev/null")
    sh(f"{CACHE_VOL_CREATE} >/dev/null 2>&1")
    r = sh(SPECS[name])
    return f"created {name}" if not r.returncode else f"failed to create {name}: {(r.stderr or r.stdout).strip()[:300]}"


def setup():
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    (CONF_DIR / "default.conf").write_text(GOOD_CONF)   # the file is not tracked by git; drills rewrite it
    record_deployed()
    sh(f"docker network create {NET} 2>/dev/null")
    sh(f"docker rm -f {FRONT} {APP} {CACHE} >/dev/null 2>&1")
    sh(f"docker volume rm {CACHE_VOL} >/dev/null 2>&1")
    sh(CACHE_VOL_CREATE, check=True)
    for name in (CACHE, APP, FRONT):
        sh(SPECS[name], check=True)
    time.sleep(2)
    print("setup:", "/", http("/"), "/api/", http("/api/"))


def record_deployed():
    """The deploy records what it deployed: the agent's restore_config puts this back (scripts/agent.py)."""
    kg = ROOT / "victim" / "known-good"
    kg.mkdir(parents=True, exist_ok=True)
    (kg / "default.conf").write_text(GOOD_CONF)


def reset():
    """Undo every injection this file knows how to make, whatever order the drills ran in."""
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    (CONF_DIR / "default.conf").write_text(GOOD_CONF)
    record_deployed()
    sh(f"docker network create {NET} >/dev/null 2>&1")      # network-removed: connect below needs it to exist
    ensure(CACHE)
    sh(f"docker start {CACHE} >/dev/null 2>&1")
    if exists(f"{APP}-old"):                    # upstream-renamed: drop the renamed one, recreate the real one below
        sh(f"docker rm -f {APP}-old >/dev/null 2>&1")
    if exists(APP) and sh(f"docker inspect -f '{{{{.HostConfig.Memory}}}}' {APP}").stdout.strip() not in ("", "0"):
        sh(f"docker rm -f {APP} >/dev/null 2>&1")    # upstream-oom: a memory limit is only undone by a rebuild
    sh(f"docker exec {CACHE} sh -c 'find /cache -mindepth 1 -maxdepth 1 \\( -name \".sess_*\" -o -name \".blob*\" \\) -delete' >/dev/null 2>&1")
    for name in (APP, FRONT):       # a previous drill may have removed one; start cannot bring back what is gone
        ensure(name)
        sh(f"docker unpause {name} >/dev/null 2>&1")
        sh(f"docker update --restart=no {name} >/dev/null 2>&1")
        sh(f"docker network connect {NET} {name} >/dev/null 2>&1")
    sh(f"docker start {APP} {FRONT} >/dev/null 2>&1")
    sh(f"docker restart -t 1 {APP} {FRONT} >/dev/null 2>&1")
    time.sleep(2)
    if http("/api/") != 200:        # the upstream's own layer may be poisoned (upstream-500): only a rebuild clears it
        sh(f"docker rm -f {APP} >/dev/null 2>&1")
        ensure(APP)
        sh(f"docker restart -t 1 {FRONT} >/dev/null 2>&1")
        time.sleep(2)


def inject(case):
    if case == "nginx-stopped":
        sh(f"docker stop -t 1 {FRONT}", check=True)
    elif case == "bad-config":
        (CONF_DIR / "default.conf").write_text(BAD_CONF)
        sh(f"docker restart -t 1 {FRONT}")  # nginx refuses the config and the container exits
    elif case == "upstream-down":
        sh(f"docker stop -t 1 {APP}", check=True)
    elif case == "container-removed":
        sh(f"docker rm -f {FRONT}", check=True)     # start/restart cannot fix this; the container has to be recreated
    elif case == "crashloop":
        # restart=always + a config nginx rejects: the container comes back every second and dies every second.
        # Restarting it is exactly the wrong move; only fixing the config ends the loop.
        (CONF_DIR / "default.conf").write_text(BAD_CONF)
        sh(f"docker update --restart=always {FRONT}", check=True)
        sh(f"docker restart -t 1 {FRONT}")
    elif case == "upstream-paused":
        sh(f"docker pause {APP}", check=True)         # alive but frozen: /api/ times out (504), it does not refuse (502)
    elif case == "upstream-detached":
        sh(f"docker network disconnect {NET} {APP}", check=True)   # both up, nothing wrong in either log except a timeout
    elif case == "upstream-500":
        # The upstream's own config, inside its writable layer, now returns 500. A restart keeps that layer, so
        # restart does not help; the container has to be recreated from its definition.
        sh(f"docker exec {APP} sh -c \"printf 'server {{ listen 80; return 500; }}\\n' > /etc/nginx/conf.d/default.conf "
           f"&& nginx -s reload\"", check=True)
    elif case == "inode-exhausted":
        # Small session files fill every inode of the front end's temp volume while bytes stay free; the front end
        # then restarts (as after a crash or a deploy) and cannot create its temp directories.
        sh(f"docker exec {CACHE} sh -c 'rm -rf /cache/*_temp; i=0; while touch /cache/.sess_$i 2>/dev/null; "
           f"do i=$((i+1)); done'", check=True)
        sh(f"docker restart -t 1 {FRONT}")
    elif case == "upstream-oom":
        # 8 MB is far below what nginx with a worker per core needs; the kernel kills it on start.
        sh(f"docker update --memory 8m --memory-swap 8m {APP}", check=True)
        sh(f"docker restart -t 1 {APP}")
    elif case == "upstream-wrong-port":
        # The upstream's own config, in its writable layer, moves it to 8080; the front end still proxies to :80.
        sh(f"docker exec {APP} sh -c \"sed -i -E 's/listen( +)80;/listen\\18080;/; s/listen( +)\\[::\\]:80;/listen\\1[::]:8080;/' "
           f"/etc/nginx/conf.d/default.conf && nginx -s reload\"", check=True)
    elif case == "api-path-404":
        (CONF_DIR / "default.conf").write_text(BAD_PATH_CONF)
        sh(f"docker restart -t 1 {FRONT}")
    elif case == "config-empty":
        # nginx starts with no server block: the container is Up, nothing listens, no error in the log.
        (CONF_DIR / "default.conf").write_text("")
        sh(f"docker restart -t 1 {FRONT}")
    elif case == "upstream-renamed":
        sh(f"docker rename {APP} {APP}-old", check=True)
        sh(f"docker restart -t 1 {FRONT}")      # nginx resolves the upstream name at start and exits
    elif case == "upstream-sigkill":
        sh(f"docker kill -s KILL {APP}", check=True)          # exit 137 with no memory limit and no OOM verdict
    elif case == "front-paused":
        sh(f"docker pause {FRONT}", check=True)       # both URLs time out; the state runbook for Paused is about the upstream
    elif case == "upstream-index-deleted":
        # The upstream's own page, in its writable layer: /api/ now gets 403 (directory listing forbidden).
        sh(f"docker exec {APP} rm -f /usr/share/nginx/html/index.html", check=True)
    elif case == "network-removed":
        for name in (FRONT, APP):
            sh(f"docker network disconnect -f {NET} {name}")
        sh(f"docker network rm {NET}", check=True)
    elif case == "front-include-missing":
        (CONF_DIR / "default.conf").write_text(BAD_INCLUDE_CONF)
        sh(f"docker restart -t 1 {FRONT}")
    elif case == "bad-upstream-name":
        (CONF_DIR / "default.conf").write_text(BAD_NAME_CONF)
        sh(f"docker restart -t 1 {FRONT}")  # nginx cannot resolve the name and exits
    else:
        sys.exit(f"unknown case {case}")
    time.sleep(2)
    print(f"injected {case}:", "/", http("/"), "/api/", http("/api/"))


def verify():
    ok = http("/") == 200 and http("/api/") == 200
    print("verify:", "/", http("/"), "/api/", http("/api/"), "->", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "setup":
        setup()
    elif cmd == "reset":
        reset(); verify()
    elif cmd == "inject":
        inject(sys.argv[2])
    elif cmd == "verify":
        sys.exit(0 if verify() else 1)
    elif cmd == "run":
        case = sys.argv[2]
        reset(); inject(case)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "agent.py"), "--case", case])
        ok = verify()
        print(f"RESULT {case}: {'PASS' if ok else 'FAIL'} (agent exit {r.returncode})")
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
