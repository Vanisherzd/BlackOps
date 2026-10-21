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

MIN_SHARED = 2          # a reference needs at least this many distinct query tokens in the document, else "no reference"
K1, B = 1.2, 0.75       # KnowledgeBaseV2's defaults
# This stack's own entities: a query naming them is a runbook matter, never a community one.
STACK_WORDS = ("edge-victim", "8880", "/api/", "/board/", "default.conf", "upstream")
GENERIC = {"the", "and", "for", "with", "not", "this", "that", "from", "are", "was", "has", "have", "its", "but",
           "when", "while", "after", "before", "into", "than", "then", "there", "error", "errors", "failed", "fails"}
SITE_HOST = {"serverfault": "serverfault.com", "unix": "unix.stackexchange.com"}


def _tokens():
    import sys
    sys.path.insert(0, str(ROOT / "kb" / "src"))
    from blackout_ops_kb.retrieval_v2 import tokens
    return tokens


class CommunityIndex:
    def __init__(self, docs):
        tokens = _tokens()
        self.tokens = tokens
        self.docs = docs
        self.tf = [Counter(tokens(d["index_text"])) for d in docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avglen = sum(self.len) / max(1, len(self.len))
        df = Counter(t for c in self.tf for t in c)
        n = len(docs)
        self.idf = {t: math.log((n + 1) / (f + 0.5)) for t, f in df.items()}

    @classmethod
    def load(cls, path=CORPUS):
        if os.environ.get("KB_COMMUNITY", "1") == "0" or not path.exists():
            return cls([])
        return cls([json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()])

    def search(self, query, k=2, min_shared=MIN_SHARED):
        """[(score, shared_tokens, doc)] best first; only documents sharing >= min_shared distinct query tokens."""
        q = Counter(self.tokens(query))
        if not q or not self.docs:
            return []
        out = []
        for i, tf in enumerate(self.tf):
            shared = [t for t in q if t in tf]
            if len(shared) < min_shared:
                continue
            s = 0.0
            for t in shared:
                f = tf[t]
                s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * self.len[i] / self.avglen))
            out.append((s, shared, self.docs[i]))
        out.sort(key=lambda x: -x[0])
        return out[:k]


_index = None


def index():
    global _index
    if _index is None:
        _index = CommunityIndex.load()
    return _index


def stack_covers(query):
    """True when the symptom is this stack's own: the query names one of its entities, or two or more of its
    non-generic words appear in one runbook's file name or title. Measured 2026-10-22: every Q_stage sentence and the
    agent's own 5xx phrasings are covered this way, while the hardware/network/platform probes are not."""
    if any(w in query for w in STACK_WORDS):
        return True
    terms = set(re.findall(r"[a-z0-9]+", query.lower())) - GENERIC
    try:
        import rag
        for name, title, _ in rag.documents():
            words = set(re.findall(r"[a-z0-9]+", (pathlib.Path(name).stem + " " + title).lower()))
            if len(terms & words) >= 2:
                return True
    except Exception:
        return True              # no runbooks readable: say nothing rather than something unchecked
    return False


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
        return render(index().search(query, k=k))
    except Exception:            # an extra on an extra: never break the tool
        return ""
