import json
import tempfile
import threading
import unittest
from pathlib import Path

from corecoder.project_checks import CheckError, ProjectChecks


class ProjectCheckTests(unittest.TestCase):
    def test_reject_linked_store(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            target = root / "target"
            target.mkdir()
            linked_store = root / "linked-store"
            try:
                linked_store.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks unavailable")
            with self.assertRaises(CheckError):
                ProjectChecks(root, linked_store)._check_store()

    def test_authorization_matching_and_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
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

    def test_running_output_is_persisted_before_completion(self):
        import sys
        import time

        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / ".corecoder").mkdir()
            command = f'"{sys.executable}" -u -c "import time; print(\'early diagnostic\', flush=True); time.sleep(5)"'
            (root / ".corecoder" / "checks.json").write_text(
                json.dumps({"version": 1, "checks": [{"id": "slow", "name": "slow", "command": command}]})
            )
            checks = ProjectChecks(root, root / "results")
            token = checks.inspect()["token"]
            cancel = threading.Event()
            events = []
            worker = threading.Thread(
                target=lambda: checks.run("slow", token, authorized=True, cancel=cancel, on_progress=events.append)
            )
            worker.start()
            try:
                deadline = time.monotonic() + 15
                while not events and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(events)
                record = checks.get(events[0]["id"])
                self.assertEqual(record["status"], "running")
                self.assertIn("early diagnostic", record["stdout"])
            finally:
                cancel.set()
                worker.join(15)
            self.assertFalse(worker.is_alive())
            self.assertIn("early diagnostic", checks.get(events[0]["id"])["stdout"])
