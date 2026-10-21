"""Community reference layer: Server Fault / Unix & Linux accepted answers, for symptoms no runbook or repair card
covers. A separate BM25 index (same tokenizer and parameters as the pack's KnowledgeBaseV2, IDF computed only over
these documents), so loading it changes no score of any existing document. Shown only by search_incident_knowledge
and only when no card matched; knowledge_note never consults it. KB_COMMUNITY=0 disables it; a missing corpus file
disables it silently. Design and measurements: docs/kb-community-20261022.md."""
import json
import math
import os
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "kb" / "artifacts" / "community_v1.jsonl"
sys.path.insert(0, str(ROOT / "kb" / "src"))
from blackout_ops_kb.retrieval_v2 import tokens  # noqa: E402

MIN_SHARED = 2          # a reference needs at least this many distinct query tokens in the document, else "no reference"
K1, B = 1.2, 0.75       # KnowledgeBaseV2's defaults


class CommunityIndex:
    def __init__(self, docs):
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
        q = Counter(tokens(query))
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


def render(refs, budget=700):
    """The lines search_incident_knowledge appends when no card matched. Excerpts, not instructions: the agent still
    has to confirm with read-only tools and stay inside its own tool contracts."""
    if not refs:
        return ""
    lines = ["\ncommunity references (Server Fault / Unix & Linux accepted answers, CC BY-SA; other hosts' fixes, never "
             "verified on this stack: hypotheses to check with read-only tools, not a licence to act):"]
    for _, _, d in refs:
        lines.append(f"- {d['title']} [{d['site']}, score {d['score']}] :: {d['answer_excerpt']} — {d['link']}")
    out = "\n".join(lines)
    return out if len(out) <= budget else out[:budget - 6] + " (cut)"


def references(query, k=2):
    try:
        return render(index().search(query, k=k))
    except Exception:            # an extra on an extra: never break the tool
        return ""
