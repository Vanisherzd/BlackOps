import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "kb_community_under_test", ROOT / "scripts" / "kb-community.py"
)
assert SPEC and SPEC.loader
kb_community = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kb_community)


class CommunityDumpTests(unittest.TestCase):
    def test_fetch_dump_writes_only_eligible_questions_with_accepted_answers(self):
        posts_xml = """<?xml version="1.0" encoding="utf-8"?>
<posts>
  <row Id="72" PostTypeId="2" ParentId="7" CreationDate="2020-06-01T11:00:00.000" Score="9" Body="&lt;p&gt;Check kubelet logs, restore network connectivity, and wait for the node to report Ready before rescheduling workloads.&lt;/p&gt;" OwnerUserId="41" ContentLicense="CC BY-SA 4.0" />
  <row Id="1" PostTypeId="1" AcceptedAnswerId="2" CreationDate="2019-03-01T10:00:00.000" Score="7" Body="&lt;p&gt;nginx fails: &lt;code&gt;bind() to 0.0.0.0:80 failed (98: Address already in use)&lt;/code&gt;&lt;/p&gt;" OwnerUserId="10" Title="nginx will not start: address already in use" Tags="&lt;nginx&gt;&lt;linux&gt;" ContentLicense="CC BY-SA 4.0" />
  <row Id="2" PostTypeId="2" ParentId="1" CreationDate="2019-03-01T12:00:00.000" Score="11" Body="&lt;p&gt;Use ss -ltnp to identify the process holding port 80, then stop the duplicate service before restarting nginx safely.&lt;/p&gt;" OwnerUserId="11" ContentLicense="CC BY-SA 4.0" />
  <row Id="3" PostTypeId="1" CreationDate="2021-01-01T10:00:00.000" Score="1" Body="&lt;p&gt;nginx failed during startup.&lt;/p&gt;" OwnerUserId="10" Title="nginx failed" Tags="&lt;nginx&gt;" ContentLicense="CC BY-SA 4.0" />
  <row Id="4" PostTypeId="1" AcceptedAnswerId="42" CreationDate="2021-01-02T10:00:00.000" Score="2" Body="&lt;p&gt;The desktop failed to boot.&lt;/p&gt;" OwnerUserId="10" Title="Windows failed to boot" Tags="&lt;windows-10&gt;" ContentLicense="CC BY-SA 4.0" />
  <row Id="5" PostTypeId="1" AcceptedAnswerId="52" CreationDate="2010-03-01T10:00:00.000" Score="3" Body="&lt;p&gt;Network failed: no route to host after an upgrade.&lt;/p&gt;" OwnerUserId="20" Title="Linux network failed after upgrade" Tags="&lt;linux&gt;&lt;networking&gt;" />
  <row Id="52" PostTypeId="2" ParentId="5" CreationDate="2010-03-02T10:00:00.000" Score="5" Body="&lt;p&gt;Restore the missing default route, verify the gateway is reachable, and persist the corrected network configuration for the next boot.&lt;/p&gt;" OwnerUserId="21" />
  <row Id="6" PostTypeId="1" AcceptedAnswerId="62" CreationDate="2022-04-01T10:00:00.000" Score="4" Body="&lt;p&gt;The systemd service failed to start.&lt;/p&gt;" OwnerUserId="30" Title="systemd service failed" Tags="&lt;systemd&gt;" ContentLicense="CC BY-SA 4.0" />
  <row Id="62" PostTypeId="2" ParentId="6" CreationDate="2022-04-01T11:00:00.000" Score="1" Body="&lt;p&gt;Restart it.&lt;/p&gt;" OwnerUserId="31" ContentLicense="CC BY-SA 4.0" />
  <row Id="7" PostTypeId="1" AcceptedAnswerId="72" CreationDate="2020-06-01T10:00:00.000" Score="8" Body="&lt;p&gt;The node is not responding and workloads cannot start.&lt;/p&gt;" OwnerUserId="40" Title="Kubernetes node is NotReady and not responding" Tags="&lt;kubernetes&gt;&lt;networking&gt;" ContentLicense="CC BY-SA 4.0" />
</posts>
"""
        users_xml = """<?xml version="1.0" encoding="utf-8"?>
<users>
  <row Id="10" DisplayName="Alice Admin" /><row Id="11" DisplayName="Bob Resolver" />
  <row Id="20" DisplayName="Old Ops" /><row Id="21" DisplayName="Archive Expert" />
  <row Id="30" DisplayName="Short Question" /><row Id="31" DisplayName="Brief Answer" />
  <row Id="40" DisplayName="Cluster Operator" /><row Id="41" DisplayName="Early Answer" />
</users>
"""
        with tempfile.TemporaryDirectory() as directory:
            temporary = pathlib.Path(directory)
            posts = temporary / "Posts.xml"
            users = temporary / "Users.xml"
            posts.write_text(posts_xml, encoding="utf-8")
            users.write_text(users_xml, encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()

            with patch.object(kb_community, "DATA", temporary), patch.object(
                kb_community, "ROOT", temporary
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                kb_community.fetch_dump(
                    ["x", "fetch-dump", "serverfault", str(posts), str(users)]
                )

            rows = [
                json.loads(line)
                for line in (temporary / "dump-serverfault.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            summary = json.loads(stdout.getvalue())

        self.assertEqual([row["question_id"] for row in rows], [1, 5, 7])
        self.assertEqual(summary["rows"], 3)
        self.assertEqual(summary["dropped_short_answer"], 1)
        self.assertIn("4 incident-like questions", stderr.getvalue())

        expected_fields = {
            "site", "question_id", "title", "tags", "category", "score",
            "creation_date", "link", "owner", "license", "question_text",
            "symptom_lines", "accepted_answer_id", "answer_text", "answer_score",
            "answer_owner", "answer_license",
        }
        for row in rows:
            with self.subTest(question_id=row["question_id"]):
                self.assertEqual(set(row), expected_fields)
                self.assertEqual(row["site"], "serverfault")
                self.assertIsInstance(row["creation_date"], int)
                self.assertTrue(row["symptom_lines"])

        nginx, old, out_of_order = rows
        self.assertEqual(nginx["title"], "nginx will not start: address already in use")
        self.assertEqual(nginx["tags"], ["nginx", "linux"])
        self.assertEqual(nginx["category"], "service")
        self.assertEqual(nginx["score"], 7)
        self.assertEqual(nginx["link"], "https://serverfault.com/questions/1")
        self.assertEqual(nginx["owner"], "Alice Admin")
        self.assertEqual(nginx["license"], "CC BY-SA 4.0")
        self.assertIn("bind() to 0.0.0.0:80 failed", nginx["question_text"])
        self.assertTrue(any("bind()" in line for line in nginx["symptom_lines"]))
        self.assertEqual(nginx["accepted_answer_id"], 2)
        self.assertIn("identify the process holding port 80", nginx["answer_text"])
        self.assertEqual(nginx["answer_score"], 11)
        self.assertEqual(nginx["answer_owner"], "Bob Resolver")
        self.assertEqual(nginx["answer_license"], "CC BY-SA 4.0")

        self.assertEqual(old["question_id"], 5)
        self.assertEqual(old["license"], "CC BY-SA 2.5")
        self.assertEqual(old["answer_license"], "CC BY-SA 2.5")
        self.assertEqual(old["owner"], "Old Ops")
        self.assertEqual(out_of_order["question_id"], 7)
        self.assertEqual(out_of_order["accepted_answer_id"], 72)
        self.assertEqual(out_of_order["answer_owner"], "Early Answer")
        self.assertEqual(out_of_order["category"], "k8s")

    def test_html_and_symptom_lines_rank_and_cap_output(self):
        long_error = "ERROR " + "x" * 200
        body = (
            "<p>Prose service failed during startup.</p><pre><code>"
            f"{long_error}\nConnection refused by peer\nDiagnostic snapshot alpha\n"
            "Diagnostic snapshot beta\nDiagnostic snapshot gamma\nDiagnostic snapshot delta"
            "</code></pre>"
        )

        lines = kb_community.html_lines(body)
        symptoms = kb_community.symptom_lines(body)

        self.assertEqual(lines[0], ("Prose service failed during startup.", False))
        self.assertTrue(all(in_code for _, in_code in lines[1:]))
        self.assertEqual(
            symptoms[:3],
            [long_error[:160], "Connection refused by peer", "Prose service failed during startup."],
        )
        self.assertEqual(len(symptoms), 6)
        self.assertTrue(all(len(line) <= 160 for line in symptoms))
        self.assertNotIn("Diagnostic snapshot delta", symptoms)

    def test_licence_boundaries_and_category_priority(self):
        self.assertEqual(kb_community.licence({"creation_date": 1302220799}), "CC BY-SA 2.5")
        self.assertEqual(kb_community.licence({"creation_date": 1302220800}), "CC BY-SA 3.0")
        self.assertEqual(kb_community.licence({"creation_date": 1525219199}), "CC BY-SA 3.0")
        self.assertEqual(kb_community.licence({"creation_date": 1525219200}), "CC BY-SA 4.0")
        self.assertEqual(kb_community.category(["networking", "kubernetes"]), "k8s")
        self.assertEqual(kb_community.category(["windows-10"]), "other")

    def test_dump_hosts_cover_documented_sites(self):
        documented_sites = {
            "serverfault", "unix", "askubuntu", "devops", "networkengineering",
            "dba", "raspberrypi", "superuser",
        }

        self.assertLessEqual(documented_sites, set(kb_community.DUMP_HOSTS))


if __name__ == "__main__":
    unittest.main()
