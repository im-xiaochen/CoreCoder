import json
import tempfile
import threading
import unittest
from pathlib import Path

from corecoder.project_checks import CheckError, ProjectChecks


class ProjectCheckTests(unittest.TestCase):
    def test_authorization_matching_and_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".corecoder").mkdir()
            config = root / ".corecoder" / "checks.json"
            config.write_text(
                json.dumps({"version": 1, "checks": [{"id": "ok", "name": "ok", "command": "echo success", "files": ["*.py"]}]})
            )
            checks = ProjectChecks(root, root / "results")
            token = checks.inspect()["token"]
            self.assertEqual(checks.select([], None)["checks"], [])
            self.assertEqual(checks.select(None, [])["checks"], [])
            with self.assertRaises(CheckError):
                checks.run("ok", token)
            cancelled = threading.Event()
            cancelled.set()
            result = checks.run("ok", token, authorized=True, files=["x.py"], cancel=cancelled)
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(checks.get(result["id"])["status"], "cancelled")
            result["status"] = "running"
            checks._save(result)
            checks.recover()
            self.assertEqual(checks.get(result["id"])["status"], "interrupted")
            config.write_text(config.read_text() + " ")
            with self.assertRaises(CheckError):
                checks.run("ok", token, authorized=True)
            with self.assertRaises(CheckError):
                checks.get("../secret")
