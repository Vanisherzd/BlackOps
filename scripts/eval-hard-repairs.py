"""Offline retrieval + tool-contract checks. Does not invoke an LLM, Docker, fault injection, or repair tools."""
import ast
import json
import pathlib
import sys
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "kb" / "src")]
from repair_knowledge import documents
from blackout_ops_kb.retrieval_v2 import AGENT_DEFAULTS, KnowledgeBaseV2

DATA = ROOT / "datasets" / "curated" / "hard_repairs"


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tool_contracts():
    tree = ast.parse((ROOT / "scripts" / "agent.py").read_text(encoding="utf-8"))
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "TOOLS" for t in n.targets))
    return {t["name"]: t["parameters"] for t in ast.literal_eval(assignment.value)}


def validate_cards(docs):
    tools = tool_contracts()
    ids = [doc["id"] for doc in docs]
    assert len(set(ids)) == len(ids), "duplicate repair knowledge id"
    calls = 0
    for doc in docs:
        card = doc["metadata"]
        assert card["historical_failure"]["report"] and not card["live_bolt_verified"]
        for src in card["sources"]:
            parsed = urlparse(src["url"])
            assert parsed.scheme == "https" and parsed.hostname in {"nginx.org", "docs.docker.com"}
        assert card["required_evidence"] and card["rollback"] and card["verify"] and card["escalate"]
        for step in card["tool_path"]:
            assert step["when"] and step["stage"]
            schema, args = tools[step["tool"]], step["args"]
            assert set(schema.get("required", [])) <= args.keys(), step
            assert args.keys() <= schema["properties"].keys(), step
            for key, value in args.items():
                field = schema["properties"][key]
                if field["type"] == "string":
                    assert isinstance(value, str), step
                elif field["type"] == "integer":
                    assert isinstance(value, int) and not isinstance(value, bool), step
                elif field["type"] == "boolean":
                    assert isinstance(value, bool), step
                if "enum" in field:
                    assert value in field["enum"], step
            if "name" in args:
                assert args["name"] in {"edge-victim", "edge-victim-app"}, step
            # This extension has no file-deletion examples, shell launchers, or newly invented action tools.
            assert step["tool"] not in {"cleanup_disk", "clear_scratch_volume", "kill_process", "self_heal"}
            calls += 1
    return calls


def selected(bundle):
    return (bundle["solutions"][0]["id"]
            if bundle["knowledge_match"]["status"] == "matched" and bundle["solutions"] else None)


def evaluate():
    docs = documents()
    calls = validate_cards(docs)
    inputs = rows(DATA / "eval_inputs.jsonl")
    truth = {r["id"]: r for r in rows(DATA / "eval_ground_truth.jsonl")}
    assert len(truth) == len(inputs) and {r["id"] for r in inputs} == truth.keys()
    assert all(set(row) == {"id", "provenance", "observation"} for row in inputs), "answer in model input"
    indexed_text = json.dumps(docs, ensure_ascii=False)
    assert all(row["id"] not in indexed_text and row["observation"] not in indexed_text for row in inputs)
    assert all("expected_card" not in json.dumps(d) for d in docs), "evaluator answer in runtime corpus"
    results = []
    negative = json.loads((DATA / "negative_queries.json").read_text(encoding="utf-8"))
    for version in ("v3", "v4"):
        corpus = ROOT / "kb" / "artifacts" / f"corpus_{version}.jsonl"
        before = KnowledgeBaseV2(corpus, **AGENT_DEFAULTS)
        after = KnowledgeBaseV2(corpus, extra_documents=docs, **AGENT_DEFAULTS)
        cases = []
        for row in inputs:
            query = {"query": row["observation"]}  # No id, expected card, cause or repair path is submitted to RAG.
            baseline = selected(before.retrieve(query, case_k=0, solution_k=3))
            got = selected(after.retrieve(query, case_k=0, solution_k=3))
            cases.append(dict(id=row["id"], before=baseline, after=got, correct=got == truth[row["id"]]["expected_card"]))
        false_matches = [q for q in negative if (selected(after.retrieve({"query": q}, case_k=0, solution_k=3)) or "").startswith("hard-repair-")]
        results.append(dict(version=version, correct=sum(c["correct"] for c in cases), total=len(cases),
                            negative_queries=len(negative), false_hard_matches=false_matches, cases=cases))
    return dict(mode="offline_retrieval_and_tool_contracts_only", knowledge_cards=len(docs),
                validated_tool_examples=calls, eval_answers_isolated=True, results=results,
                llm_invoked=False, live_repair_performed=False,
                limitation="Unseen wording/path variants of known fault families; not a repair-success or unseen-family benchmark.")


if __name__ == "__main__":
    report = evaluate()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(any(r["correct"] != r["total"] or r["false_hard_matches"] for r in report["results"]))
