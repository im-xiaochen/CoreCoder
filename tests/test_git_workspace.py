import subprocess
import tempfile
import unittest
from pathlib import Path

from corecoder.git_workspace import GitWorkspace, GitWorkspaceError


class GitWorkspaceTests(unittest.TestCase):
    def test_review_stage_and_commit_index(self):
        with tempfile.TemporaryDirectory() as d:

            def git(*args):
                return subprocess.run(["git", *args], cwd=d, check=True, capture_output=True).stdout

            git("init")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            path = Path(d) / "a.txt"
            path.write_text("first", encoding="utf-8")
            repo = GitWorkspace(d)
            review = repo.status()
            repo.stage(["a.txt"], review["review_token"])
            path.write_text("second", encoding="utf-8")
            review = repo.status()
            self.assertIn("second", repo.diff("a.txt")["text"])
            result = repo.commit(["a.txt"], review["review_token"], "first commit")
            self.assertTrue(result["commit"])
            self.assertEqual(git("show", "HEAD:a.txt"), b"first")
            self.assertEqual(path.read_text(), "second")
            with self.assertRaises(GitWorkspaceError):
                repo.stage(["a.txt"], review["review_token"])

    def test_baseline_and_rename(self):
        with tempfile.TemporaryDirectory() as d:

            def git(*args):
                return subprocess.run(["git", *args], cwd=d, check=True, capture_output=True).stdout

            git("init")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            path = Path(d) / "old"
            path.write_text("content", encoding="utf-8")
            git("add", "old")
            git("commit", "-m", "base")
            repo = GitWorkspace(d)
            baseline = repo.baseline()
            path.unlink()
            state = repo.status(baseline)
            self.assertFalse(state["entries"][0]["preexisting"])
            self.assertTrue(state["entries"][0]["changed_during_run"])
            path.write_text("content", encoding="utf-8")
            git("mv", "old", "new")
            state = repo.status(baseline)
            entry = state["entries"][0]
            self.assertEqual(entry["old_path"], "old")
            with self.assertRaises(GitWorkspaceError):
                repo.commit(["new"], state["review_token"], "rename")
            result = repo.commit(["old", "new"], state["review_token"], "rename")
            self.assertTrue(result["commit"])
