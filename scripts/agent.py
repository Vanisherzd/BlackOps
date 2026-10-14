"""Self-healing decision loop: observe -> retrieve runbooks -> decide (tool calls) -> act -> verify.

  uv run --no-sync scripts/agent.py [--case NAME] [--dry-run] [--max-steps 8]
  env: VLLM_URL (default http://127.0.0.1:8000), SERVED_NAME (edge-agent), AGENT_THINK=0 to disable thinking

Scope is deliberately small: two sandbox containers (see scripts/faults.py). Every action goes through a whitelist;
remediation tools become no-ops with --dry-run. Each step is appended to logs/agent/<timestamp>.jsonl.
"""
import argparse
import json
import os
import pathlib
import glob
import ipaddress
import re
import socket
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from openai import OpenAI

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONF_DIR = (ROOT / "victim" / "conf.d").resolve()
RUNBOOKS = ROOT / "runbooks"
NAMES = {"edge-victim", "edge-victim-app"}
HEALTH = {"front": "http://127.0.0.1:8880/", "api": "http://127.0.0.1:8880/api/"}
EXEC_ALLOW = ("nginx -t", "nginx -T", "cat ", "ls", "ps", "curl", "df", "du", "tail", "head", "grep", "id", "env")
UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,64}$")          # systemd unit / journal identifier

# --- retrieval discipline ----------------------------------------------------------------------------------------
# Telling a model to read the runbook first does not make it read the runbook first: across seven drills it skipped
# retrieval whenever it believed it already knew the answer, and one bad-config run wandered for six minutes because
# of it. So the rule lives in the tool layer. Evidence tools push the matching runbook into the answer, and an action
# refuses once until a runbook has been seen for this incident. An incident ends when health comes back green.
MUTATING = {"docker_start", "docker_restart", "write_config", "recreate_stack", "network_repair", "cleanup_disk",
            "clear_scratch_volume",
            "kill_process"}
SYMPTOM_RE = re.compile(r"emerg|error|fatal|fail|refus|denied|timed out|timeout|cannot|could not|no such|"
                        r"not found|exit(ed)? \(?\d|restarting|paused|oom|killed|no space|\b50[0-9]\b", re.I)
_incident = {"runbook_seen": False, "refusals": 0, "trace": []}
DESTRUCTIVE = {"kill_process", "cleanup_disk", "clear_scratch_volume"}          # plus recreate_stack with replace=true; see reviewed()


def reviewed(name, args):
    """Second-agent approval for an irreversible action. Returns None to proceed, or the refusal text."""
    import reviewer
    ok, reason = reviewer.review(name, args, _incident["trace"], _incident["runbook_seen"])
    if ok:
        return None
    # Every refusal names who refused and the next call. "the change reviewer did not approve" once read as a request
    # for a human: the agent answered "/approve" and the drill ended (2026-10-14).
    return (f"review-rejected: refused by the automatic change reviewer (no human approval exists: do not ask for one or send /approve). "
            f"Reason: {reason}\nNext: make the call the reason names, then call {name} again; if there is none, choose "
            "a less invasive action, or stop and escalate in your final report.")


ACCESS_5XX_RE = re.compile(r'"(?:GET|POST|HEAD) [^"]*" (5\d\d) ')


def symptom_query(text):
    """The lines that look like a complaint, or None when the output carries no symptom worth matching on.

    An nginx access-log line (`"GET / HTTP/1.0" 500 177 "-" "curl"`) is a symptom, but as embedding input it is mostly
    noise: quotes, sizes, user agents. Say what it means instead, in the words the runbook uses.
    """
    lines = [l.strip() for l in (text or "").splitlines() if SYMPTOM_RE.search(l)]
    if not lines:
        return None
    codes = sorted({m.group(1) for l in lines for m in [ACCESS_5XX_RE.search(l)] if m})
    plain = [l for l in lines if not ACCESS_5XX_RE.search(l)]
    query = " ".join(plain[-4:])[:400]
    if codes:
        query = f"the service itself answers HTTP {' '.join(codes)} to requests, container is up. " + query
    return query.strip() or None


def with_health(result):
    """Append the health check to an action's result. The agent that fixed the stack and kept digging for five
    minutes had not run check_health after acting; now the answer arrives with the action."""
    health = {k: http_status(u) for k, u in HEALTH.items()}
    if all(v == 200 for v in health.values()):
        _incident.update(runbook_seen=False, refusals=0, trace=[])
        return f"{result}\nhealth now: {json.dumps(health)} -> both URLs healthy. The incident is resolved: report root cause, actions, final health and stop."
    state = docker_ps()
    hint = ""
    for line in state.splitlines():
        if "Exited" in line or "MISSING" in line or "Restarting" in line:
            hint = f" {line.split(chr(9))[0]} is {line.split(chr(9))[1][:40]}: that is the next thing to fix."
    return f"{result}\nhealth now: {json.dumps(health)} -> still failing.{hint}\ncontainers now:\n{state}"


# A container state is a fact with a known procedure; it does not need a similarity search that can land on the
# runbook with the loudest "Exited" in it (it did: a clean stop retrieved container-crashloop). Log text, which is
# free-form, still goes through retrieval.
STATE_RUNBOOKS = (("MISSING", "container-missing.md"), ("Restarting", "container-crashloop.md"),
                  ("Paused", "upstream-504.md"), ("Exited (137)", "oom-killed.md"), ("Exited (0)", "web-service-down.md"))


def attach_named(result, name):
    body = {n: t for n, _, t in __import__("rag").documents()}.get(name)
    if not body:
        return result
    _incident["runbook_seen"] = True
    return f"{result}\n\n--- matching runbook, read it before you act ---\n## {name}\n{body[:1500]}"


def attach_runbook(result, query):
    """Push the matching procedure into an evidence answer, so the agent cannot act without having seen it.

    Only on a query with a real symptom in it: cosine similarity is positive for anything, so attaching on every
    read would staple an irrelevant procedure to healthy output and teach the agent to ignore the section.
    """
    import rag
    if not query:
        return result
    ranked = rag.rank(query)
    if not ranked or ranked[0][1] <= 0:
        return result
    name = ranked[0][0]
    body = {n: t for n, _, t in rag.documents()}[name]
    _incident["runbook_seen"] = True
    return f"{result}\n\n--- matching runbook, read it before you act ---\n## {name}\n{body[:1500]}" + knowledge_note(query)


# --- host layer -------------------------------------------------------------------------------------------------
# What this agent may touch outside the two containers. The rule behind the list: only things this project created.
# Deliberately absent, and why:
#   docker.service / containerd  - the inference server runs as `docker run --rm`, so bouncing the daemon deletes the
#                                  model this agent thinks with. A stopped daemon is unrecoverable from in here by
#                                  construction; report it and escalate.
#   any system unit              - sudo on this host needs a password, and no password belongs in this repo.
#   images and volumes           - shared with the rest of the lab; the agent reports pressure, a human decides.
# logs/agent/*.jsonl is the incident audit trail and is deliberately not cleanable: an ops agent does not delete
# the record of what it did.
# An allowlist of service output that can be regenerated, not "logs/*.log": a pattern over a directory also catches
# whatever is written there later, and it did twice (drill logs on 2026-10-14 01:37, the first A/B's logs at 12:29).
CLEANUP_GLOBS = (str(ROOT / "logs" / "*.out"), str(ROOT / "logs" / "trt-serve.log"), "/tmp/edge-agent-*")
CLEANUP_MIN_AGE_S = 600        # never delete a file something is probably still writing
# Evidence is not a log to rotate. On 2026-10-14 an inode-exhaustion drill ran cleanup_disk (the host's / really is at
# 94 %, so it looked justified) and deleted every earlier drills-*.log, the raw record behind docs/scorecard.md.
EVIDENCE_GLOBS = (str(ROOT / "logs" / "drills-*.log"), str(ROOT / "logs" / "ab-*.log"), str(ROOT / "logs" / "oai-shim.log"),
                  str(ROOT / "logs" / "ops-api.log"), str(ROOT / "logs" / "reviews.jsonl"))   # belt and braces


def cleanup_candidates(now=None):
    """(path, size) of the files cleanup_disk would delete, and how many it skips because a process holds them."""
    now = now or time.time()
    keep = {os.path.realpath(f) for pattern in EVIDENCE_GLOBS for f in glob.glob(pattern)}
    open_files, victims, skipped = held_open(), [], 0
    for pattern in CLEANUP_GLOBS:
        for f in glob.glob(pattern):
            if not os.path.isfile(f) or now - os.path.getmtime(f) <= CLEANUP_MIN_AGE_S or os.path.realpath(f) in keep:
                continue
            if os.path.realpath(f) in open_files:   # a live service still writes here; unlinking it loses the log
                skipped += 1
                continue
            victims.append((f, os.path.getsize(f)))
    return victims, skipped
PROTECTED_CMD = re.compile(r"sshd?\b|systemd|dockerd|containerd|trtllm|tensorrt|openclaw|openshell|nemoclaw|"
                           r"ops_api|oai_shim|uv run|/init\b", re.I)


def docker_ps():
    """State of this stack's containers, naming the ones that are absent.

    `docker ps -a` simply omits a container that was removed, so its absence is invisible next to a healthy sibling.
    An agent then re-reads the same output looking for the difference. Say it outright instead.
    """
    listed = dict(line.split("\t", 1) for line in
                  sh("docker ps -a --filter name=edge-victim --format '{{.Names}}\t{{.Status}}'").splitlines()
                  if "\t" in line)
    return "\n".join(f"{n}\t{listed.get(n, 'MISSING (no such container: it was removed, not stopped)')}"
                      for n in sorted(NAMES))


def held_open():
    """Realpaths this user's processes currently have open. Deleting one frees no space until the writer exits, and
    the service keeps logging into a file nobody can read again."""
    out = set()
    for fd in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            out.add(os.path.realpath(fd))
        except OSError:
            pass
    return out


def local_target(host):
    """Loopback and RFC1918 only: these tools run on the host for anyone who can reach the ops API, so they must not
    become a probe into the rest of the network (or a cloud metadata endpoint)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"cannot resolve {host!r}"
    for *_, addr in infos:
        ip = ipaddress.ip_address(addr[0])
        if not (ip.is_loopback or ip.is_private) or ip.is_link_local:
            return f"denied: {host} resolves to {ip}, outside loopback and the private ranges"
    return None
BASE = os.environ.get("VLLM_URL", "http://127.0.0.1:8000")
MODEL = os.environ.get("SERVED_NAME", "edge-agent")
THINK = os.environ.get("AGENT_THINK", "1") == "1"

SYSTEM = f"""You are an autonomous IT operations agent keeping a small web stack healthy.
Environment: docker container `edge-victim` (nginx front-end, health URLs {HEALTH['front']} and {HEALTH['api']}) and
`edge-victim-app` (upstream behind /api/). The nginx config lives on the host at victim/conf.d/default.conf.
Host-level read-only diagnostics are also available (disk_usage, service_status, journal_tail, port_check, http_check,
top_processes) for faults that are not inside the containers.
Rules: gather evidence first (health, container states, logs, config test), consult runbooks, then apply the least
invasive fix, then verify with check_health. Never touch anything outside these two containers and that config
directory. Call `finish` once both health URLs return 200 or when you cannot fix it (status=escalate)."""

TOOLS = [
    {"name": "check_health", "description": "HTTP status of the front-end and /api/ health URLs", "parameters": {"type": "object", "properties": {}}},
    {"name": "docker_ps", "description": "State of the two containers (docker ps -a)", "parameters": {"type": "object", "properties": {}}},
    {"name": "docker_logs", "description": "Last lines of a container's logs", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "tail": {"type": "integer", "default": 40}}, "required": ["name"]}},
    {"name": "docker_exec", "description": "Run a read-only diagnostic command inside a running container (nginx -t, cat, ls, ps, curl, df, du, tail, head, grep)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "cmd": {"type": "string"}}, "required": ["name", "cmd"]}},
    {"name": "docker_start", "description": "Start a stopped container", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "docker_restart", "description": "Restart a container (needed after a config change)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "read_config", "description": "Read victim/conf.d/default.conf", "parameters": {"type": "object", "properties": {}}},
    {"name": "write_config", "description": "Replace the nginx config with the given full content. The file lives on the host and is mounted read-only into edge-victim, so this tool is the only way to change it (the container's own 'read-only file system' warning is expected). docker_restart edge-victim afterwards to apply", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "search_runbooks", "description": "Search the IT runbooks for a symptom in your own words or with the exact error text (502 upstream, nginx emerg config, disk full, exit 137, crash loop); returns the best matching procedures, or the catalogue if nothing matches", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "search_incident_knowledge", "description": "Offline knowledge base of 1,644 historical incidents and 17 Linux/Kubernetes remediation cards (disk full, inode exhaustion, systemd restart loop, OOM, DNS resolver, TLS expiry, port not listening, CrashLoopBackOff). Use it for a symptom no runbook covers. Answers 'matched' with checks and fixes to verify on this host, or 'insufficient', which means gather more evidence or escalate; never repair from a weak candidate", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "symptom or exact error text"}, "scenario": {"type": "string", "enum": ["disk_full", "crashloop", "dns"]}}, "required": ["query"]}},
    {"name": "disk_usage", "description": "Disk usage: this stack's scratch volume, the host filesystems, and docker; use for disk-full symptoms", "parameters": {"type": "object", "properties": {"inodes": {"type": "boolean", "default": False, "description": "also report inode usage per filesystem (df -i)"}}}},
    {"name": "service_status", "description": "Whether a host systemd unit is active, plus its last status line (read-only)", "parameters": {"type": "object", "properties": {"unit": {"type": "string"}}, "required": ["unit"]}},
    {"name": "journal_tail", "description": "Last lines of a host systemd unit's journal", "parameters": {"type": "object", "properties": {"unit": {"type": "string"}, "lines": {"type": "integer", "default": 30}}, "required": ["unit"]}},
    {"name": "port_check", "description": "Whether a TCP port accepts connections", "parameters": {"type": "object", "properties": {"host": {"type": "string", "default": "127.0.0.1"}, "port": {"type": "integer"}}, "required": ["port"]}},
    {"name": "http_check", "description": "GET an http(s) URL on this host or the private network and return the status code and the first bytes of the body", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "top_processes", "description": "Top host processes by CPU with memory usage", "parameters": {"type": "object", "properties": {}}},
    {"name": "recreate_stack", "description": "Recreate a container of this stack from its definition: one that no longer exists at all, or with replace=true one whose own state is corrupt and survives a restart (e.g. a broken config inside the upstream). Start/restart first if it merely exited", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "replace": {"type": "boolean", "default": False}}}},
    {"name": "network_repair", "description": "Make sure the stack network exists and both containers are attached to it; use when the front end cannot resolve or reach the upstream", "parameters": {"type": "object", "properties": {}}},
    {"name": "cleanup_disk", "description": "Free space by deleting this project's own old service output (*.out, the inference server's startup log); never images, volumes, records, or anyone else's data", "parameters": {"type": "object", "properties": {}}},
    {"name": "clear_scratch_volume", "description": "Delete every file in this stack's scratch volume edge-cache (mounted as /var/cache/nginx in edge-victim: nginx's temp files). Its contents are disposable; nginx recreates its temp directories when it starts", "parameters": {"type": "object", "properties": {}}},
    {"name": "kill_process", "description": "Terminate a runaway host process by pid; only processes owned by this user and not part of the platform itself", "parameters": {"type": "object", "properties": {"pid": {"type": "integer"}, "force": {"type": "boolean", "default": False}}, "required": ["pid"]}},
    {"name": "finish", "description": "End the run with a verdict", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["resolved", "unresolved", "escalate"]}, "root_cause": {"type": "string"}, "actions": {"type": "string"}}, "required": ["status", "root_cause", "actions"]}},
]


def sh(cmd, timeout=30, limit=4000):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout + r.stderr).strip()
    if not out:
        return f"(exit {r.returncode}, no output)"
    # Keep the tail, not the head. A crash-looping nginx printed ten lines of entrypoint chatter per restart on
    # stdout and its one [emerg] on stderr; head truncation dropped the only line that mattered, three drills in a row.
    return out if len(out) <= limit else "...(earlier output cut)\n" + out[-limit:]


def collapse(text):
    """Fold lines that repeat into one line with a count, keeping first-seen order. A restart loop turns 100 lines of
    the same entrypoint chatter into a dozen, and the error that recurs every cycle becomes the loudest line."""
    seen, order = {}, []
    for line in text.splitlines():
        key = line.strip()
        if key in seen:
            seen[key] += 1
        else:
            seen[key] = 1
            order.append(line)
    return "\n".join(f"{l}   (x{seen[l.strip()]})" if seen[l.strip()] > 1 else l for l in order)


def http_status(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"unreachable ({type(e).__name__})"


def search_runbooks(query):
    """Retrieval lives in scripts/rag.py: hybrid keyword + dense, with its own fallback when the index is absent."""
    import rag
    return rag.search(query) + knowledge_note(query)


KB_DB = ROOT / "kb" / "artifacts" / "blackout_ops_kb.sqlite"
# v3 (2026-10-13, docs/kb-retrieval-v2.md): cases indexed by their own incident's telemetry, word-boundary IDF
# scoring, no substring `matched`. On the held-out benchmark it turned retrieval from harmful (50 answers broken, 16
# fixed) to helpful (16 broken, 53 fixed). KB_RETRIEVAL=v1 restores the pack as shipped.
KB_V3 = ROOT / "kb" / "artifacts" / "corpus_v3.jsonl"
_kb = None


def knowledge_base():
    """The pack, opened once: v3 unless KB_RETRIEVAL=v1."""
    global _kb
    if _kb is None:
        sys.path.insert(0, str(ROOT / "kb" / "src"))
        if os.environ.get("KB_RETRIEVAL", "v3") == "v3" and KB_V3.exists():
            from blackout_ops_kb.retrieval_v2 import AGENT_DEFAULTS, KnowledgeBaseV2
            _kb = KnowledgeBaseV2(KB_V3, **AGENT_DEFAULTS)
        else:
            from blackout_ops_kb import KnowledgeBase
            _kb = KnowledgeBase(KB_DB)
    return _kb


def knowledge_note(query):
    """A knowledge card pushed next to a runbook, only when the pack is confident (v3: one of the card's own symptom
    phrases is in the query whole and specific). Pulling it was left to the model and never happened: the skill said
    to ask the pack when runbook search returns the catalogue, and dense search never does ("printer toner low"
    returns disk-full.md), so in every drill the pack went unread. A score floor to detect "no runbook covers this"
    was measured and does not separate (covered 0.66-0.88, uncovered up to 0.73), so the card rides along instead.
    Measured on 24 uncovered symptoms: 6 cards pushed, 5 right, 14 correct silences, 4 missed; on 38 covered ones 3
    cards, each consistent with the runbook. Never counts as a runbook: the gate is unchanged."""
    try:
        kb = knowledge_base()
        if type(kb).__name__ != "KnowledgeBaseV2" or not query:
            return ""                                      # v1's `matched` was wrong 20 times in 21; push nothing
        b = kb.retrieve({"query": query}, case_k=0, solution_k=3)
    except Exception:                                      # the note is an extra; it must never break the tool
        return ""
    if b["knowledge_match"]["status"] != "matched" or not b["solutions"]:
        return ""
    s = b["solutions"][0]
    m = s.get("metadata") or {}
    lines = [f"\n\n--- knowledge card (from other systems' incidents: a hypothesis to confirm here, not this stack's "
             f"procedure) ---\n{s['id']}: {m.get('title', s.get('title', ''))} [matched on: {' '.join(s.get('matched_terms') or [])}]"]
    lines += [f"CHECK {c.get('command', '')} :: {c.get('purpose', '')}" for c in m.get("checks", [])[:3]]
    lines += [f"FIX {f.get('action', '')}" for f in m.get("remediation", [])[:2]]
    lines.append("If the runbook above does not fit what you see, test the card's CHECK lines with your read-only tools.")
    return "\n".join(lines)[:1200]


def search_incident_knowledge(query, scenario=None):
    """A teammate's offline knowledge pack (kb/): historical cases from four public AIOps benchmarks plus curated
    Linux/Kubernetes remediation cards in SQLite FTS5, stdlib only. Its verdict is kept (`matched` = a card covers
    the symptom, `insufficient` = nothing does, so no repair may be derived from the weak candidates) but its size
    is not: the raw bundle is 14-24 KB of JSON and the model has a 32k window, so only the lines the agent can act
    on come back. It never counts as having read this stack's runbook; the gate still wants search_runbooks."""
    try:
        kb = knowledge_base()
        incident = {"query": query, "scenario": scenario} if scenario in ("disk_full", "crashloop", "dns") else {"query": query}
        bundle = kb.retrieve(incident, case_k=3, solution_k=3)
    except Exception as e:
        return f"knowledge base unavailable ({type(e).__name__}: {str(e)[:100]}); use search_runbooks"
    km = bundle["knowledge_match"]
    lines = [f"knowledge base ({bundle['scenario']}): {km['status']}, best card coverage {km['best_solution_coverage']:.2f}"]
    for s in bundle["solutions"][:2]:            # two cards: the third is mostly noise on a 33-term query
        m = s.get("metadata") or {}
        lines.append(f"\n## {s['id']}: {m.get('title', s.get('title', ''))} [{m.get('platform', '?')}, risk {s.get('risk_tier')}, coverage {s['match_coverage']:.2f}]")
        if m.get("symptoms"):
            lines.append("symptoms: " + "; ".join(m["symptoms"][:2]))
        for c in m.get("checks", [])[:3]:
            lines.append(f"CHECK [{c.get('level', 'R')}] {c.get('command', '')} :: {c.get('purpose', '')}")
        for f in m.get("remediation", [])[:2]:
            lines.append(f"FIX [{f.get('level', 'W')}] {f.get('action', '')}")
        if m.get("verify"):
            lines.append("verify: " + str(m["verify"][0]))
    if km["status"] != "matched":
        weak = ", ".join(f"{s['id']} ({s['match_coverage']:.2f})" for s in bundle["weak_solution_candidates"][:3]) or "none"
        lines.append(f"no remediation card covers this symptom; weak candidates ({weak}) are not a basis for repair. "
                     "Use search_runbooks for this stack's own procedures, gather more read-only evidence, or escalate.")
    titles = list(dict.fromkeys(str(c.get("title", ""))[:60] for c in bundle["cases"][:3]))
    if titles:
        lines.append("\nsimilar historical cases: " + "; ".join(titles))
    lines.append("These are hypotheses from other systems' incidents: confirm against this host's evidence before acting.")
    out = "\n".join(lines)
    return out if len(out) <= 2500 else out[:2500] + "\n...(cut)"


def trace_summary(name, result):
    """What the incident trace (the reviewer's evidence) keeps of a result. A blind first-300-characters cut kept nginx's
    startup notices and dropped the one [emerg] line at the end of a log, and the reviewer then judged an inode
    exhaustion "a connectivity issue": logs keep their end (without the attached runbook), other results their head
    and, when long, their tail, where the appended health check is."""
    text = str(result)
    if name == "docker_logs":
        body = text.split("\n\n--- matching runbook")[0]
        text = body if len(body) <= 300 else "… " + body[-300:]
    elif len(text) > 450:
        text = text[:300] + " … " + text[-150:]
    return text[:460].replace("\n", " ")


def run_tool(name, args, dry_run):
    """Dispatch one tool call and record it in the incident trace (the reviewer's evidence)."""
    result = _run_tool(name, args, dry_run)
    try:
        shown = {k: (v if len(str(v)) < 80 else str(v)[:77] + "...") for k, v in (args or {}).items()} if isinstance(args, dict) else str(args)[:80]
        _incident["trace"].append(dict(tool=name, args=json.dumps(shown, ensure_ascii=False), result=trace_summary(name, result)))
        del _incident["trace"][:-30]
    except Exception:
        pass
    return result


def _run_tool(name, args, dry_run):
    if isinstance(args, str):            # some clients send arguments as a JSON string
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return f"bad arguments (not JSON): {args[:200]}"
    args = args or {}
    if "name" in args and args["name"] is None:
        args.pop("name")
    for alias in ("container", "container_name", "service"):   # small models / meta-tool layers rename things
        if alias in args and "name" not in args:
            args["name"] = args.pop(alias)
    c = args.get("content")
    if isinstance(c, str) and "\n" not in c and "\\n" in c:   # double-escaped JSON: literal backslash-n, no real newlines
        args["content"] = c.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    if name in ("docker_logs", "docker_exec", "docker_start", "docker_restart") and "name" not in args:
        return f"error: {name} needs args.name (one of {sorted(NAMES)})"
    if name == "docker_exec" and "cmd" not in args:
        return "error: docker_exec needs args.cmd"
    if name == "write_config" and "content" not in args:
        return "error: write_config needs args.content (the full file)"
    if name in ("search_runbooks", "search_incident_knowledge") and "query" not in args:
        return f"error: {name} needs args.query"
    for alias in ("service", "unit_name", "systemd_unit"):         # small models rename this one too
        if alias in args and "unit" not in args:
            args["unit"] = args.pop(alias)
    if name in ("service_status", "journal_tail") and "unit" not in args:
        return f"error: {name} needs args.unit (a systemd unit name, e.g. docker)"
    if name == "port_check" and "port" not in args:
        return "error: port_check needs args.port"
    if name == "http_check" and "url" not in args:
        return "error: http_check needs args.url"
    if name == "kill_process" and "pid" not in args:
        return "error: kill_process needs args.pid (from top_processes)"

    if name in MUTATING and not _incident["runbook_seen"]:
        # The agent is about to act without having read any evidence, so we do not know the symptom either and
        # cannot pick the procedure for it. Send it back for the symptom. Bounded, so a stubborn model cannot
        # deadlock: after two refusals the action goes through and the log shows it did.
        _incident["refusals"] += 1
        if _incident["refusals"] <= 2:
            import rag
            catalogue = "\n".join(f"- {n}: {t}" for n, t, _ in rag.documents())
            return (f"refused: (automatic rule) no runbook consulted yet for this incident, so {name} is premature. "
                    'Next: call docker_logs {"name": "<the failing container>"} (it attaches the matching runbook), or '
                    'search_runbooks {"query": "<the error text>"}; then call ' + name + " again. Runbooks available:\n"
                    + catalogue)

    def guard(n):
        if n not in NAMES:
            return f"denied: container {n!r} is outside the allowed set {sorted(NAMES)}. Next: the same call with one of those names."
    if name == "check_health":
        health = {k: http_status(u) for k, u in HEALTH.items()}
        if all(v == 200 for v in health.values()):
            _incident.update(runbook_seen=False, refusals=0, trace=[])   # incident over; the next one starts clean
        return json.dumps(health)
    if name == "docker_ps":
        out = docker_ps()
        for marker, runbook in STATE_RUNBOOKS:
            if marker in out:
                return attach_named(out, runbook)
        return out
    if name == "docker_logs":
        denied = guard(args["name"])
        if denied:
            return denied
        out = collapse(sh(f"docker logs --tail {int(args.get('tail', 40))} {args['name']} 2>&1", limit=6000))
        if "read-only file system" in out:
            out += ("\n\nnote: the entrypoint's 'can not modify /etc/nginx/conf.d/default.conf (read-only file "
                    "system?)' is expected and harmless. That file is a host file mounted read-only into the "
                    "container; write_config changes it on the host, then docker_restart applies it.")
        query = symptom_query(out)
        if not query:
            state = next((l for l in docker_ps().splitlines() if l.startswith(args["name"] + "\t")), "")
            for marker, runbook in STATE_RUNBOOKS:
                if marker in state:
                    # nothing wrong in the log and the process is gone: say so, or the agent goes looking for an
                    # error that does not exist while the gate keeps refusing the start it should have done.
                    out += f"\n\nno error in this log; the container is {state.split(chr(9))[1][:30]}."
                    return attach_named(out, runbook)
        return attach_runbook(out, query)
    if name == "docker_exec":
        cmd = args["cmd"].strip()
        if not cmd.startswith(EXEC_ALLOW) or any(t in cmd for t in (";", "&&", "|", ">", "`", "$(")):
            return ("denied: docker_exec runs one read-only command (nginx -t, cat, ls, ps, curl, df, du, tail, head, "
                    "grep, id, env) with no ; && | > ` or $(. Next: one such command, e.g. "
                    '{"name": "edge-victim-app", "cmd": "df -h /"}; to change state use the action tools '
                    "(write_config, docker_start/docker_restart, network_repair, recreate_stack, clear_scratch_volume).")
        return guard(args["name"]) or sh(f"docker exec {args['name']} sh -c {json.dumps(cmd)}")
    if name in ("docker_start", "docker_restart"):
        d = guard(args["name"])
        if d:
            return d
        if args["name"] == "edge-victim":
            import faults
            app_nets = sh(f"docker inspect edge-victim-app --format '{{{{json .NetworkSettings.Networks}}}}'")
            if faults.NET not in app_nets:
                return (f"denied: edge-victim-app is not on the {faults.NET} network right now, so nginx would fail to "
                        "resolve it at startup and exit. Run network_repair first, then start/restart edge-victim.")
        if dry_run:
            return f"DRY-RUN: would run docker {name.split('_')[1]} {args['name']}"
        out = sh(f"docker {name.split('_')[1]} -t 5 {args['name']}" if name == "docker_restart" else f"docker start {args['name']}")
        time.sleep(2)
        return with_health(out + "\n" + docker_ps())
    if name == "read_config":
        return (CONF_DIR / "default.conf").read_text()
    if name == "write_config":
        content = args["content"]
        if len(content) > 20000 or "proxy_pass" not in content:
            return ("denied: config must stay under 20 KB and keep the /api/ proxy_pass. Next: read_config, keep its "
                    "proxy_pass line, and write_config the complete corrected file.")
        if dry_run:
            return "DRY-RUN: would write default.conf"
        (CONF_DIR / "default.conf").write_text(content)
        return "written, but not applied: nginx only reads it on start. Call docker_restart {\"name\": \"edge-victim\"} now."
    if name == "search_runbooks":
        _incident["runbook_seen"] = True
        return search_runbooks(args["query"])
    if name == "search_incident_knowledge":
        return search_incident_knowledge(str(args["query"]), args.get("scenario"))
    if name == "disk_usage":
        # The scratch volume comes first: the incident trace keeps 300 characters of a result, and the reviewer's
        # precondition for clearing it is read from there.
        import faults
        # OPS_INODES=on-request (the knowledge A/B, docs/kb-retrieval-v2.md): inode figures only when asked for, so
        # whether the agent thinks of inodes is part of what is measured. Default: always shown.
        inodes = os.environ.get("OPS_INODES", "always") != "on-request" or bool(args.get("inodes"))
        vol = sh(f"docker exec {faults.CACHE} sh -c 'df -h /cache | tail -1; df -i /cache | tail -1'")
        lines = vol.strip().splitlines()
        if len(lines) == 2 and len(lines[1].split()) >= 5:
            scratch = f"scratch volume edge-cache (/var/cache/nginx in edge-victim): space {lines[0].split()[4]}"
            scratch += f", inodes {lines[1].split()[4]}" if inodes else ""
        else:
            scratch = f"scratch volume edge-cache: unreadable ({vol.strip()[:120]})"
        return (scratch + "\n\nhost, space:\n" + sh("df -h -x tmpfs -x devtmpfs -x efivarfs | head -12")
                + ("\nhost, inodes:\n" + sh("df -i -x tmpfs -x devtmpfs -x efivarfs | head -12") if inodes else "")
                + "\n" + sh("docker system df"))
    if name in ("service_status", "journal_tail"):
        unit = str(args["unit"]).strip()
        if not UNIT_RE.match(unit):
            return f"denied: {unit!r} is not a plain unit name"
        if name == "service_status":
            return sh(f"systemctl is-active {unit}; systemctl status {unit} --no-pager -n 0 | head -6")
        return sh(f"journalctl -u {unit} -n {max(1, min(int(args.get('lines', 30)), 200))} --no-pager")
    if name == "port_check":
        host, port = str(args.get("host") or "127.0.0.1"), int(args["port"])
        denied = local_target(host)
        if denied:
            return denied
        try:
            with socket.create_connection((host, port), timeout=3):
                return f"{host}:{port} open"
        except Exception as e:
            return f"{host}:{port} closed ({type(e).__name__})"
    if name == "http_check":
        url = str(args["url"])
        if not url.startswith(("http://", "https://")):
            return "denied: url must be http(s)"
        denied = local_target(urllib.parse.urlsplit(url).hostname or "")
        if denied:
            return denied
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return f"{r.status}\n{r.read(600).decode(errors='replace')}"
        except urllib.error.HTTPError as e:
            return f"{e.code}\n{e.read(600).decode(errors='replace')}"
        except Exception as e:
            return f"unreachable ({type(e).__name__})"
    if name == "top_processes":
        return sh("ps -eo pid,comm,pcpu,pmem,rss --sort=-pcpu | head -16")
    if name == "recreate_stack":
        import faults                                   # one definition of the containers, shared with the harness
        targets = [args["name"]] if args.get("name") else sorted(faults.SPECS)
        for t in targets:
            if t not in faults.SPECS:
                return f"denied: {t!r} is not part of this stack"
        if args.get("replace") and not args.get("name"):
            return ("denied: replace=true needs an explicit name; it destroys that container and builds it again. "
                    'Next: recreate_stack {"name": "edge-victim-app", "replace": true}.')
        if args.get("replace") and args.get("name") == "edge-victim":
            return ("denied: edge-victim's nginx config is a host file mounted into the container; rebuilding the "
                    "container keeps the same file and the same fault. Fix the file with write_config, then "
                    "docker_restart edge-victim.")
        if dry_run:
            return f"DRY-RUN: would recreate {targets}" + (" (replacing)" if args.get("replace") else "")
        if args.get("replace"):
            # Known precondition, checked in code: a rebuild is never a first move. The runbook says restart first,
            # and the reviewer once approved a premature rebuild because "the runbook was consulted".
            if not any(t["tool"] == "docker_restart" and args["name"] in t["args"] for t in _incident["trace"]):
                return (f"denied: recreate_stack replace=true on {args['name']} before it was restarted in this incident. "
                        f"docker_restart it first; rebuild only if it still fails afterwards.")
            refusal = reviewed(name, {"name": args["name"], "replace": True})
            if refusal:
                return refusal
        if args.get("replace"):
            sh(f"docker rm -f {args['name']}")       # stateless by definition (SPECS); the config is a host file
        out = [faults.ensure(t) for t in targets]
        time.sleep(2)
        return with_health("\n".join(out) + "\n" + docker_ps())
    if name == "network_repair":
        import faults
        if dry_run:
            return "DRY-RUN: would attach both containers to the stack network"
        sh(f"docker network create {faults.NET} 2>/dev/null")
        out = []
        for c in sorted(NAMES):
            joined = faults.NET in sh(f"docker inspect {c} --format '{{{{json .NetworkSettings.Networks}}}}'")
            out.append(f"{c}: already on {faults.NET}" if joined
                       else f"{c}: " + (sh(f"docker network connect {faults.NET} {c}") or f"attached to {faults.NET}"))
        time.sleep(1)
        return with_health("\n".join(out))
    if name == "cleanup_disk":
        victims, skipped = cleanup_candidates()
        freed = sum(n for _, n in victims)
        if dry_run:
            return f"DRY-RUN: would delete {len(victims)} project log file(s), {freed / 1e6:.1f} MB"
        refusal = reviewed(name, {"files": len(victims), "mb": round(freed / 1e6, 1)})
        if refusal:
            return refusal
        for f, _ in victims:
            try:
                os.remove(f)
            except OSError as e:
                return f"stopped after an error on {f}: {e}"
        usage = shutil.disk_usage("/")
        return (f"deleted {len(victims)} project log file(s), freed {freed / 1e6:.1f} MB "
                f"(skipped {skipped} still held open by a running service); "
                f"/ is now {100 * usage.used / usage.total:.0f}% full with {usage.free / 1e9:.0f} GB free. "
                "Images and volumes were not touched: report disk pressure instead of deleting shared data.")
    if name == "clear_scratch_volume":
        import faults
        if dry_run:
            return "DRY-RUN: would delete every file in the scratch volume edge-cache"
        refusal = reviewed(name, {})
        if refusal:
            return refusal
        out = sh(f"docker exec {faults.CACHE} sh -c 'n=$(find /cache -mindepth 1 | wc -l); "
                 f"find /cache -mindepth 1 -delete; echo deleted $n entries; df -i /cache | tail -1'")
        return with_health(out.strip() + "\nThe front end creates its temp directories when it starts; start it if it is down.")
    if name == "kill_process":
        pid = int(args["pid"])
        if pid in (os.getpid(), os.getppid(), 1):
            return "denied: that pid is this agent or init. Next: top_processes, and pick the runaway pid it lists."
        info = sh(f"ps -o uid=,args= -p {pid}").strip()
        if not info or "exit " in info:
            return f"no such process: {pid}"
        uid, cmd = info.split(None, 1)
        if int(uid) != os.getuid():
            return f"denied: pid {pid} belongs to uid {uid}, not this agent. Next: report it in your final report; it is not yours to kill."
        if PROTECTED_CMD.search(cmd):
            return f"denied: pid {pid} is part of the platform ({cmd[:120]}). Next: report it; the platform is not killed from here."
        if dry_run:
            return f"DRY-RUN: would signal {pid} ({cmd[:80]})"
        refusal = reviewed(name, {"pid": pid, "cmd": cmd[:80], "force": bool(args.get("force"))})
        if refusal:
            return refusal
        sig = "-9" if args.get("force") else "-15"
        sh(f"kill {sig} {pid}")
        for _ in range(6):                       # SIGTERM is a request; give the process a moment to honour it
            time.sleep(0.5)
            if not sh(f"ps -p {pid} -o pid=").strip():
                return f"signalled {pid} with {sig} ({cmd[:80]}); gone"
        return (f"signalled {pid} with {sig} ({cmd[:80]}); still running after 3s"
                + ("" if args.get("force") else " — call again with force=true to send SIGKILL"))
    return f"unknown tool {name}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-steps", type=int, default=8)
    a = ap.parse_args()
    client = OpenAI(base_url=f"{BASE}/v1", api_key="none", timeout=300)
    log_dir = ROOT / "logs" / "agent"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{a.case or 'adhoc'}.jsonl").open("w")
    trace = lambda **kw: (log.write(json.dumps(kw, ensure_ascii=False) + "\n"), log.flush())

    observation = json.dumps({"health": {k: http_status(u) for k, u in HEALTH.items()},
                              "containers": sh("docker ps -a --filter name=edge-victim --format '{{.Names}}\t{{.Status}}'")})
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Alert: health check degraded. Initial observation: {observation}\nDiagnose and repair."}]
    trace(step=0, observation=observation)
    tools = [{"type": "function", "function": t} for t in TOOLS]
    verdict = None
    for step in range(1, a.max_steps + 1):
        t0 = time.time()
        r = client.chat.completions.create(model=MODEL, messages=messages, tools=tools, tool_choice="auto",
                                           temperature=0, max_tokens=1500,
                                           extra_body={"chat_template_kwargs": {"enable_thinking": THINK}})
        m = r.choices[0].message
        reasoning = getattr(m, "reasoning_content", None) or (m.model_extra or {}).get("reasoning_content")
        calls = m.tool_calls or []
        messages.append({"role": "assistant", "content": m.content or "", "tool_calls": [c.model_dump() for c in calls]} if calls
                        else {"role": "assistant", "content": m.content or ""})
        if not calls:
            trace(step=step, latency=round(time.time() - t0, 1), reasoning=(reasoning or "")[:2000], text=m.content)
            messages.append({"role": "user", "content": "Use a tool, or call finish."})
            continue
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if c.function.name == "finish":
                verdict = args
                trace(step=step, latency=round(time.time() - t0, 1), reasoning=(reasoning or "")[:2000], finish=args)
                break
            result = run_tool(c.function.name, args, a.dry_run)
            trace(step=step, latency=round(time.time() - t0, 1), reasoning=(reasoning or "")[:2000],
                  tool=c.function.name, args=args, result=result[:2000])
            print(f"[{step}] {c.function.name}({json.dumps(args)[:120]}) -> {result[:160]!r}")
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})
        if verdict:
            break
    final = {k: http_status(u) for k, u in HEALTH.items()}
    trace(step="final", health=final, verdict=verdict)
    print("verdict:", json.dumps(verdict, ensure_ascii=False))
    print("final health:", final)
    return 0 if all(v == 200 for v in final.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
