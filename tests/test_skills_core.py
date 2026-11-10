"""Offline focused tests of explicit skill loading."""
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from corecoder.skills import list_skills, resolve_skill


def project(tmp_path, name="custom", body="hello"):
    directory = tmp_path / ".corecoder" / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    return directory


def test_list_does_not_read_body_and_conflict(tmp_path):
    project(tmp_path, "review")
    with patch.object(Path, "open", side_effect=AssertionError("read")):
        snapshot = list_skills(tmp_path)
    assert len([x for x in snapshot if x.name == "review" and x.conflict]) == 2
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_skill(snapshot, "review")
    assert resolve_skill(snapshot, "review", "builtin").source == "builtin"


def test_metadata_auxiliary_and_budgets(tmp_path):
    directory = project(tmp_path)
    (directory / "SKILL.md").write_bytes(b"---\r\nversion: 2\r\nfiles: note.txt\r\n---\r\nBody")
    (directory / "note.txt").write_text("aux")
    snapshot = list_skills(tmp_path)
    loaded = resolve_skill(snapshot, "custom")
    assert loaded.version == "2" and loaded.files == ("SKILL.md", "note.txt") and "aux" in loaded.text
    (directory / "SKILL.md").write_text("---\nfiles: " + ",".join(str(i) for i in range(9)) + "\n---\nx")
    with pytest.raises(ValueError):
        resolve_skill(snapshot, "custom")
    (directory / "SKILL.md").write_text("x" * 32769)
    with pytest.raises(ValueError):
        resolve_skill(snapshot, "custom")
    (directory / "SKILL.md").write_text("---\nversion: a\nversion: b\n---\nx")
    with pytest.raises(ValueError):
        resolve_skill(snapshot, "custom")


def test_reject_unsafe_and_binary(tmp_path):
    directory = project(tmp_path, body="---\nfiles: ../secret\n---\nx")
    snapshot = list_skills(tmp_path)
    with pytest.raises(ValueError):
        resolve_skill(snapshot, "custom")
    (directory / "SKILL.md").write_text("---\nfiles: .env.local\n---\nx")
    (directory / ".env.local").write_text("secret")
    with pytest.raises(ValueError):
        resolve_skill(snapshot, "custom")
    (directory / "SKILL.md").write_bytes(b"abc\0def")
    with pytest.raises(ValueError):
        resolve_skill(snapshot, "custom")


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    target = tmp_path_factory.mktemp("wheel")
    root = Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable, "-m", "pip", "wheel", str(root), "--no-deps", "-w", str(target)],
                   check=True, capture_output=True)
    return next(target.glob("*.whl"))


def test_bundled_templates_present_in_wheel(built_wheel):
    with zipfile.ZipFile(built_wheel) as wheel:
        for name in ("review", "test", "pr-summary"):
            assert f"corecoder/builtin_skills/{name}/SKILL.md" in wheel.namelist()


def test_replaced_directory_junction_rejected(tmp_path):
    directory = project(tmp_path)
    snapshot = list_skills(tmp_path)
    original = directory.with_name("saved")
    directory.rename(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("redirected")
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(directory), str(outside)],
                       check=True, capture_output=True)
    else:
        directory.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="redirected"):
            resolve_skill(snapshot, "custom")
    finally:
        if os.name == "nt":
            directory.rmdir()
        else:
            directory.unlink()


def test_wheel_isolated_builtin_loading(tmp_path, built_wheel):
    with zipfile.ZipFile(built_wheel) as archive:
        archive.extractall(tmp_path / "installed")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from corecoder.skills import list_skills, resolve_skill; "
            "s=list_skills(sys.argv[2]); "
            "assert all(resolve_skill(s,n,'builtin').text for n in ('review','test','pr-summary'))")
    subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path / "installed"), str(tmp_path)],
                   check=True, cwd=tmp_path)
