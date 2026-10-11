"""R04: storage failures retain a retryable undo and leave the REPL usable."""

from pathlib import Path

from corecoder import checkpoints
from corecoder.agent import Agent
from corecoder.demo import ScriptedLLM
from corecoder.tools.write import WriteFileTool
from designDocument.tests.support import drive_repl


def prepare_file(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")
    WriteFileTool().execute(str(target), "agent")
    assert checkpoints.pending() == 1
    return target


def test_restore_write_failure_keeps_entry_and_allows_retry(tmp_path, monkeypatch):
    target = prepare_file(tmp_path)
    with monkeypatch.context() as scoped:
        def fail(*args, **kwargs):
            raise OSError("simulated disk failure")

        scoped.setattr(Path, "write_bytes", fail)
        result = checkpoints.undo()
    assert "error" in result.lower()
    assert target.read_text(encoding="utf-8") == "agent"
    assert checkpoints.pending() == 1
    checkpoints.undo()
    assert target.read_text(encoding="utf-8") == "original"
    assert checkpoints.pending() == 0


def test_created_file_delete_failure_keeps_entry(tmp_path, monkeypatch):
    target = tmp_path / "new.txt"
    WriteFileTool().execute(str(target), "agent")
    original_unlink = Path.unlink

    def deny_target(path, *args, **kwargs):
        if path == target:
            raise PermissionError("simulated locked file")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", deny_target)
        result = checkpoints.undo()
    assert "error" in result.lower()
    assert target.exists()
    assert checkpoints.pending() == 1
    checkpoints.undo()
    assert not target.exists()


def test_checkpoint_commit_failure_compensates_and_remains_retryable(tmp_path, monkeypatch):
    target = prepare_file(tmp_path)
    original_replace = Path.replace

    def deny_commit(path, destination):
        if Path(destination) == checkpoints.CHECKPOINTS_FILE:
            raise OSError("simulated checkpoint commit failure")
        return original_replace(path, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", deny_commit)
        result = checkpoints.undo()
    assert "error" in result.lower()
    assert target.read_text(encoding="utf-8") == "agent"
    assert checkpoints.pending() == 1
    checkpoints._stack.clear()
    checkpoints._loaded = False
    assert checkpoints.pending() == 1
    checkpoints.undo()
    assert target.read_text(encoding="utf-8") == "original"


def test_undo_failure_does_not_end_repl(tmp_path, monkeypatch, capsys):
    prepare_file(tmp_path)

    def fail():
        raise OSError("simulated restore failure")

    monkeypatch.setattr(checkpoints, "undo", fail)
    a = Agent(ScriptedLLM([]))
    consumed = drive_repl(monkeypatch, a, ["/undo", "/help", "quit"])
    assert consumed == ["/undo", "/help", "quit"]
    assert "simulated restore failure" in capsys.readouterr().out
    assert checkpoints.pending() == 1
