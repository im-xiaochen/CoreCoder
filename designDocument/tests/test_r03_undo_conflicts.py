"""R03: undo refuses to erase edits made after the agent's write."""

import pytest

from corecoder import checkpoints
from corecoder.tools.write import WriteFileTool


def test_unchanged_postimage_can_be_undone(tmp_path):
    target = tmp_path / "file.txt"
    target.write_bytes(b"original\r\n")
    WriteFileTool().execute(str(target), "agent")
    checkpoints.undo()
    assert target.read_bytes() == b"original\r\n"
    assert checkpoints.pending() == 0


@pytest.mark.parametrize("existed", [True, False])
def test_external_edit_preserves_file_and_checkpoint(tmp_path, existed):
    target = tmp_path / "file.txt"
    if existed:
        target.write_text("original", encoding="utf-8")
    WriteFileTool().execute(str(target), "agent")
    target.write_text("human", encoding="utf-8")
    result = checkpoints.undo()
    assert target.read_text(encoding="utf-8") == "human"
    assert checkpoints.pending() == 1
    assert "conflict" in result.lower()
    assert str(target) in result


def test_external_delete_is_a_conflict(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")
    WriteFileTool().execute(str(target), "agent")
    target.unlink()
    result = checkpoints.undo()
    assert not target.exists()
    assert checkpoints.pending() == 1
    assert "conflict" in result.lower()


def test_force_undo_is_explicit(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")
    WriteFileTool().execute(str(target), "agent")
    target.write_text("human", encoding="utf-8")
    checkpoints.undo(force=True)
    assert target.read_text(encoding="utf-8") == "original"
    assert checkpoints.pending() == 0


def test_conflict_guard_survives_reload(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")
    WriteFileTool().execute(str(target), "agent")
    checkpoints._stack.clear()
    checkpoints._loaded = False
    target.write_text("human", encoding="utf-8")
    checkpoints.undo()
    assert target.read_text(encoding="utf-8") == "human"
    assert checkpoints.pending() == 1
