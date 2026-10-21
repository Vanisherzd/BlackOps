"""The tool layer pushes community references for symptoms no runbook owns (scripts/agent.py community_push)."""
import pathlib
import sys
import tempfile
import time
import types
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
if "openai" not in sys.modules:                 # agent.py imports the client at module level; these tests never call it
    try:
        import openai  # noqa: F401
    except Exception:
        sys.modules["openai"] = types.SimpleNamespace(OpenAI=object)
import agent  # noqa: E402
import community_knowledge  # noqa: E402

TOO_BIG = 'upstream sent too big header while reading response header from upstream, upstream: "http://172.24.0.3:80/", host: "127.0.0.1:8880"'
NOFILE = "socket() failed (24: Too many open files) while connecting to upstream"


class OwnedSymptomTest(unittest.TestCase):
    def test_runbook_symptoms_and_status_codes_are_owned(self):
        for q in ["connect() failed (111: Connection refused) while connecting to upstream",
                  "the service itself answers HTTP 502 Bad Gateway to requests, container is up.",
                  "directory index of /usr/share/nginx/html/ is forbidden"]:
            self.assertTrue(community_knowledge.owned_symptom(q), q)

    def test_entity_names_alone_do_not_own_an_error_line(self):
        for q in (TOO_BIG, NOFILE):
            self.assertFalse(community_knowledge.owned_symptom(q), q)
            self.assertTrue(community_knowledge.stack_covers(q), q)   # the wider gate says covered: why the push has its own


class CommunityPushTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.stub = types.SimpleNamespace(owned_symptom=community_knowledge.owned_symptom,
                                          references=lambda q, k=2, owned=None: self.calls.append(q) or "\ncommunity references (x):\n- A :: x\n- B :: y")
        self.real = sys.modules.get("community_knowledge")
        sys.modules["community_knowledge"] = self.stub
        self.saved = (agent.COMMUNITY_PUSH, agent.COMMUNITY_PUSH_TIMEOUT, agent.ROOT)
        agent._incident["community_seen"] = set()
        agent.COMMUNITY_PUSH, agent.COMMUNITY_PUSH_TIMEOUT = True, 4.0
        agent.ROOT = pathlib.Path(tempfile.mkdtemp())
        (agent.ROOT / "logs").mkdir()

    def tearDown(self):
        sys.modules["community_knowledge"] = self.real
        agent.COMMUNITY_PUSH, agent.COMMUNITY_PUSH_TIMEOUT, agent.ROOT = self.saved

    def test_pushes_once_per_symptom_and_drops_the_status_prefix(self):
        q = "the service itself answers HTTP 502 Bad Gateway to requests, container is up. " + TOO_BIG
        out = agent.community_push(q)
        self.assertIn("- A", out)
        self.assertEqual(self.calls, [TOO_BIG])
        self.assertEqual(agent.community_push(q), "")          # same symptom, same incident: once
        self.assertEqual(len(self.calls), 1)
        logged = (agent.ROOT / "logs" / "ops-api.log").read_text()
        self.assertIn('"tool": "community_push"', logged)
        self.assertIn("2 references", logged)

    def test_owned_symptoms_and_status_only_queries_push_nothing(self):
        self.assertEqual(agent.community_push("connect() failed (111: Connection refused) while connecting to upstream"), "")
        self.assertEqual(agent.community_push("the service itself answers HTTP 504 Gateway Timeout to requests, container is up."), "")
        self.assertEqual(self.calls, [])

    def test_failure_and_timeout_fail_open(self):
        def boom(q, k=2, owned=None):
            raise RuntimeError("sidecar down")
        self.stub.references = boom
        self.assertEqual(agent.community_push(NOFILE), "")
        agent._incident["community_seen"] = set()

        def slow(q, k=2, owned=None):
            time.sleep(0.4)
            return "late"
        self.stub.references = slow
        agent.COMMUNITY_PUSH_TIMEOUT = 0.05
        self.assertEqual(agent.community_push(NOFILE), "")

    def test_off_switch(self):
        agent.COMMUNITY_PUSH = False
        self.assertEqual(agent.community_push(NOFILE), "")
        self.assertEqual(self.calls, [])

    def test_reviewer_trace_never_sees_other_hosts_text(self):
        kept = agent.trace_summary("search_runbooks", "## runbook\nsteps\ncommunity references (x):\n- A :: other host")
        self.assertNotIn("other host", kept)


if __name__ == "__main__":
    unittest.main()
