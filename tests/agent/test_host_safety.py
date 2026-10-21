"""Rollback and command compatibility tests; no Docker, network, or file cleanup."""
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from ops_safety import restore_config_file


class MemoryFile:
    def __init__(self, text):
        self.text = text

    def read_text(self):
        return self.text

    def write_text(self, text):
        self.text = text


class ConfigRestoreTest(unittest.TestCase):
    def test_previous_source_is_read_before_it_is_rotated(self):
        current = MemoryFile("broken config")
        previous = MemoryFile("previous healthy config")
        restore_config_file(current, previous, previous)
        self.assertEqual(current.text, "previous healthy config")
        self.assertEqual(previous.text, "broken config")

    def test_known_good_is_retained_and_current_is_backed_up(self):
        current, source, previous = (MemoryFile(v) for v in ("broken", "known good", "older"))
        restore_config_file(current, source, previous)
        self.assertEqual((current.text, source.text, previous.text), ("known good", "known good", "broken"))

    def test_unreadable_source_does_not_overwrite_current_or_backup(self):
        current, previous = MemoryFile("broken"), MemoryFile("older")
        source = Mock()
        source.read_text.side_effect = OSError("unreadable source")
        with self.assertRaises(OSError):
            restore_config_file(current, source, previous)
        self.assertEqual((current.text, previous.text), ("broken", "older"))

    def test_backup_failure_leaves_active_config_untouched(self):
        current, source = MemoryFile("broken"), MemoryFile("healthy")
        previous = Mock()
        previous.write_text.side_effect = OSError("cannot back up")
        with self.assertRaises(OSError):
            restore_config_file(current, source, previous)
        self.assertEqual(current.text, "broken")


class HostDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No model calls are made. Missing SDK imports use an inert stand-in.
        try:
            import openai
        except ModuleNotFoundError as error:
            if error.name != "openai":
                raise
            sdk = types.ModuleType("openai")
            sdk.OpenAI = Mock(side_effect=AssertionError("LLM calls are forbidden in host-tool tests"))
            with patch.dict(sys.modules, {"openai": sdk}):
                cls.agent = cls.load_agent()
        else:
            cls.agent = cls.load_agent()

    @staticmethod
    def load_agent():
        spec = importlib.util.spec_from_file_location("host_agent_safety_under_test", SCRIPTS / "agent.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_existing_diagnostic_forms_are_not_newly_denied(self):
        commands = [
            "nginx -t", "nginx -t -c /tmp/custom.conf", "df -h /", "du -sh /var/cache/nginx /tmp",
            "curl -L http://edge-victim-app/", "curl -H 'Host: backend' http://edge-victim-app/",
            "curl -X POST http://edge-victim-app/diagnostic", "curl -o /tmp/response http://edge-victim-app/",
            "curl --config /tmp/curlrc http://edge-victim-app/", "env LANG=C nginx -t",
            "tail -f /var/log/nginx/error.log",
        ]
        for command in commands:
            with self.subTest(command=command), patch.object(self.agent, "sh", return_value="mock diagnostic") as run:
                result = self.agent._run_tool("docker_exec", {"name": "edge-victim-app", "cmd": command}, False)
                self.assertEqual(result, "mock diagnostic")
                run.assert_called_once()

    def test_original_container_and_shell_operator_guards_remain(self):
        cases = [
            {"name": "vllm-edge", "cmd": "id"},
            {"name": "edge-victim-app", "cmd": "ls / ; id"},
            {"name": "edge-victim-app", "cmd": "cat $(id)"},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments), patch.object(self.agent, "sh") as run:
                result = self.agent._run_tool("docker_exec", arguments, False)
                self.assertTrue(result.startswith("denied:"))
                run.assert_not_called()

    def test_restore_dispatch_reads_backup_before_rotation(self):
        previous, conf_dir, good_dir = Mock(), pathlib.PurePosixPath("/fake/conf"), Mock()
        previous.name = "previous.conf"
        with patch.dict(self.agent._incident, runbook_seen=True), patch.object(self.agent, "PREVIOUS", previous), patch.object(self.agent, "CONF_DIR", conf_dir), patch.object(self.agent, "KNOWN_GOOD_DIR", good_dir), patch.object(self.agent, "restore_config_file") as restore:
            result = self.agent._run_tool("restore_config", {"source": "previous"}, False)
        self.assertTrue(result.startswith("restored previous.conf"))
        restore.assert_called_once_with(conf_dir / "default.conf", previous, previous)
        previous.write_text.assert_not_called()

    def test_restore_dry_run_never_writes_files(self):
        previous = Mock()
        previous.name = "previous.conf"
        with patch.dict(self.agent._incident, runbook_seen=True), patch.object(self.agent, "PREVIOUS", previous), patch.object(self.agent, "restore_config_file") as restore:
            result = self.agent._run_tool("restore_config", {"source": "previous"}, True)
        self.assertTrue(result.startswith("DRY-RUN"))
        restore.assert_not_called()

    def test_missing_backup_does_not_write_config(self):
        previous = Mock()
        previous.exists.return_value = False
        with patch.dict(self.agent._incident, runbook_seen=True), patch.object(self.agent, "PREVIOUS", previous), patch.object(self.agent, "restore_config_file") as restore:
            result = self.agent._run_tool("restore_config", {"source": "previous"}, False)
        self.assertTrue(result.startswith("error: no previous"))
        restore.assert_not_called()


if __name__ == "__main__":
    unittest.main()
