"""Community reference layer for the knowledge pack: fetch, build, evaluate. Plan, predictions and pass criteria were
written before any data was fetched: docs/kb-community-20261022.md.

  .venv/bin/python scripts/kb-community.py fetch            Stack Exchange API -> datasets/derived/stackexchange_ops_qa_20261022/qa.jsonl
  .venv/bin/python scripts/kb-community.py build            qa.jsonl -> kb/artifacts/community_v1.jsonl (index) + holdout.jsonl (15%, never indexed)
  .venv/bin/python scripts/kb-community.py eval             C1 (wrapper check), P2 (held-out category@1), P3 (38 probe sentences), latency
  .venv/bin/python scripts/kb-community.py probe "<text>"   what the agent's tool would append for this sentence
  .venv/bin/python scripts/kb-community.py fetch-dump <site> <Posts.xml> <Users.xml>
                                                            the archive.org data dump of one site -> dump-<site>.jsonl (same rows; no API quota)
  .venv/bin/python scripts/kb-community.py build --dump     index = API rows + dump rows, v1 hold-out excluded by id -> kb/artifacts/community_v2.jsonl

Stdlib only. The API needs no key: 300 requests per IP per day, 100 items per request; `backoff` is honoured.
Content is CC BY-SA (2.5 before 2011-04-08, 3.0 before 2018-05-02, 4.0 after); every row keeps link, author and licence.
"""
import gzip
import hashlib
import html
import json
import pathlib
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
DATA = ROOT / "datasets" / "derived" / "stackexchange_ops_qa_20261022"
QA = DATA / "qa.jsonl"
HOLDOUT = DATA / "holdout.jsonl"
MANIFEST = DATA / "manifest.json"
INDEX = ROOT / "kb" / "artifacts" / "community_v1.jsonl"
API = "https://api.stackexchange.com/2.3"
SEED = 20261022

# Category priority (first hit wins) and the tags fetched per site: fixed in the plan before fetching.
TAGS = [
    ("hardware", {"serverfault": ["ipmi", "ecc", "nvidia", "gpu", "pcie", "temperature", "ups", "power-supply", "smart",
                                  "raid", "mdadm", "hard-drive", "nvme"],
                  "unix": ["nvidia", "raid", "mdadm", "hard-disk"]}),
    ("virtualization", {"serverfault": ["proxmox", "ceph", "kvm-virtualization", "libvirt"], "unix": ["kvm", "libvirt"]}),
    ("k8s", {"serverfault": ["kubernetes", "kubectl", "etcd", "kubeadm"], "unix": []}),
    ("docker", {"serverfault": ["docker", "docker-compose", "containers", "overlayfs"], "unix": ["docker"]}),
    ("disk_fs", {"serverfault": ["disk-space-utilization", "filesystems", "ext4", "xfs", "nfs", "lvm", "inode"],
                 "unix": ["disk-usage", "filesystems", "ext4", "xfs", "nfs", "lvm", "inode"]}),
    ("memory_process", {"serverfault": ["memory", "out-of-memory", "swap", "ulimit", "file-descriptors", "zombie-process",
                                        "kernel-panic", "kernel", "load-average"],
                        "unix": ["out-of-memory", "swap", "ulimit", "kernel-panic", "kernel"]}),
    ("tls_time", {"serverfault": ["ssl", "ssl-certificate", "openssl", "certificate", "ntp", "chrony", "timezone"],
                  "unix": ["openssl", "ntp"]}),
    ("network", {"serverfault": ["networking", "iptables", "nftables", "firewall", "routing", "mtu", "arp", "bonding",
                                 "conntrack", "dhcp", "vlan", "linux-networking", "dns", "bind"],
                 "unix": ["networking", "iptables", "routing", "dns"]}),
    ("service", {"serverfault": ["nginx", "apache-2.4", "systemd", "haproxy", "php-fpm"], "unix": ["systemd", "nginx"]}),
    ("permissions", {"serverfault": ["ssh", "permissions", "sudo", "selinux"], "unix": ["ssh", "permissions", "sudo", "selinux"]}),
    ("boot_packages", {"serverfault": ["grub", "fstab", "apt", "dpkg", "yum", "cron", "logrotate", "rsyslog"],
                       "unix": ["grub", "fstab", "apt", "dpkg", "cron"]}),
    ("app", {"serverfault": ["java", "mysql", "postgresql", "redis"], "unix": []}),
]
TAG_CATEGORY = {}
for _cat, _sites in TAGS:
    for _tags in _sites.values():
        for _t in _tags:
            TAG_CATEGORY.setdefault(_t, _cat)
CATEGORY_ORDER = [c for c, _ in TAGS]

SYMPTOM_RE = re.compile(r"error|fail|denied|refused|time(d)? ?out|cannot|can't|unable|no space|emerg|crit|warn|exit|kill"
                        r"|panic|segfault|xid|ecc|mce|degraded|unreachable|not found|no such|invalid|corrupt|stuck|hang"
                        r"|down\b|lost|exceeded|too many|full\b|read-only|not synchron|expired|oom|out of memory|blocked"
                        r"|reset|dropping|not running|not responding|not working|broken", re.I)

# Q_gap and Q_stage, verbatim from the plan (docs/kb-community-20261022.md).
Q_GAP = [
    ("disk_fs", "EXT4-fs error (device nvme0n1p2): ext4_journal_check_start: Detected aborted journal; remounting filesystem read-only"),
    ("disk_fs", "df shows / 100% used but du -sh /* adds up to half of that; a deleted log file is still held open by a process"),
    ("disk_fs", "docker: write /var/lib/docker/containers/<id>/<id>-json.log: no space left on device; container json logs grew to 40 GB"),
    ("disk_fs", "nfs: server storage01 not responding, still trying; processes stuck in D state"),
    ("disk_fs", "LVM thin pool data is 100% full, writes fail with input/output error"),
    ("memory_process", "accept4: Too many open files (EMFILE) in nginx worker; worker_connections exceed open file resource limit"),
    ("memory_process", "fork: retry: Resource temporarily unavailable; cgroup pids.max reached"),
    ("service", "nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address already in use)"),
    ("service", "systemd: start request repeated too quickly for app.service; Failed with result 'start-limit-hit'"),
    ("network", "kernel: nf_conntrack: table full, dropping packet"),
    ("network", "ping -M do -s 1472 fails with message too long, mtu=1400; large transfers hang while ssh works"),
    ("network", "no default route after reboot; ip route shows only the link-local subnet, external hosts unreachable"),
    ("network", "bond0: link status definitely down for interface eth1, disabling it; LACP partner not responding"),
    ("network", "connect: cannot assign requested address; thousands of sockets in TIME_WAIT, ephemeral port range exhausted"),
    ("network", "iptables rule added by mistake drops traffic to port 443; curl connection timed out but the service is listening"),
    ("tls_time", "chronyc tracking: Leap status: Not synchronised; clock is 11 minutes behind and TLS handshakes fail with certificate not yet valid"),
    ("tls_time", "curl: (60) SSL certificate problem: unable to get local issuer certificate; intermediate certificate missing from the chain"),
    ("tls_time", "x509: certificate has expired or is not yet valid; kubeadm certs check-expiration shows the apiserver certificate expired"),
    ("hardware", "NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus"),
    ("hardware", "EDAC MC0: 1 UE memory read error on DIMM; mce: Hardware Error: Machine check events logged"),
    ("hardware", "ipmitool sel list: Power Supply #2 Power Supply AC lost Asserted"),
    ("hardware", "smartctl: SMART overall-health self-assessment test result: FAILED; Reallocated_Sector_Ct increasing; md0 degraded array"),
    ("hardware", "nvme nvme0: I/O timeout, controller reset; iostat shows await above 2000 ms"),
    ("hardware", "UPS on battery: upsc shows ups.status OB LB, battery.runtime 300 seconds; mains power lost"),
    ("hardware", "thermal throttling: CPU temperature above 95C, fan 3 reads 0 RPM in ipmitool sdr"),
    ("k8s", "Pod stuck in ImagePullBackOff: failed to pull image registry.local/app:1.2: manifest unknown"),
    ("k8s", "node worker-2 NotReady: kubelet stopped posting node status; PLEG is not healthy"),
    ("k8s", "PersistentVolumeClaim is Pending: no persistent volumes available for this claim and no storage class is set"),
    ("k8s", "etcdserver: request timed out; apiserver slow, etcd fsync latency above 1 s"),
    ("virtualization", "corosync: quorum lost, blocking activity; pve cluster node shows no quorum and VMs cannot start"),
    ("virtualization", "qm start 101 fails: VM is locked (backup); vzdump backup job failed last night"),
    ("virtualization", "ceph health HEALTH_WARN: 1 osds down; Degraded data redundancy, pgs undersized"),
    ("docker", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"),
    ("docker", "docker: Error response from daemon: failed to create shim task: OCI runtime create failed"),
    ("boot_packages", "cron job did not run: CRON (root) MAIL (mailed 1 byte of output but got status 0x004b); PATH differs under cron"),
    ("boot_packages", "apt: dpkg was interrupted, you must manually run dpkg --configure -a to correct the problem"),
    ("boot_packages", "boot drops to emergency mode: Timed out waiting for device dev-disk-by-uuid; fstab entry for an unplugged disk"),
    ("app", "java: GC overhead limit exceeded; long stop-the-world pauses, requests time out while the heap is near max"),
]
# How the agent itself queries (scripts/agent.py symptom_query, docs/kb-repair-cards.md): bare codes and its own 5xx
# phrasings. Expected: no community reference (bare codes tokenise to nothing; the phrasings are runbook matters).
Q_AGENT = ["504", "502", "429", "the service itself answers HTTP 504 Gateway Timeout",
           "HTTP 500 Internal Server Error from upstream edge-victim-app", "upstream prematurely closed connection while reading response header"]
Q_STAGE = [
    "nginx stopped exited 0",
    "connect() failed (111: Connection refused) while connecting to upstream",
    "container edge-victim-app removed no such container",
    'nginx: [emerg] unknown directive "proxy_pas" in /etc/nginx/conf.d/default.conf',
    "upstream returned 500 internal server error on /api/",
    "SSH permission denied (publickey)",
]


# ---------------------------------------------------------------- HTML -> lines
class _Text(HTMLParser):
    BLOCK = {"p", "br", "li", "pre", "div", "h1", "h2", "h3", "h4", "blockquote", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.code = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append(("\n", False))
        if tag in ("pre", "code"):
            self.code += 1

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append(("\n", False))
        if tag in ("pre", "code") and self.code:
            self.code -= 1

    def handle_data(self, data):
        self.parts.append((data, self.code > 0))


def html_lines(body):
    """[(line, in_code)] with whitespace collapsed; code blocks keep their own lines."""
    p = _Text()
    p.feed(body or "")
    lines, cur, cur_code = [], [], False
    for text, code in p.parts:
        for i, piece in enumerate(text.split("\n")):
            if i:
                lines.append(("".join(cur), cur_code))
                cur, cur_code = [], code
            if piece:
                cur.append(piece)
                cur_code = cur_code or code
    lines.append(("".join(cur), cur_code))
    return [(re.sub(r"\s+", " ", l).strip(), c) for l, c in lines if l.strip()]


def symptom_lines(body, limit=6, width=160):
    """Code lines with an error word first, then prose lines with one, then other code lines."""
    ls = html_lines(body)
    ranked = ([l for l, c in ls if c and SYMPTOM_RE.search(l)] + [l for l, c in ls if not c and SYMPTOM_RE.search(l)]
              + [l for l, c in ls if c and not SYMPTOM_RE.search(l)])
    out = []
    for l in ranked:
        if len(l) >= 12 and l[:width] not in out:
            out.append(l[:width])
        if len(out) == limit:
            break
    return out


def plain(body, width=1500):
    return " ".join(l for l, _ in html_lines(body))[:width]


def licence(item):
    if item.get("content_license"):
        return item["content_license"]
    t = item.get("creation_date", 0)
    return "CC BY-SA 2.5" if t < 1302220800 else "CC BY-SA 3.0" if t < 1525219200 else "CC BY-SA 4.0"


def category(tags):
    for c in CATEGORY_ORDER:
        if any(TAG_CATEGORY.get(t) == c for t in tags):
            return c
    return "other"


# ---------------------------------------------------------------- fetch
class Api:
    def __init__(self):
        self.calls, self.quota = 0, None

    def get(self, path, **params):
        params.setdefault("pagesize", 100)
        params.setdefault("filter", "withbody")
        url = f"{API}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "blackout-ops-kb/1.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read()
                    data = json.loads(gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw)
                break
            except urllib.error.HTTPError as e:
                body = e.read()[:300]
                if e.code in (502, 503) and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                if e.code == 400 and b"throttle" in body and attempt < 2:
                    m = re.search(rb"(\d+) seconds", body)
                    time.sleep(int(m.group(1)) + 1 if m else 60)
                    continue
                raise SystemExit(f"API {e.code} on {path}: {body!r}")
        self.calls += 1
        self.quota = data.get("quota_remaining")
        time.sleep(max(0.4, float(data.get("backoff") or 0)))
        return data


def fetch(_):
    DATA.mkdir(parents=True, exist_ok=True)
    api, questions, per_tag = Api(), {}, []
    for cat, sites in TAGS:
        for site, tags in sites.items():
            for tag in tags:
                d = api.get("search/advanced", site=site, tagged=tag, accepted="True", sort="votes", order="desc")
                n_new = 0
                for it in d.get("items", []):
                    key = (site, it["question_id"])
                    if key not in questions and it.get("accepted_answer_id"):
                        questions[key] = (site, it)
                        n_new += 1
                per_tag.append({"site": site, "tag": tag, "category": cat, "returned": len(d.get("items", [])), "new": n_new})
                print(f"{site:11s} {tag:24s} {len(d.get('items', [])):3d} returned {n_new:3d} new  quota {api.quota}", file=sys.stderr)
    # incident-like only: a symptom line in the body or an error word in the title (fewer answer calls, closer to the use)
    kept = {k: v for k, v in questions.items() if SYMPTOM_RE.search(v[1]["title"]) or symptom_lines(v[1]["body"], limit=1)}
    answers = {}
    for site in {k[0] for k in kept}:
        ids = sorted({v[1]["accepted_answer_id"] for k, v in kept.items() if k[0] == site})
        for i in range(0, len(ids), 100):
            d = api.get("answers/" + ";".join(map(str, ids[i:i + 100])), site=site)
            for a in d.get("items", []):
                answers[(site, a["answer_id"])] = a
            print(f"{site:11s} answers {i + len(ids[i:i + 100]):5d}/{len(ids)}  quota {api.quota}", file=sys.stderr)
    rows, dropped_short = [], 0
    for (site, qid), (_, it) in sorted(kept.items()):
        a = answers.get((site, it["accepted_answer_id"]))
        if not a:
            continue
        answer_text = plain(a.get("body"))
        if len(answer_text) < 80:
            dropped_short += 1
            continue
        rows.append({"site": site, "question_id": qid, "title": html.unescape(it["title"]), "tags": it.get("tags", []),
                     "category": category(it.get("tags", [])), "score": it.get("score", 0), "creation_date": it.get("creation_date"),
                     "link": it["link"], "owner": (it.get("owner") or {}).get("display_name", ""), "license": licence(it),
                     "question_text": plain(it.get("body")), "symptom_lines": symptom_lines(it.get("body")),
                     "accepted_answer_id": it["accepted_answer_id"], "answer_text": answer_text,
                     "answer_score": a.get("score", 0), "answer_owner": (a.get("owner") or {}).get("display_name", ""),
                     "answer_license": licence(a)})
    QA.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    manifest = {"schema": "blackout-ops-community-qa/v1", "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "source": "Stack Exchange API 2.3, search/advanced accepted=True sort=votes, 100 per tag; answers by id",
                "license": "CC BY-SA (per-row `license`/`answer_license`; attribution = link + owner fields)",
                "api_calls": api.calls, "quota_remaining": api.quota, "questions_seen": len(questions),
                "incident_like": len(kept), "answers_fetched": len(answers), "dropped_short_answer": dropped_short,
                "rows": len(rows), "by_site": dict(Counter(r["site"] for r in rows)),
                "by_category": dict(Counter(r["category"] for r in rows)),
                "by_license": dict(Counter(r["license"] for r in rows)), "per_tag": per_tag,
                "sha256_qa": hashlib.sha256(QA.read_bytes()).hexdigest()}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "per_tag"}, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- data dump (archive.org/details/stackexchange)
def fetch_dump(argv):
    """Posts.xml rows: PostTypeId 1 = question (Title, Tags like '<nginx><ssl>', AcceptedAnswerId, Body as HTML), 2 = answer
    (ParentId). Streamed twice: questions in our tag set with an accepted answer and an incident-like body first, then the
    accepted answers. Users.xml gives the display names the licence asks us to credit. Rows match the API schema."""
    import xml.etree.ElementTree as ET
    site, posts, users = argv[2], pathlib.Path(argv[3]), pathlib.Path(argv[4])
    host = {"serverfault": "serverfault.com", "unix": "unix.stackexchange.com"}[site]
    names = {}
    for _, el in ET.iterparse(str(users), events=("end",)):
        if el.tag == "row":
            names[el.get("Id")] = el.get("DisplayName", "")
            el.clear()
    def epoch(ts):
        return int(time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")))
    questions, wanted = {}, {}
    for _, el in ET.iterparse(str(posts), events=("end",)):
        if el.tag != "row":
            continue
        if el.get("PostTypeId") == "1" and el.get("AcceptedAnswerId"):
            tags = re.findall(r"<([^<>]+)>", el.get("Tags", "")) or el.get("Tags", "").split("|")
            tags = [t for t in tags if t]
            if any(t in TAG_CATEGORY for t in tags):
                title, body = html.unescape(el.get("Title", "")), el.get("Body", "")
                if SYMPTOM_RE.search(title) or symptom_lines(body, limit=1):
                    q = {"site": site, "question_id": int(el.get("Id")), "title": title, "tags": tags, "category": category(tags),
                         "score": int(el.get("Score", 0)), "creation_date": epoch(el.get("CreationDate")),
                         "link": f"https://{host}/questions/{el.get('Id')}", "owner": names.get(el.get("OwnerUserId"), el.get("OwnerDisplayName", "")),
                         "license": el.get("ContentLicense") or licence({"creation_date": epoch(el.get("CreationDate"))}),
                         "question_text": plain(body), "symptom_lines": symptom_lines(body), "accepted_answer_id": int(el.get("AcceptedAnswerId"))}
                    questions[q["question_id"]] = q
                    wanted[q["accepted_answer_id"]] = q["question_id"]
        el.clear()
    print(f"{site}: {len(questions)} incident-like questions with accepted answers in our tags", file=sys.stderr)
    rows, short = [], 0
    for _, el in ET.iterparse(str(posts), events=("end",)):
        if el.tag == "row" and el.get("PostTypeId") == "2" and int(el.get("Id")) in wanted:
            q = questions[wanted[int(el.get("Id"))]]
            text = plain(el.get("Body", ""))
            if len(text) < 80:
                short += 1
            else:
                rows.append({**q, "answer_text": text, "answer_score": int(el.get("Score", 0)),
                             "answer_owner": names.get(el.get("OwnerUserId"), el.get("OwnerDisplayName", "")),
                             "answer_license": el.get("ContentLicense") or licence({"creation_date": epoch(el.get("CreationDate"))})})
        if el.tag == "row":
            el.clear()
    out = DATA / f"dump-{site}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sorted(rows, key=lambda r: r["question_id"])), encoding="utf-8")
    print(json.dumps({"site": site, "rows": len(rows), "dropped_short_answer": short, "by_category": dict(Counter(r["category"] for r in rows)),
                      "by_license": dict(Counter(r["license"] for r in rows)), "out": str(out.relative_to(ROOT)),
                      "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- build
def index_text(r):
    return " ".join([r["title"], " ".join(r["tags"]), " ".join(r["symptom_lines"]), r["answer_text"][:300]])


def excerpt(text, width=320):
    if len(text) <= width:
        return text
    cut = text[:width]
    return cut[:cut.rfind(" ")] + "…" if " " in cut[width // 2:] else cut + "…"


def build(argv):
    """Default: the API rows, 15 % stratified hold-out -> community_v1. With --dump: API rows + dump rows, the v1 hold-out
    excluded by (site, question_id) so it stays a clean test set -> community_v2 (the runtime loads v2 when present)."""
    rows = [json.loads(l) for l in QA.read_text(encoding="utf-8").splitlines() if l.strip()]
    if "--dump" in argv:
        held = {(r["site"], r["question_id"]) for r in (json.loads(l) for l in HOLDOUT.read_text(encoding="utf-8").splitlines() if l.strip())}
        seen = {(r["site"], r["question_id"]) for r in rows}
        for f in sorted(DATA.glob("dump-*.jsonl")):
            for l in f.read_text(encoding="utf-8").splitlines():
                if not l.strip():
                    continue
                r = json.loads(l)
                if (r["site"], r["question_id"]) not in seen:
                    rows.append(r)
                    seen.add((r["site"], r["question_id"]))
        rows = [r for r in rows if r["category"] != "other" and (r["site"], r["question_id"]) not in held]
        docs = [{"id": f"se-{r['site']}-{r['question_id']}", "site": r["site"], "title": r["title"], "tags": r["tags"],
                 "category": r["category"], "score": r["score"], "link": r["link"], "owner": r["owner"], "license": r["license"],
                 "answer_owner": r["answer_owner"], "answer_license": r["answer_license"], "answer_id": r["accepted_answer_id"],
                 "index_text": index_text(r), "answer_excerpt": excerpt(r["answer_text"])} for r in rows]
        out = INDEX.with_name("community_v2.jsonl")
        out.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs), encoding="utf-8")
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
        m["build_v2"] = {"indexed": len(docs), "holdout_excluded": len(held), "by_category": dict(Counter(d["category"] for d in docs)),
                         "by_site": dict(Counter(d["site"] for d in docs)), "sha256_index": hashlib.sha256(out.read_bytes()).hexdigest(),
                         "index_bytes": out.stat().st_size}
        MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(m["build_v2"], ensure_ascii=False, indent=1))
        return
    rows = [r for r in rows if r["category"] != "other"]
    by_cat = {}
    for r in sorted(rows, key=lambda r: (r["category"], r["site"], r["question_id"])):
        by_cat.setdefault(r["category"], []).append(r)
    rng, hold = random.Random(SEED), set()
    for cat, rs in sorted(by_cat.items()):
        n = round(0.15 * len(rs))
        hold |= {(r["site"], r["question_id"]) for r in rng.sample(rs, n)}
    docs, held = [], []
    for r in rows:
        if (r["site"], r["question_id"]) in hold:
            held.append(r)
            continue
        docs.append({"id": f"se-{r['site']}-{r['question_id']}", "site": r["site"], "title": r["title"], "tags": r["tags"],
                     "category": r["category"], "score": r["score"], "link": r["link"], "owner": r["owner"],
                     "license": r["license"], "answer_owner": r["answer_owner"], "answer_license": r["answer_license"],
                     "answer_id": r["accepted_answer_id"], "index_text": index_text(r), "answer_excerpt": excerpt(r["answer_text"])})
    INDEX.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs), encoding="utf-8")
    HOLDOUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in held), encoding="utf-8")
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    m["build"] = {"seed": SEED, "holdout_fraction": 0.15, "indexed": len(docs), "holdout": len(held),
                  "indexed_by_category": dict(Counter(d["category"] for d in docs)),
                  "sha256_index": hashlib.sha256(INDEX.read_bytes()).hexdigest(),
                  "sha256_holdout": hashlib.sha256(HOLDOUT.read_bytes()).hexdigest(),
                  "index_bytes": INDEX.stat().st_size}
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(m["build"], ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- eval
def evaluate(_):
    import os
    os.environ.setdefault("HEALTHY_GUARD", "0")
    import community_knowledge as ck
    idx = ck.CommunityIndex.load()
    report = {"indexed": len(idx.docs)}

    # C1: the wrapper changes nothing the stage path sees (forced by construction; run to prove the import did not break it)
    import agent
    notes_on = [agent.knowledge_note(q) for _, q in Q_GAP] + [agent.knowledge_note(q) for q in Q_STAGE]
    os.environ["KB_COMMUNITY"] = "0"
    ck._index = None
    notes_off = [agent.knowledge_note(q) for _, q in Q_GAP] + [agent.knowledge_note(q) for q in Q_STAGE]
    os.environ["KB_COMMUNITY"] = "1"
    ck._index = None
    stage = {}
    for q in Q_STAGE:
        out = agent.search_incident_knowledge(q)
        matched = out.startswith("\n--- retrieved hard-failure procedure") or ": matched," in out.splitlines()[0]
        stage[q] = {"card_matched": matched, "community_shown": "community references" in out}
    agent_shaped = {q: "community references" in agent.search_incident_knowledge(q) for q in Q_AGENT}
    gap_shown = sum("community references" in agent.search_incident_knowledge(q) for _, q in Q_GAP)
    report["C1"] = {"knowledge_note_identical": notes_on == notes_off,
                    "community_never_with_matched_card": not any(v["card_matched"] and v["community_shown"] for v in stage.values()),
                    "community_never_on_stage_queries": not any(v["community_shown"] for v in stage.values()),
                    "community_never_on_agent_shaped_queries": not any(agent_shaped.values()),
                    "shown_for_gap_sentences": f"{gap_shown}/{len(Q_GAP)}", "not_vacuous": gap_shown > 0 or not idx.docs,
                    "stage": stage, "agent_shaped": agent_shaped}
    report["C1"]["pass"] = all(report["C1"][k] for k in ("knowledge_note_identical", "community_never_with_matched_card",
                                                          "community_never_on_stage_queries", "community_never_on_agent_shaped_queries",
                                                          "not_vacuous"))

    # P2: held-out category@1; query = title + symptom lines (no tags, no answer)
    held = [json.loads(l) for l in HOLDOUT.read_text(encoding="utf-8").splitlines() if l.strip()] if HOLDOUT.exists() else []
    hits, shown, per_cat = 0, 0, {}
    for r in held:
        refs = ck.rank(" ".join([r["title"], *r["symptom_lines"]]), k=1)      # the live path: BM25, or reranked + floored
        c = per_cat.setdefault(r["category"], Counter())
        c["n"] += 1
        if refs:
            shown += 1
            if refs[0][2]["category"] == r["category"]:
                hits += 1
                c["hit"] += 1
    # stricter secondary number: symptom lines only (no title, which usually names the tag)
    s_hits = s_n = 0
    for r in held:
        if not r["symptom_lines"]:
            continue
        s_n += 1
        refs = ck.rank(" ".join(r["symptom_lines"]), k=1)
        s_hits += bool(refs) and refs[0][2]["category"] == r["category"]
    share = Counter(d["category"] for d in idx.docs)
    majority = max(share.values()) / max(1, len(idx.docs)) if share else 0
    report["P2"] = {"holdout": len(held), "category_at_1": round(hits / max(1, len(held)), 3),
                    "wrong_at_1": round((shown - hits) / max(1, len(held)), 3),
                    "returned_a_reference": round(shown / max(1, len(held)), 3), "majority_baseline": round(majority, 3),
                    "path": "rerank+floor" if ck.RERANK_URL else "bm25",
                    "pass": bool(held) and hits / len(held) >= 0.60 and hits / len(held) >= 2 * majority,
                    "symptom_lines_only": {"n": s_n, "category_at_1": round(s_hits / max(1, s_n), 3)},
                    "by_category": {k: f"{v['hit']}/{v['n']}" for k, v in sorted(per_cat.items())}}

    # P3: the 38 probe sentences
    rows, right, wrong, none = [], 0, 0, 0
    for cat, q in Q_GAP:
        refs = [] if ck.stack_covers(q) else ck.rank(q, k=2)
        top = refs[0][2] if refs else None
        verdict = "none" if not top else "right" if top["category"] == cat else "wrong"
        right, wrong, none = right + (verdict == "right"), wrong + (verdict == "wrong"), none + (verdict == "none")
        rows.append({"category": cat, "query": q[:70], "verdict": verdict,
                     "top": (top["title"][:80] + f" [{top['category']}]") if top else None})
    n = len(Q_GAP)
    report["P3"] = {"right": f"{right}/{n}", "wrong": f"{wrong}/{n}", "none": f"{none}/{n}",
                    "target_met": right / n >= 0.70 and wrong / n <= 0.15, "rows": rows}

    # latency
    t0 = time.perf_counter()
    for _, q in Q_GAP:
        ck.rank(q, k=2)
    report["latency_ms_per_query"] = round(1000 * (time.perf_counter() - t0) / n, 1)
    print(json.dumps(report, ensure_ascii=False, indent=1))


def probe(argv):
    import community_knowledge as ck
    q = " ".join(argv[2:])
    for s, shared, d in ck.index().search(q, k=3):
        print(f"{s:6.2f} {d['category']:14s} {d['title'][:70]}  shared={shared}")
    print(ck.references(q))


if __name__ == "__main__":
    {"fetch": fetch, "fetch-dump": fetch_dump, "build": build, "eval": evaluate, "probe": probe}[sys.argv[1]](sys.argv)
