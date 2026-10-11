"""R10: validate JSON shapes and preserve old archives on failed atomic save."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from corecoder import mcp, session
from corecoder.hooks import load_hooks


@pytest.mark.parametrize("body", [[], None, 42, "text", {"PreToolUse": [42]}, {"PreToolUse": "wrong"},
                                      {"PostToolUse": [{"command": 42}]}, {"PreToolUse": [{"command": "x", "matcher": []}]}])
def test_bad_hooks_structure_is_ignored_with_file_diagnostic(tmp_path, caplog, body):
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        hooks = load_hooks(path)
    assert not hooks
    assert str(path) in caplog.text


def test_mixed_hooks_keep_valid_entry_and_name_bad_field(tmp_path, caplog):
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"PreToolUse": [42, {"command": "echo ok", "matcher": "bash"}]}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        hooks = load_hooks(path)
    assert hooks.pre == [{"command": "echo ok", "matcher": "bash"}]
    assert "PreToolUse[0]" in caplog.text


@pytest.mark.parametrize("body", [[], None, 42, {"mcpServers": [42]}, {"mcpServers": "wrong"}])
def test_bad_mcp_root_never_starts_process(tmp_path, monkeypatch, caplog, body):
    called = []
    monkeypatch.setattr(mcp, "MCPClient", lambda *args, **kwargs: called.append(args))
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert mcp.load_mcp_tools(path) == []
    assert called == []
    assert str(path) in caplog.text


def test_bad_mcp_entries_do_not_discard_good_server(tmp_path, monkeypatch, caplog):
    started = []

    def fake_client(name, command, args=(), env=None):
        started.append(name)
        return SimpleNamespace(tools=[SimpleNamespace(name=f"mcp__{name}__echo")], close=lambda: None)

    monkeypatch.setattr(mcp, "MCPClient", fake_client)
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {
        "scalar": 42, "bad-command": {"command": []},
        "bad-args": {"command": "x", "args": [42]},
        "bad-env": {"command": "x", "env": {"A": 42}},
        "good": {"command": "offline", "args": []},
    }}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        tools = mcp.load_mcp_tools(path)
    assert started == ["good"]
    assert [tool.name for tool in tools] == ["mcp__good__echo"]
    assert "bad-args" in caplog.text and str(path) in caplog.text


@pytest.mark.parametrize("body", [[], None, 42, {}, {"model": 42, "messages": []},
                                      {"model": "m", "messages": [42]},
                                      {"model": "m", "messages": [{"role": "invalid", "content": "x"}]},
                                      {"model": "m", "messages": [{"role": "user", "content": 42}]},
                                      {"model": "m", "messages": [{"role": "tool", "tool_call_id": "orphan", "content": "x"}]},
                                      {"model": "m", "messages": "wrong"},
                                      {"schema_version": 999, "model": "m", "messages": []}])
def test_invalid_sessions_are_skipped_by_load_and_list(body):
    session.SESSIONS_DIR.mkdir(parents=True)
    (session.SESSIONS_DIR / "bad.json").write_text(json.dumps(body), encoding="utf-8")
    assert session.load_session("bad") is None
    assert session.list_sessions() == []


def test_truncated_session_and_legacy_archive():
    session.SESSIONS_DIR.mkdir(parents=True)
    (session.SESSIONS_DIR / "bad.json").write_text('{"messages":', encoding="utf-8")
    messages = [{"role": "user", "content": "hello"}]
    (session.SESSIONS_DIR / "legacy.json").write_text(json.dumps({"model": "m", "messages": messages}), encoding="utf-8")
    assert session.load_session("bad") is None
    assert session.load_session("legacy") == (messages, "m")
    assert [item["id"] for item in session.list_sessions()] == ["legacy"]


def test_atomic_save_failure_preserves_previous_archive(monkeypatch):
    messages = [{"role": "user", "content": "original"}]
    session.save_session(messages, "offline", "saved")
    target = session.SESSIONS_DIR / "saved.json"
    old_bytes = target.read_bytes()
    original_replace = Path.replace

    def fail_commit(path, destination):
        if Path(destination) == target:
            raise OSError("simulated save failure")
        return original_replace(path, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail_commit)
        with pytest.raises(OSError, match="simulated save failure"):
            session.save_session([{"role": "user", "content": "new"}], "offline", "saved")
    assert target.read_bytes() == old_bytes
    assert session.load_session("saved") == (messages, "offline")


def test_new_archive_has_version_and_no_credentials():
    sid = session.save_session([{"role": "user", "content": "hello"}], "offline", "versioned")
    data = json.loads((session.SESSIONS_DIR / f"{sid}.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert not ({"api_key", "access_token", "authorization"} & data.keys())
