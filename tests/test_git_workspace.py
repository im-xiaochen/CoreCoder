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

    def test_commit_uses_global_and_explicit_identity(self):
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            config = root / "global.gitconfig"
            config.write_text("[user]\n name = Global Person\n email = global@example.invalid\n", encoding="utf-8")
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            (root / "a").write_text("content")
            with patch.dict(
                os.environ,
                {
                    "GIT_CONFIG_GLOBAL": str(config),
                    "GIT_AUTHOR_NAME": "Author Override",
                    "GIT_AUTHOR_EMAIL": "author@example.invalid",
                },
                clear=False,
            ):
                repo = GitWorkspace(root)
                state = repo.status()
                state = repo.stage(["a"], state["review_token"])
                repo.commit(["a"], state["review_token"], "identity")
                output = subprocess.run(
                    ["git", "show", "-s", "--format=%an <%ae>|%cn <%ce>"], cwd=root, check=True, capture_output=True, text=True
                ).stdout.strip()
            self.assertEqual(output, "Author Override <author@example.invalid>|Global Person <global@example.invalid>")
