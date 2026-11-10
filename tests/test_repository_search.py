"""Focused offline checks for workspace search."""
from corecoder.repository_search import inventory
from corecoder.state import SessionState
from corecoder.tools.glob_tool import GlobTool
from corecoder.tools.grep import GrepTool
from corecoder.tools.repo_map import RepoMapTool


def fixture(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.py").write_text("from pkg.sub import thing\ndef alpha(): pass\n")
    (tmp_path / "sub" / "b.py").write_text("def beta(): pass\n")
    (tmp_path / ".gitignore").write_text("secret.py\n")
    (tmp_path / "secret.py").write_text("secret\n")
    return SessionState(workspace_root=tmp_path)


def test_glob_anchoring_and_pages(tmp_path):
    state = fixture(tmp_path)
    tool = GlobTool(state)
    assert "a.py" in tool.execute("*.py")
    assert "b.py" not in tool.execute("*.py")
    both = tool.execute("**/*.py")
    assert "a.py" in both and "b.py" in both and "secret.py" not in both
    assert "next_offset=1" in tool.execute("**/*.py", limit=1)
    assert "b.py" in tool.execute("**/*.py", offset=1, limit=1)
    assert "a.py" in tool.execute("*.py", path="a.py")


def test_map_module_and_budgets(tmp_path):
    state = fixture(tmp_path)
    (tmp_path / "large.py").write_bytes(b"x" * 1_000_001)
    (tmp_path / "lines.py").write_text("x=1\n" * 10001)
    result = RepoMapTool(state).execute()
    assert "pkg.sub" in result
    assert "oversized" in result and "line limit" in result
    assert len(result.encode()) < 110_000


def test_inventory_ignore_and_traversal(tmp_path):
    fixture(tmp_path)
    paths, _, _ = inventory(tmp_path, tmp_path)
    assert not any(p.name == "secret.py" for p in paths)
    paths, _, _ = inventory(tmp_path, tmp_path / "secret.py")
    assert not paths
    try:
        inventory(tmp_path, tmp_path / "sub" / "..")
    except ValueError:
        pass
    else:
        raise AssertionError("parent traversal accepted")


def test_grep_timeout(tmp_path):
    state = fixture(tmp_path)
    (tmp_path / "slow.txt").write_text("a" * 1000 + "!\n")
    assert "timed out" in GrepTool(state).execute("(a+)+$", path="slow.txt")


def test_worker_does_not_execute_workspace_code(tmp_path, monkeypatch):
    state = fixture(tmp_path)
    marker = tmp_path / "executed"
    (tmp_path / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('bad')")
    package = tmp_path / "corecoder"
    package.mkdir()
    (package / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('bad')")
    monkeypatch.chdir(tmp_path)
    result = GrepTool(state).execute("alpha", path="a.py")
    assert "alpha" in result and not marker.exists()


def test_no_rg_non_git_walk_and_ignore(tmp_path, monkeypatch):
    import shutil

    from corecoder import repository_search
    (tmp_path / "file.txt").write_text("ok")
    real_which = shutil.which
    monkeypatch.setattr(repository_search.shutil, "which", lambda name: None if name == "rg" else real_which(name))
    paths, backend, _ = inventory(tmp_path, tmp_path)
    assert backend == "walk" and any(p.name == "file.txt" for p in paths)
    (tmp_path / ".gitignore").write_text("file.txt\n")
    import pytest
    with pytest.raises(ValueError, match="ignore"):
        inventory(tmp_path, tmp_path)
