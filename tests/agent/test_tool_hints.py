"""Pure helpers added 2026-10-22: hosts oddities and the health probe's timeout wording."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("HEALTHY_GUARD", "0")
if "openai" not in sys.modules:                 # agent.py imports the client at module level; these tests never call it
    try:
        import openai  # noqa: F401
    except ImportError:
        import types
        sys.modules["openai"] = types.SimpleNamespace(OpenAI=object)
import agent  # noqa: E402


class HostsOdditiesTest(unittest.TestCase):
    DEFAULT = ("127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost ip6-loopback\nfe00::0\tip6-localnet\n"
               "ff00::0\tip6-mcastprefix\nff02::1\tip6-allnodes\nff02::2\tip6-allrouters\n172.24.0.3\t3dbf51d611ba\n")

    def test_docker_defaults_are_not_odd(self):
        self.assertEqual(agent.hosts_oddities(self.DEFAULT), [])

    def test_hand_written_stack_entry_is_odd(self):
        odd = agent.hosts_oddities(self.DEFAULT + "10.255.255.1 edge-victim-app\n# a comment\n")
        self.assertEqual(odd, ["10.255.255.1 edge-victim-app"])


class HealthTimeoutWordingTest(unittest.TestCase):
    def test_timeout_is_named_not_called_unreachable(self):
        import socket
        old = agent.urllib.request.build_opener
        class Opener:
            def open(self, url, timeout=None):
                raise socket.timeout("timed out")
        agent.urllib.request.build_opener = lambda *a, **k: Opener()
        try:
            text = agent.http_status("http://127.0.0.1:1/")
        finally:
            agent.urllib.request.build_opener = old
        self.assertIn("timed out after", text)
        self.assertIn("not a refused connection", text)

    def test_refusal_stays_unreachable(self):
        old = agent.urllib.request.build_opener
        class Opener:
            def open(self, url, timeout=None):
                raise ConnectionRefusedError(111, "refused")
        agent.urllib.request.build_opener = lambda *a, **k: Opener()
        try:
            text = agent.http_status("http://127.0.0.1:1/")
        finally:
            agent.urllib.request.build_opener = old
        self.assertEqual(text, "unreachable (ConnectionRefusedError)")


if __name__ == "__main__":
    unittest.main()


class RepairImageFileTest(unittest.TestCase):
    def test_repairable_paths(self):
        self.assertTrue(agent.repairable_path("edge-victim-app", "/etc/nginx/conf.d/default.conf"))
        self.assertTrue(agent.repairable_path("edge-victim-app", "/usr/share/nginx/html/index.html"))
        self.assertTrue(agent.repairable_path("edge-victim", "/etc/nginx/nginx.conf"))
        self.assertFalse(agent.repairable_path("edge-victim", "/etc/nginx/conf.d/default.conf"))   # the front end's mounted host file
        self.assertFalse(agent.repairable_path("edge-victim-app", "/etc/passwd"))
        self.assertFalse(agent.repairable_path("edge-victim-app", "/etc/nginx/../passwd"))

    def test_rebuild_waits_for_the_in_place_repair(self):
        import faults
        from unittest import mock
        import json
        agent._incident.update(trace=[{"tool": "docker_restart", "args": json.dumps({"name": "edge-victim-app"}), "result": "edge-victim-app restarted"}], runbook_seen=True, injected=[])
        with mock.patch.object(faults, "layer_changes", return_value=[("/etc/nginx/conf.d/default.conf", "changed")]), \
             mock.patch.object(agent, "sh", return_value=""), mock.patch.dict(os.environ, {"HEALTHY_GUARD": "0", "GUARDRAILS": "0"}):
            out = agent._run_tool("recreate_stack", {"name": "edge-victim-app", "replace": True}, False)
        self.assertTrue(out.startswith("denied:"), out)
        self.assertIn("repair_image_file", out)
