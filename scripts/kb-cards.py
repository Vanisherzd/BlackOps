"""Repair cards for this stack, added to the knowledge pack: kb/artifacts/corpus_v3.jsonl -> corpus_v4.jsonl.

  .venv/bin/python scripts/kb-cards.py build     # write corpus_v4.jsonl, print what was linked
  .venv/bin/python scripts/kb-cards.py phrases   # each card symptom phrase's weight against the card bar
  .venv/bin/python scripts/kb-cards.py experience  # verified fixes from the drill logs -> kb/cards/stack_experience.json
  .venv/bin/python scripts/kb-cards.py eval      # which card is pushed: public held-out incidents, the agent's queries

The pack's 1,557 cases come from public AIOps benchmarks (Kubernetes microservices) and say what broke, never how it
was fixed; its 17 cards do carry fixes, as kubectl and shell commands the agent cannot run. kb/cards/ holds cards
written per public fault family, but with checks and fixes in this stack's own tools, an escalation line for what no
tool can fix, and the public fault types they cover. `build` adds them as solution documents and links every case of
a covered fault type to its card (metadata.repair_card), so a retrieved case points at a fix.

`experience` adds what only this stack can supply: the fixes its own drills verified. Each becomes a case document
(stack-<drill case>) and a `proven_here` line on the card of its kind.

A card reaches the agent only when one of its symptom phrases is in the query whole and weighs at least the bar
(AGENT_DEFAULTS phrase_bar, 8): `phrases` lists the weights, so a phrase too common to ever match can be reworded.
"""
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ART = ROOT / "kb" / "artifacts"
V3, V4 = ART / "corpus_v3.jsonl", ART / "corpus_v4.jsonl"
EXPERIENCE = ROOT / "kb" / "cards" / "stack_experience.json"
CARDS = sorted(p for p in (ROOT / "kb" / "cards").glob("*.json") if p != EXPERIENCE)


def fault_type(doc):
    return (doc.get("metadata") or {}).get("fault_type") or next(iter(doc.get("labels") or []), None)


def card_doc(c):
    """A card in the corpus's solution shape: the content text is what BM25 indexes, the metadata what is shown."""
    lines = [f"Scenario: {c['scenario']}", "Symptoms: " + "; ".join(c["symptoms"]),
             "Root causes: " + "; ".join(c["root_causes"])]
    lines += [f"CHECK [{x['level']}] {x['command']} :: {x['purpose']}" for x in c["checks"]]
    lines += [f"FIX [{x['level']}] {x['action']} :: approval={x['approval_required']}" for x in c["remediation"]]
    lines += ["Rollback: " + "; ".join(c["rollback"]), "Verify: " + "; ".join(c["verify"]),
              "Safety: " + "; ".join(c["safety"]), "Escalate: " + "; ".join(c["escalate"]),
              "Public fault types: " + "; ".join(c["fault_types"])]
    return {"id": c["id"], "doc_type": "solution", "scenario_v1": c["scenario"], "platform": c["platform"],
            "title": c["title"], "content": "\n".join(lines), "tags": " ".join(c["tags"]),
            "source": "Blackout Ops repair card for this Docker stack (public fault families)",
            "risk_tier": c["risk_tier"], "labels": [], "metadata": c, "duplicates": [], "scenario": c["scenario"],
            "has_evidence": True}


def load_cards():
    cards = [c for p in CARDS for c in json.loads(p.read_text(encoding="utf-8"))]
    ids = [c["id"] for c in cards]
    assert len(ids) == len(set(ids)), "duplicate card id"
    owner = {}
    for c in cards:
        for t in c["fault_types"]:
            assert t not in owner, f"fault type {t!r} claimed by {owner.get(t)} and {c['id']}"
            owner[t] = c["id"]
    return cards, owner


def experience_doc(e):
    """A verified fix from this stack's own drills, as a case document."""
    lines = [f"Incident on this stack (drill {e['case']})", f"Symptoms: health at injection {e['symptom']}"]
    if e["cause"]:
        lines.append(f"Root cause: {e['cause']}")
    lines.append(f"Fixed by: {e['fix']} ({e['fix_runs']} of {e['verified_runs']} verified runs)")
    return {"id": f"stack-{e['case']}", "doc_type": "case", "scenario_v1": "other", "platform": "docker",
            "title": f"this stack: {e['case']} fixed by {e['fix']}", "content": "\n".join(lines), "tags": e["case"],
            "source": "this stack's drills (autonomous, cause named, shortest fix)", "risk_tier": None, "labels": [],
            "metadata": {"repair_card": e["card"], "fix": e["fix"], "verified_runs": e["verified_runs"],
                         "example": e["example"]},
            "duplicates": [], "scenario": "other", "has_evidence": True}


def build(_):
    cards, owner = load_cards()
    exp = json.loads(EXPERIENCE.read_text(encoding="utf-8")) if EXPERIENCE.exists() else []
    for c in cards:
        c["proven_here"] = [f"{e['case']}: {e['fix']} ({e['fix_runs']}/{e['verified_runs']} runs)"
                            for e in exp if e["card"] == c["id"]]
    docs = [json.loads(l) for l in V3.read_text(encoding="utf-8").splitlines() if l.strip()]
    known = collections.Counter(fault_type(d) for d in docs if d["doc_type"] == "case")
    unknown = sorted(t for t in owner if t not in known)
    linked = collections.Counter()
    for d in docs:
        if d["doc_type"] == "case" and fault_type(d) in owner:
            d.setdefault("metadata", {})["repair_card"] = owner[fault_type(d)]
            linked[owner[fault_type(d)]] += 1
    docs = [d for d in docs if d["id"] not in owner.values()] + [card_doc(c) for c in cards]
    docs += [experience_doc(e) for e in exp]
    with open(V4, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    n_cases = sum(known.values())
    print(f"{len(cards)} cards; {sum(linked.values())}/{n_cases} cases linked to one "
          f"({100 * sum(linked.values()) / n_cases:.0f}%)")
    for c in cards:
        print(f"  {c['id']}: {linked[c['id']]} cases")
    uncovered = [(t, n) for t, n in known.most_common() if t not in owner]
    print("fault types with no card:", ", ".join(f"{t} ({n})" for t, n in uncovered) or "none")
    if unknown:
        print("card fault types not in the pack:", ", ".join(unknown))
    print(f"{len(exp)} verified fixes from this stack's drills, {sum(bool(e['card']) for e in exp)} under a card")
    print(f"wrote {V4}")


def phrases(_):
    sys.path.insert(0, str(ROOT / "kb" / "src"))
    from blackout_ops_kb.retrieval_v2 import AGENT_DEFAULTS, KnowledgeBaseV2, tokens
    kb = KnowledgeBaseV2(V4, **AGENT_DEFAULTS)
    bar = AGENT_DEFAULTS["phrase_bar"]
    cards, _ = load_cards()
    low = 0
    for c in cards:
        print(c["id"])
        for p in c["symptoms"]:
            w = sum(kb.idf[t] for t in set(tokens(p)))
            low += w < bar
            print(f"  {w:5.1f} {'ok ' if w >= bar else 'LOW'} {p}")
    print(f"{low} phrases under the bar of {bar}")


def pushed(kb, query, k=int(__import__("os").environ.get("CARD_K", "6"))):
    """The card knowledge_note would push for this query, or None (same call and rule as scripts/agent.py: six candidates, so a
    stack card outranked on BM25 by a generic one still reaches the phrase test)."""
    b = kb.retrieve({"query": query}, case_k=0, solution_k=k)
    return b["solutions"][0]["id"] if b["knowledge_match"]["status"] == "matched" and b["solutions"] else None


def drill_queries():
    """(case, query) for every search_runbooks / search_incident_knowledge call the sandbox agent made in a drill."""
    import re
    out = []
    for log in sorted((ROOT / "logs").glob("drills-*.log")):
        case = None
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"=== \S+ reset \+ inject (\S+)", line)
            if m:
                case = m.group(1)
            m = re.match(r"\S+ \S+ (search_runbooks|search_incident_knowledge) (\{.*?\}) ->", line)
            if m and case:
                try:
                    out.append((case, json.loads(m.group(2)).get("query", "")))
                except ValueError:
                    pass
    return out


def evaluate(_):
    """Which card is pushed, on public held-out incidents (is it the card of the incident's fault type?) and on the
    agent's own drill queries (listed for review: a drill case is this stack's, not a public fault type)."""
    import importlib.util
    sys.path.insert(0, str(ROOT / "kb" / "src"))
    from blackout_ops_kb.retrieval_v2 import AGENT_DEFAULTS, KnowledgeBaseV2
    spec = importlib.util.spec_from_file_location("kre", ROOT / "scripts" / "kb-retrieval-eval.py")
    kre = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kre)
    _, owner = load_cards()
    kbs = {"v3": KnowledgeBaseV2(V3, **AGENT_DEFAULTS), "v4": KnowledgeBaseV2(V4, **AGENT_DEFAULTS)}
    lines = []
    for split in ("dev", "eval"):
        cases = kre.load_split(split)
        for qname, qmap in kre.queries(split, cases).items():
            for name, kb in kbs.items():
                n = push = right = card = 0
                for c in cases:
                    q = qmap.get(c["id"])
                    if not q:
                        continue
                    n += 1
                    got = pushed(kb, q)
                    truth = owner.get(c["metadata"].get("fault_type") or next(iter(c["labels"]), None))
                    push += got is not None
                    card += bool(got and got.startswith("card-"))
                    right += bool(got and got == truth)
                lines.append(f"{split:4} {qname:18} {name}: {n} incidents, card pushed {push}, a stack repair card "
                             f"{card}, the incident's own card {right}, another card {card - right}")
    print("\n".join(lines))
    print("\nagent drill queries (v3 -> v4):")
    seen = set()
    for case, q in drill_queries():
        if (case, q) in seen:
            continue
        seen.add((case, q))
        a, b = pushed(kbs["v3"], q), pushed(kbs["v4"], q)
        if a or b:
            print(f"  {case:24} {q[:70]!r}: {a} -> {b}")
    print(f"  ({len(seen)} distinct queries; the rest push nothing in either)")


# Which repair card a drill case is an instance of; a case with no card of its kind (a syntax error, a paused
# container) still becomes an experience record, it just hangs under no card.
DRILL_CARD = {
    "nginx-stopped": "card-docker-container-killed-signal", "upstream-down": "card-docker-container-killed-signal",
    "upstream-sigkill": "card-docker-container-killed-signal", "upstream-oom": "card-docker-container-oom",
    "bad-upstream-name": "card-docker-name-resolution-partition", "upstream-renamed": "card-docker-name-resolution-partition",
    "upstream-detached": "card-docker-name-resolution-partition", "network-removed": "card-docker-name-resolution-partition",
    "upstream-wrong-port": "card-docker-port-mismatch", "api-path-404": "card-docker-misrouted-request",
    "upstream-index-deleted": "card-docker-upstream-content-4xx", "upstream-500": "card-docker-app-5xx-defect",
    "front-include-missing": "card-docker-missing-config-reference", "inode-exhausted": "card-docker-disk-io",
}


# The scorer's cause regex is lenient: "killed by the OOM killer" passes for a SIGKILL. A root-cause sentence that names
# the wrong mechanism or the wrong container must not become this stack's record of the incident.
WRONG_CAUSE = {"upstream-sigkill": r"(?i)oom|out of memory", "front-paused": r"(?i)edge.victim.app",
               "upstream-down": r"(?i)oom|out of memory", "nginx-stopped": r"(?i)oom|out of memory"}


def short_call(c):
    """docker_restart edge-victim, recreate_stack edge-victim-app replace=true, write_config (full file)."""
    try:
        args = json.loads(c["args"]) if c["args"] else {}
    except ValueError:
        args = {}
    if "content" in args:
        return f"{c['tool']} (full corrected file)"
    parts = [str(args.pop("name"))] if "name" in args else []
    parts += [f"{k}={str(v).lower()}" for k, v in args.items()]
    return " ".join([c["tool"], *parts])


def experience(_):
    """kb/cards/stack_experience.json from the drill logs: per drill case, the runs the agent fixed on its own and
    explained correctly (scorer outcome autonomous, cause named,
    efficiency 1.0: no state change beyond the shortest fix), the state-changing calls they executed, the symptom
    at injection and one root-cause sentence. Regenerate after new drills; review the file before committing it."""
    import re
    sys.path.insert(0, str(ROOT / "scripts"))
    import score
    runs = collections.defaultdict(list)
    for log in sorted((ROOT / "logs").glob("drills-*.log")):
        text = log.read_text(encoding="utf-8", errors="replace")
        for m in score.BLOCK_RE.finditer(text):
            start, case, body = m.groups()
            r = score.score_block(case, start, body)
            if not r or r["outcome"] != "autonomous" or not r["cause_named"] or r["efficiency"] != 1.0:
                continue
            turn = body.split("=== ops-api.log", 1)[-1]
            actions = [short_call(c) for c in score.parse_calls(turn) if c["status"] == "ok" and c["tool"] in score.MUTATING]
            sym = re.search(r"injected \S+: (/ \S+ /api/ \S+)", body)
            cause = re.search(r"(?im)^\W*root cause\W*[:\-]\s*(.+)$", body)
            runs[case].append({"log": log.name, "start": start, "seconds": r["seconds"], "actions": actions,
                               "symptom": sym.group(1) if sym else None,
                               "cause": cause.group(1).strip()[:300] if cause else None})
    out = []
    for case, rs in sorted(runs.items()):
        seqs = collections.Counter(" ; ".join(r["actions"]) for r in rs)
        best, n = seqs.most_common(1)[0]
        ok = [r for r in rs if " ; ".join(r["actions"]) == best and r["cause"]
              and not re.search(WRONG_CAUSE.get(case, r"(?!)"), r["cause"])]
        example = ok[0] if ok else {**rs[0], "cause": None}
        out.append({"case": case, "card": DRILL_CARD.get(case), "verified_runs": len(rs),
                    "fix": best, "fix_runs": n, "symptom": example["symptom"], "cause": example["cause"],
                    "example": f"{example['log']} {example['start']}"})
    EXPERIENCE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for e in out:
        print(f"{e['case']:24} {e['verified_runs']:2} runs; fix ({e['fix_runs']}): {e['fix'][:90]}")
    print(f"wrote {EXPERIENCE}")


if __name__ == "__main__":
    {"build": build, "phrases": phrases, "eval": evaluate, "experience": experience}[sys.argv[1]](sys.argv)
