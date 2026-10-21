"""Phrase-gate regression probe: what knowledge_note() pushes for fixed log-wording sentences, v3 and v4.

  HEALTHY_GUARD=0 .venv/bin/python scripts/kb-gate-probe.py        # one line per sentence: tag, v3 card, v4 card

The sentences are the two defects docs/kb-coverage-survey.md (section 2, D group) measured: cards that exist but never
pass the phrase gate (inode, OOMKilled / exit 137, CrashLoopBackOff, 500) and a generic phrase that pushes a card on
SSH / file-ownership sentences; plus eight controls (the five stage symptoms and three pushes that work today) whose
outcome must not change. Run it before and after editing any card and diff the two outputs. Sentences tagged "-bare"
document the gate's ceiling: a single token weighs at most ln(N) = 7.5 < bar 8, and digits are not tokens, so
"CrashLoopBackOff", "exit code 137" and "500 Internal Server Error" alone can never push a card by phrase data.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "kb" / "src")]
os.environ.setdefault("HEALTHY_GUARD", "0")

PROBES = [
    # (a) misses: the right card is in the BM25 top six but no phrase of it clears the bar
    ("inode-1", "df -i: /dev/sda1 100% IUse%, inode exhaustion on /var/lib/docker, cannot create new files"),
    ("inode-2", "filesystem has no free inodes left (df -i shows IFree 0) although df -h reports 40% free"),
    ("inode-3-bare", "df -i inode usage 100% on /"),
    ("oom-1", "container edge-victim-app exited with exit code 137 (OOMKilled)"),
    ("oom-2", "State: Terminated, Reason: OOMKilled, Exit Code: 137"),
    ("oom-3-bare", "exit code 137"),
    ("crash-1", "pod api-7d9f4 status CrashLoopBackOff, restarts 12"),
    ("crash-2", "Warning  BackOff  Back-off restarting failed container"),
    ("crash-3-bare", "CrashLoopBackOff"),
    ("500-1", "GET /api/ HTTP/1.1 500 Internal Server Error"),
    ("500-2", "upstream returned 500 internal server error, exception in application log"),
    # (b) false pushes: a generic phrase matches sentences from another domain; must be silent
    ("ssh-1", "ssh: Permission denied (publickey)."),
    ("ssh-2", "Permission denied (publickey,password) when connecting to the host over ssh"),
    ("own-1", "chown: changing ownership of '/data': Operation not permitted; file owned by root, permission denied for the app user"),
    # controls: outcome must stay as it is today
    ("ctl-nginx-stopped", "edge-nginx container is not running, exited (0), curl: connection refused on port 80"),
    ("ctl-502-refused", "502 Bad Gateway: connect() failed (111: Connection refused) while connecting to upstream"),
    ("ctl-container-removed", "edge-victim-app container removed, docker ps shows no such container"),
    ("ctl-emerg-directive", 'nginx: [emerg] unknown directive "foo" in /etc/nginx/conf.d/default.conf:3'),
    ("ctl-upstream-500", "upstream edge-victim-app answers 500 Internal Server Error on /api/"),
    ("ctl-nospace", "write failed: No space left on device"),
    ("ctl-oomkilled-yes", "OOMKilled: yes exit 137"),
    ("ctl-host-not-found", 'nginx: [emerg] host not found in upstream "edge-victim-app"'),
]


def outcomes(version):
    import agent
    os.environ["KB_RETRIEVAL"] = version
    agent._kb = None                                   # the pack is cached per process; reopen it for this version
    ids = {d["id"] for d in agent.knowledge_base().docs if d["doc_type"] == "solution"}
    out = {}
    for tag, q in PROBES:
        note = agent.knowledge_note(q)
        out[tag] = next((i for i in sorted(ids, key=len, reverse=True) if i in note), None) if note else None
        assert not note or out[tag], f"note without a known card id: {note[:120]!r}"
    return out


def main():
    v3, v4 = outcomes("v3"), outcomes("v4")
    print(f"{'tag':22} {'v3':44} {'v4':44} sentence")
    for tag, q in PROBES:
        print(f"{tag:22} {v3[tag] or '-':44} {v4[tag] or '-':44} {q[:60]}")
    pushed = sum(bool(v4[t]) for t, _ in PROBES)
    print(f"\nv4 pushed {pushed}/{len(PROBES)}; false pushes (ssh/own): "
          f"{sum(bool(v4[t]) for t, _ in PROBES if t.startswith(('ssh', 'own')))}; "
          f"misses pushed: {sum(bool(v4[t]) for t, _ in PROBES if t.startswith(('inode', 'oom', 'crash', '500')) and not t.endswith('bare'))}/8")


if __name__ == "__main__":
    main()
