"""Self-check for the retrieval discipline in agent.py. Run on the Spark: uv run --no-sync scripts/test-retrieval-gate.py

Asserts the property the prompt could not buy: no action reaches the stack before a runbook has been seen.
"""
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

    assert agent.symptom_query("all fine\nstarting up") is None, "clean output must not trigger an attachment"
    assert agent.symptom_query('nginx: [emerg] invalid number of arguments in "return" directive'), \
        "an emerg line is a symptom"

    noise = "\n".join(["/docker-entrypoint.sh: Configuration complete; ready for start up"] * 30
                      + ['nginx: [emerg] invalid number of arguments in "return" directive'] * 6)
    folded = agent.collapse(noise)
    assert folded.count("\n") == 1 and "(x30)" in folded and "(x6)" in folded, folded
    assert agent.sh("printf 'a%.0s' $(seq 1 5000); echo; echo LAST", limit=100).endswith("LAST"), "truncation must keep the end"

    health = agent.run_tool("check_health", {}, dry_run=True)
    if '"front": 200' in health and '"api": 200' in health:
        assert not agent._incident["runbook_seen"], "green health must end the incident"
        print("green-health reset: checked")
    else:
        print(f"green-health reset: skipped, stack is not healthy right now ({health})")
    print("retrieval gate self-check passed")


if __name__ == "__main__":
    main()
