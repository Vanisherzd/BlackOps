"""Self-check for the retrieval discipline in agent.py. Run on the Spark: uv run --no-sync scripts/test-retrieval-gate.py

Asserts the property the prompt could not buy: no action reaches the stack before a runbook has been seen.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import agent  # noqa: E402

CONF = "server {\n  listen 80;\n  location /api/ { proxy_pass http://edge-victim-app:80/; }\n}\n"


def main():
    agent._incident.update(runbook_seen=False, refusals=0)
    first = agent.run_tool("write_config", {"content": CONF}, dry_run=True)
    assert first.startswith("refused:"), f"a cold write_config must be refused, got: {first[:120]}"
    assert "nginx-config-error.md" in first, "the refusal must list the catalogue"
    assert not agent._incident["runbook_seen"], "a refusal alone must not count as having read anything"

    agent.run_tool("search_runbooks", {"query": "nginx emerg invalid directive"}, dry_run=True)
    assert agent._incident["runbook_seen"]
    second = agent.run_tool("write_config", {"content": CONF}, dry_run=True)
    assert second.startswith("DRY-RUN"), f"after a search the write must go through, got: {second[:120]}"

    agent._incident.update(runbook_seen=False, refusals=0)          # a stubborn agent is let through on the third try
    for _ in range(2):
        assert agent.run_tool("write_config", {"content": CONF}, dry_run=True).startswith("refused:")
    assert agent.run_tool("write_config", {"content": CONF}, dry_run=True).startswith("DRY-RUN"), "must not deadlock"

    for tool, args in (("docker_start", {"name": "edge-victim"}), ("recreate_stack", {}),
                       ("network_repair", {}), ("kill_process", {"pid": 999999}), ("cleanup_disk", {})):
        agent._incident.update(runbook_seen=False, refusals=0)
        out = agent.run_tool(tool, args, dry_run=True)
        assert out.startswith("refused:"), f"{tool} must be gated, got: {out[:120]}"

    agent._incident.update(runbook_seen=False, refusals=0)
    assert agent.run_tool("docker_ps", {}, dry_run=True) is not None            # read-only, never gated
    assert agent.run_tool("read_config", {}, dry_run=True) is not None

    # The knowledge pack (kb/): read-only, compact, keeps its own matched/insufficient verdict, never counts as a runbook.
    agent._incident.update(runbook_seen=False, refusals=0)
    kb = agent.run_tool("search_incident_knowledge", {"query": "write error: No space left on device, / at 97%"}, dry_run=True)
    assert kb.startswith("knowledge base (disk_full): matched") and "solution-linux-disk" in kb and "CHECK [R]" in kb, kb[:200]
    assert len(kb) <= 2600, f"the knowledge answer must stay small for a 32k window, got {len(kb)}"
    assert not agent._incident["runbook_seen"], "the knowledge pack is hypotheses, not this stack's runbook"
    assert agent._incident["trace"][-1]["tool"] == "search_incident_knowledge", "trace must record calls"
    kb = agent.run_tool("search_incident_knowledge", {"query": "printer toner low in tray two"}, dry_run=True)
    assert "insufficient" in kb.splitlines()[0] and "not a basis for repair" in kb, kb[:200]
    assert agent.run_tool("search_incident_knowledge", {}, dry_run=True).startswith("error:")
    assert agent.run_tool("write_config", {"content": CONF}, dry_run=True).startswith("refused:"), "knowledge alone must not open the gate"

    assert agent.symptom_query("all fine\nstarting up") is None, "clean output must not trigger an attachment"
    assert agent.symptom_query('nginx: [emerg] invalid number of arguments in "return" directive'), \
        "an emerg line is a symptom"

    noise = "\n".join(["/docker-entrypoint.sh: Configuration complete; ready for start up"] * 30
                      + ['nginx: [emerg] invalid number of arguments in "return" directive'] * 6)
    folded = agent.collapse(noise)
    assert folded.count("\n") == 1 and "(x30)" in folded and "(x6)" in folded, folded
    assert agent.sh("printf 'a%.0s' $(seq 1 5000); echo; echo LAST", limit=100).endswith("LAST"), "truncation must keep the end"

    import reviewer
    calls = []
    real = reviewer.review
    reviewer.review = lambda action, args, trace, seen: (calls.append(action) or (False, "not yet"))
    if os.environ.get("LIVE_STACK"):
        agent._incident.update(runbook_seen=True, refusals=0, trace=[{"tool": "check_health", "args": "{}", "result": "500"}])
        out = agent.run_tool("recreate_stack", {"name": "edge-victim-app", "replace": True}, dry_run=False)
        assert out.startswith("denied:") and "restarted" in out and not calls, f"a rebuild with no prior restart is refused in code, reviewer not consulted: {out[:120]}"
        agent._incident["trace"].append({"tool": "docker_restart", "args": '{"name": "edge-victim-app"}', "result": "still 500"})
        out = agent.run_tool("recreate_stack", {"name": "edge-victim-app", "replace": True}, dry_run=False)
        assert out.startswith("review-rejected:") and calls == ["recreate_stack"], f"after a restart the reviewer decides: {out[:120]}"
    reviewer.review = real
    recorded = [t["tool"] for t in agent._incident["trace"]]        # LIVE_STACK replaces the trace above, so only the tail is checked
    assert recorded[-1] == ("recreate_stack" if os.environ.get("LIVE_STACK") else "write_config"), f"last three: {recorded[-3:]}"

    for marker, want in agent.STATE_RUNBOOKS:              # state markers map to fixed runbooks, never a guess
        got = agent.attach_named("x", want)
        assert f"## {want}" in got, f"{marker} -> {want} must attach by name"
    assert agent.attach_named("x", "no-such.md") == "x"

    health = agent.run_tool("check_health", {}, dry_run=True)
    if '"front": 200' in health and '"api": 200' in health:
        assert not agent._incident["runbook_seen"], "green health must end the incident"
        print("green-health reset: checked")
    else:
        print(f"green-health reset: skipped, stack is not healthy right now ({health})")
    print("retrieval gate self-check passed")


if __name__ == "__main__":
    main()
