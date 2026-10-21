"""Community reference layer: Server Fault / Unix & Linux accepted answers, for symptoms no runbook or repair card
covers. A separate BM25 index (same tokenizer and parameters as the pack's KnowledgeBaseV2, IDF computed only over
these documents), so loading it changes no score of any existing document. Shown only by search_incident_knowledge,
only when no card matched and no runbook of this stack names the symptom; knowledge_note never consults it.
KB_COMMUNITY=0 disables it; a missing corpus file disables it silently. Design, review and measurements:
docs/kb-community-20261022.md."""
import json
import math
import os
import pathlib
import re
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "kb" / "artifacts" / "community_v1.jsonl"
# API rows + the archive.org dumps as per-site gzip shards; used when the directory has shards (KB_COMMUNITY_V1=1 forces v1)
CORPUS_V2 = CORPUS.with_name("community_v2")

MIN_SHARED = 2          # a reference needs at least this many distinct query tokens in the document, else "no reference"
K1, B = 1.2, 0.75       # KnowledgeBaseV2's defaults
# This stack's own matters: its entities, and the symptoms its runbooks already describe (runbooks/*.md titles). A
# query containing one of these gets no community reference. Word-overlap with runbook titles was tried first and
# suppressed hardware/boot probes on words like "device" or "up" (docs/kb-community-20261022.md, Phase 4), so the list
# is explicit.
STACK_PHRASES = ("edge-victim", "8880", "/api/", "/board/", "default.conf", "upstream", "nginx stopped", "exited (0)",
                 "exited 0", "web service down", "connection refused", "bad gateway", "gateway timeout", "gateway time-out",
                 "internal server error", "service unavailable", "no such container", "unknown directive", "restart loop",
                 "crashloop", "exit 137", "exited (137)", "out of memory", "oomkilled", "no space left on device",
                 "forbidden", "too many requests", "redirect loop", "mime.types", "worker_connections", "empty mounted",
                 "name resolution", "could not be resolved", "host not found in upstream")
STACK_CODES = re.compile(r"\b(50[0-4]|40[34]|429|301)\b")
SITE_HOST = {"serverfault": "serverfault.com", "unix": "unix.stackexchange.com"}


def _tokens():
    """The pack's tokenizer (drops digits and stop words). COMMUNITY_TOKENS=digits adds numeric tokens of 2-5 digits
    (exit codes, ports, Xid numbers) to index and query alike: a retrieval experiment knob, measured before any default
    changes (docs/kb-community-20261022.md)."""
    import sys
    sys.path.insert(0, str(ROOT / "kb" / "src"))
    from blackout_ops_kb.retrieval_v2 import tokens
    if os.environ.get("COMMUNITY_TOKENS") == "digits":
        num = re.compile(r"(?<![\w.])\d{2,5}(?![\w.])")
        return lambda text: tokens(text) + num.findall(str(text))
    return tokens


class CommunityIndex:
    """BM25 with an inverted index: a query touches only the postings of its own tokens, so 66k documents (the v2 corpus)
    cost the same per query as 6k did with a full scan. Same scores as the scan, by construction."""

    def __init__(self, docs):
        tokens = _tokens()
        self.tokens = tokens
        self.docs = docs
        self.len = []
        self.postings = {}                    # token -> list of (doc index, term frequency)
        for i, d in enumerate(docs):
            tf = Counter(tokens(d["index_text"]))
            self.len.append(sum(tf.values()))
            for t, f in tf.items():
                self.postings.setdefault(t, []).append((i, f))
        self.avglen = sum(self.len) / max(1, len(self.len))
        n = len(docs)
        self.idf = {t: math.log((n + 1) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def __len__(self):
        return len(self.docs)

    def category_counts(self):
        return Counter(d["category"] for d in self.docs)

    @classmethod
    def load(cls, path=None):
        if os.environ.get("KB_COMMUNITY", "1") == "0":
            return cls([])
        if path is None:
            # v2 (158,881 documents, 7 sites) is opt-in only: with the reranker it scored category@1 0.756 (FTS5 first
            # stage) against v1 + rerank 0.775, and 2.5 GB of RAM in the Python index (docs/kb-community-20261022.md,
            # Gate 1). KB_COMMUNITY_V2=1 selects it (with COMMUNITY_BACKEND=fts5 for the small footprint); default v1.
            shards = sorted(CORPUS_V2.glob("*.jsonl.gz")) if CORPUS_V2.is_dir() else []
            want_v2 = os.environ.get("KB_COMMUNITY_V2") == "1"
            plan = governor_plan()
            if plan and plan.get("community_corpus") == "v1":
                want_v2 = False
            path = CORPUS_V2 if shards and want_v2 else CORPUS
        if not path.exists():
            return cls([])
        files = sorted(path.glob("*.jsonl.gz")) if path.is_dir() else [path]
        docs = []
        for f in files:
            if str(f).endswith(".gz"):
                import gzip
                text = gzip.open(f, "rt", encoding="utf-8").read()
            else:
                text = f.read_text(encoding="utf-8")
            docs += [json.loads(l) for l in text.splitlines() if l.strip()]
        return cls(docs)

    def search(self, query, k=2, min_shared=MIN_SHARED):
        """[(score, shared_tokens, doc)] best first; only documents sharing >= min_shared distinct query tokens."""
        q = Counter(self.tokens(query))
        if not q or not self.docs:
            return []
        acc, shared = {}, {}
        for t in q:
            for i, f in self.postings.get(t, ()):
                acc[i] = acc.get(i, 0.0) + self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * self.len[i] / self.avglen))
                shared.setdefault(i, []).append(t)
        out = [(s, shared[i], self.docs[i]) for i, s in acc.items() if len(shared[i]) >= min_shared]
        out.sort(key=lambda x: -x[0])
        return out[:k]


# Optional cross-encoder reranking (docs/kb-community-20261022.md Gate B): RERANK_URL names a vLLM rerank sidecar
# (scripts/serve-rerank.sh, nvidia/llama-nemotron-rerank-1b-v2). BM25 keeps the first stage (top RERANK_TOP_N), the
# reranker orders them and its score decides "no reference" below RERANK_MIN_SCORE. Unset, or any failure, means the
# BM25 order as before: the sidecar can never take the tool down.
RERANK_URL = os.environ.get("RERANK_URL", "")
# 50 candidates and the reranker reading question + answer: chosen on held-out half A (0.789 vs 0.777 for 20/answer),
# read on half B (0.788 vs 0.775); recall@50 0.998. Costs about 2.5x the rerank time of top-20 (docs/kb-community-20261022.md).
RERANK_TOP_N = int(os.environ.get("RERANK_TOP_N", "50"))
# 0 = order only, no abstention: on the held-out halves every floor from 0.05 up turned more right answers into "none"
# than wrong ones (0.05: right 0.78 -> 0.38, wrong 0.22 -> 0.07); the reranker's value here is the order (wrong@1
# 0.29 -> 0.22), not the absolute score (docs/kb-community-20261022.md, Gate B).
RERANK_MIN_SCORE = float(os.environ.get("RERANK_MIN_SCORE", "0"))
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT", "5"))

class FtsIndex:
    """The same contract as CommunityIndex on an SQLite FTS5 file (kb/artifacts/community.sqlite, built by
    `kb-community.py build-fts`): no tokenising at start, a few MB of RAM, millisecond queries at 159k documents. FTS5's
    own bm25() ranks (unicode61 tokens: the pack tokenizer's compound names split into parts, so scores differ from
    CommunityIndex, which is why P2/P3 are re-measured per backend). Rows come back as the same doc dicts."""

    COLS = ("id", "site", "title", "tags", "category", "score", "link", "owner", "license", "answer_owner", "answer_license",
            "answer_id", "answer_excerpt", "index_text")

    def __init__(self, path):
        import sqlite3
        self.path = path
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.tokens = _tokens()
        self.n = self.con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

    def __len__(self):
        return self.n

    def category_counts(self):
        return Counter(dict(self.con.execute("SELECT category, COUNT(*) FROM docs GROUP BY category").fetchall()))

    def search(self, query, k=2, min_shared=MIN_SHARED):
        words = list(dict.fromkeys(w for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", query) if len(w) >= 3))
        if not words or not self.n:
            return []
        match = " OR ".join(f'"{w}"' for w in words[:40])
        rows = self.con.execute("SELECT rowid, bm25(fts) FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                                (match, max(k * 4, 40))).fetchall()
        q = set(self.tokens(query))
        out = []
        for rowid, neg in rows:
            r = self.con.execute(f"SELECT {', '.join(self.COLS)} FROM docs WHERE rowid = ?", (rowid,)).fetchone()
            d = dict(zip(self.COLS, r))
            d["tags"] = json.loads(d["tags"]) if d["tags"] else []
            shared = [t for t in q if t in set(self.tokens(d["index_text"]))]
            if len(shared) >= min_shared:
                out.append((-neg, shared, d))
            if len(out) >= k:
                break
        return out


FTS_PATH = CORPUS.with_name("community.sqlite")
_index = None


RERANK_PASSAGE = os.environ.get("RERANK_PASSAGE", "both")   # what the reranker reads: both (title+tags+symptom lines+answer head) | answer


def passage(d):
    return d["index_text"] if RERANK_PASSAGE == "both" else f"{d['title']}\n{d['answer_excerpt']}"


def rerank(query, cands):
    """[(rerank_score, shared, doc)] best first, or None when the sidecar did not answer."""
    import urllib.request
    try:
        body = json.dumps({"model": "rerank", "query": query, "top_n": len(cands),
                           "documents": [passage(d) for _, _, d in cands]}).encode()
        req = urllib.request.Request(RERANK_URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=RERANK_TIMEOUT) as r:
            results = json.loads(r.read())["results"]
        scored = [(float(item["relevance_score"]), cands[item["index"]][1], cands[item["index"]][2]) for item in results]
        scored.sort(key=lambda x: -x[0])
        return scored
    except Exception:
        return None


def governor_plan():
    """RESOURCE_GOVERNOR=1 lets scripts/resource_governor.py decide whether the reranker and the big corpus run on this
    host right now (memory envelope); unset, nothing is restricted. Failures mean "not restricting"."""
    if os.environ.get("RESOURCE_GOVERNOR") != "1":
        return None
    try:
        import resource_governor
        return resource_governor.plan(resource_governor.read_mem_available_gib())
    except Exception:
        return None


def rank(query, k=2):
    """The references the tool would show, best first: reranked and floored when a sidecar answers, BM25 otherwise."""
    idx = index()
    plan = governor_plan()
    if plan and not plan.get("reranker_allowed", True):
        return idx.search(query, k=k)
    if RERANK_URL and len(idx):
        cands = idx.search(query, k=RERANK_TOP_N, min_shared=1)
        scored = rerank(query, cands) if cands else []
        if scored is not None:
            return [x for x in scored if x[0] >= RERANK_MIN_SCORE][:k]
    return idx.search(query, k=k)


def index():
    """COMMUNITY_BACKEND=fts5 (and the sqlite file present) selects FtsIndex; otherwise the in-memory CommunityIndex."""
    global _index
    if _index is None:
        if os.environ.get("COMMUNITY_BACKEND") == "fts5" and FTS_PATH.exists() and os.environ.get("KB_COMMUNITY", "1") != "0":
            _index = FtsIndex(FTS_PATH)
        else:
            _index = CommunityIndex.load()
    return _index


def stack_covers(query):
    """True when the symptom is this stack's own: the query names one of its entities, one of its runbooks' symptoms,
    or an HTTP status its runbooks handle."""
    q = query.lower()
    return any(p in q for p in STACK_PHRASES) or bool(STACK_CODES.search(q))


def line_for(d, excerpt_width=None):
    excerpt = d["answer_excerpt"] if excerpt_width is None else d["answer_excerpt"][:excerpt_width].rstrip() + "…"
    return (f"- {d['title']} [{d['site']}, score {d['score']}] :: {excerpt} — answer by {d.get('answer_owner') or 'unknown'}, "
            f"{d.get('answer_license', 'CC BY-SA')}, https://{SITE_HOST.get(d['site'], d['site'])}/a/{d.get('answer_id', '')}")


def render(refs, budget=700):
    """The lines search_incident_knowledge appends. Excerpts with attribution, not instructions: the agent still has to
    confirm with read-only tools and stay inside its own tool contracts. A reference that does not fit is dropped,
    never cut mid-sentence or mid-URL; the first one is shortened to fit if it has to be."""
    if not refs:
        return ""
    head = ("\ncommunity references (Server Fault / Unix & Linux accepted answers, CC BY-SA; other hosts' fixes, never "
            "verified on this stack: hypotheses to check with read-only tools, not a licence to act):")
    lines, used = [head], len(head)
    for _, _, d in refs:
        line = line_for(d)
        if used + 1 + len(line) > budget:
            if lines == [head]:
                over = used + 1 + len(line) - budget
                line = line_for(d, max(60, len(d["answer_excerpt"]) - over - 1))
            else:
                break
        lines.append(line)
        used += 1 + len(line)
    return "\n".join(lines)


def references(query, k=2):
    try:
        if stack_covers(query):
            return ""
        return render(rank(query, k=k))
    except Exception:            # an extra on an extra: never break the tool
        return ""
