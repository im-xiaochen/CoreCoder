"""Focused Web skill listing checks."""
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from web_backend.service import RequestError, Service


def test_skills_listing_without_api_key(tmp_path):
    service = object.__new__(Service)
    service.workspace = tmp_path
    session = SimpleNamespace(mode="plan", archived=False)
    service.execution = threading.Lock()
    service.lock = threading.Lock()
    service.sessions = {"session": session}
    service.get = lambda sid: session
    listing = service.skills("session")
    assert {entry["name"] for entry in listing["skills"]} >= {"review", "test", "pr-summary"}


def test_chat_rejects_before_skill_io(tmp_path):
    service = object.__new__(Service)
    service.workspace = tmp_path
    session = SimpleNamespace(mode="chat", archived=False)
    service.execution = threading.Lock()
    service.lock = threading.Lock()
    service.sessions = {"session": session}
    service.get = lambda sid: session
    with patch("corecoder.skills.list_skills", side_effect=AssertionError("unexpected IO")), pytest.raises(RequestError):
        service.skills("session")


def test_reserved_skill_lists_explicit_command(tmp_path):
    from corecoder.skill_commands import skill_command
    directory = tmp_path / ".corecoder" / "skills" / "reset"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("safe text")
    kind, listing = skill_command(tmp_path, "/skills")
    assert kind == "list" and "/reset@.corecoder/skills/reset" in listing and "conflict" in listing
    kind, prompt = skill_command(tmp_path, "/reset@.corecoder/skills/reset task")
    assert kind == "run" and "safe text" in prompt
