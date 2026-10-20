"""Coverage probe of the knowledge pack: 70 real data-centre symptom lines (log wording, not the agent's queries) run
through the retrieval the agent uses (KnowledgeBaseV2, AGENT_DEFAULTS, six card candidates as KB_RETRIEVAL=v4).
Read-only on kb/. Measured 2026-10-20 on corpus_v4: 14/70 push a card; see docs/kb-coverage-survey.md.

  .venv/bin/python scripts/kb-probe.py [corpus.jsonl]        # default kb/artifacts/corpus_v4.jsonl
  .venv/bin/python scripts/kb-probe.py --json out.json

Each line carries the card that should fire (a regex over card ids), "" when the pack has no card for it yet, or
None when no card should fire (a push there is a wrong push). Verdicts: right, wrong, missed (a right card exists
and nothing was pushed), gap (no card exists, nothing pushed), quiet (None and nothing pushed).
"""
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kb" / "src"))
from blackout_ops_kb.retrieval_v2 import AGENT_DEFAULTS, KnowledgeBaseV2  # noqa: E402

# (category, symptom line, expected card id regex | "" no card yet | None nothing should fire)
Q = [
 # cat, query
    ("disk", "No space left on device /var/log full", "card-docker-disk-io|solution-linux-disk-log-growth"),
    ("disk", "df shows 100% but du small, deleted file still held open", ""),
    ("disk", "inode exhaustion no space left df -i 100%", "solution-linux-disk-inode-exhaustion"),
    ("disk", "EXT4-fs error remounting filesystem read-only", ""),
    ("disk", "docker overlay2 filling /var/lib/docker", ""),
    ("disk", "journald logs consuming disk", "solution-linux-disk-log-growth"),
    ("disk", "SMART reallocated sectors disk failing I/O error", ""),
    ("disk", "RAID array degraded mdadm disk failed", ""),
    ("disk", "NFS stale file handle server not responding", ""),
    ("disk", "LVM thin pool full", ""),
    ("mem", "Out of memory: Killed process oom-killer", "solution-linux-oom-killed|card-docker-host-memory-pressure|card-docker-container-oom"),
    ("mem", "container OOMKilled exit code 137", "card-docker-container-oom|solution-k8s-crashloop-oomkilled"),
    ("mem", "swap thrashing high load average memory pressure", "card-docker-host-memory-pressure"),
    ("cpu", "CPU saturated load average high requests timing out", "card-docker-cpu-saturation"),
    ("cpu", "kernel: task blocked for more than 120 seconds hung_task", ""),
    ("proc", "Too many open files EMFILE ulimit", ""),
    ("proc", "fork: Resource temporarily unavailable pids limit", ""),
    ("proc", "zombie processes defunct accumulating", ""),
    ("svc", "systemd unit failed start request repeated too quickly", "solution-linux-systemd-restart-loop"),
    ("svc", "nginx emerg unknown directive config test failed", ""),
    ("svc", "502 Bad Gateway upstream connection refused", "card-docker-port-mismatch|solution-linux-connection-refused-port"),
    ("svc", "504 Gateway Timeout upstream timed out", ""),
    ("svc", "connection refused port not listening", "solution-linux-connection-refused-port|card-docker-port-mismatch"),
    ("svc", "address already in use bind failed", ""),
    ("svc", "database too many connections connection pool exhausted", "card-docker-slow-dependency"),
    ("svc", "429 too many requests rate limited", "card-docker-slow-dependency"),
    ("net", "DNS resolution failed Temporary failure in name resolution", "solution-linux-dns-resolver|card-docker-name-resolution-partition"),
    ("net", "SERVFAIL upstream DNS server unreachable", ""),
    ("net", "nf_conntrack: table full, dropping packet", ""),
    ("net", "MTU mismatch packets fragmented large responses hang", ""),
    ("net", "duplicate IP address ARP conflict", ""),
    ("net", "network interface link down NIC flapping", ""),
    ("net", "bond interface failover slave down LACP", ""),
    ("net", "iptables firewall rule blocking traffic", ""),
    ("net", "packet loss high latency between hosts", "card-docker-network-latency-loss"),
    ("net", "ephemeral port exhaustion TIME_WAIT cannot assign requested address", ""),
    ("net", "DHCP lease not obtained no IP address", ""),
    ("net", "default route missing network unreachable", ""),
    ("tls", "x509 certificate has expired", "solution-linux-tls-certificate-expired"),
    ("tls", "certificate signed by unknown authority", ""),
    ("time", "clock skew NTP not synchronized time drift", ""),
    ("auth", "SSH permission denied publickey", None),
    ("auth", "permission denied file ownership", None),
    ("auth", "SELinux AppArmor denied operation", ""),
    ("auth", "password expired account locked", ""),
    ("hw", "Machine check exception hardware error mce", ""),
    ("hw", "EDAC corrected ECC memory errors", ""),
    ("hw", "CPU temperature above threshold throttled thermal", ""),
    ("hw", "power supply PSU failure redundancy lost IPMI SEL", ""),
    ("hw", "fan failure BMC sensor critical", ""),
    ("hw", "NVRM Xid GPU fallen off the bus", ""),
    ("hw", "PCIe AER corrected error", ""),
    ("hw", "NVMe controller reset timeout", ""),
    ("k8s", "CrashLoopBackOff back-off restarting failed container", "solution-k8s-crashloop-.*"),
    ("k8s", "ImagePullBackOff pull access denied", ""),
    ("k8s", "node NotReady kubelet stopped posting status", ""),
    ("k8s", "pod pending insufficient cpu unschedulable", ""),
    ("k8s", "etcd leader election lost high latency", ""),
    ("k8s", "PersistentVolumeClaim pending no storage class", ""),
    ("docker", "docker daemon not responding Cannot connect to the Docker daemon", ""),
    ("docker", "container paused not responding", ""),
    ("docker", "container network detached no route to upstream", "card-docker-name-resolution-partition"),
    ("virt", "Proxmox VM will not start lock timeout", ""),
    ("virt", "Proxmox cluster quorum lost corosync", ""),
    ("virt", "ceph HEALTH_WARN osd down pg degraded", ""),
    ("app", "java GC overhead long pauses latency", ""),
    ("app", "application exception 500 internal server error", "card-docker-app-5xx-defect"),
    ("sched", "cron job not running backup failed", ""),
    ("pkg", "apt dpkg lock held broken packages after upgrade", ""),
    ("boot", "kernel panic boot failure emergency mode fstab", ""),
]


def verdict(expect, pushed):
    if pushed:
        if expect is None or expect == "":
            return "wrong" if expect is None else "unexpected"
        return "right" if re.fullmatch(expect, pushed) else "wrong"
    if expect:
        return "missed"
    return "quiet" if expect is None else "gap"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    corpus = pathlib.Path(args[0]) if args and not args[0].endswith(".json") else ROOT / "kb" / "artifacts" / "corpus_v4.jsonl"
    kb = KnowledgeBaseV2(corpus, **AGENT_DEFAULTS)
    rows, tally, per_cat = [], collections.Counter(), collections.defaultdict(lambda: [0, 0])
    for cat, q, expect in Q:
        b = kb.retrieve({"query": q}, case_k=0, solution_k=6)
        pushed = b["solutions"][0]["id"] if b["knowledge_match"]["status"] == "matched" and b["solutions"] else ""
        top = kb.search(q, doc_type="solution", top_k=1)
        v = verdict(expect, pushed)
        tally[v] += 1
        per_cat[cat][0] += bool(pushed)
        per_cat[cat][1] += 1
        rows.append(dict(cat=cat, query=q, expect=expect, pushed=pushed, bm25_top=top[0]["id"] if top else "", verdict=v))
        print(f"{v:10} {cat:6} pushed={pushed or '-':42} bm25={(top[0]['id'] if top else '-'):42} | {q}")
    print("\nper category (pushed/total):", ", ".join(f"{c} {a}/{n}" for c, (a, n) in per_cat.items()))
    print(f"pushed {sum(a for a, _ in per_cat.values())}/{len(Q)}; " + ", ".join(f"{k} {v}" for k, v in tally.most_common()))
    if "--json" in sys.argv:
        pathlib.Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(rows, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
