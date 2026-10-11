"""Offline integration tests: no real API key, user stores or paid requests."""

import http.client
import json
import threading
from decimal import Decimal
from http.server import ThreadingHTTPServer

import pytest

from corecoder import Config, checkpoints
from corecoder import session as cli_sessions
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse, ToolCall
from corecoder.usage import CostSummary
from web_backend.server import make_handler
from web_backend.service import RequestError, Service


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("CORECODER_DATA_DIR", str(tmp_path / "stores"))
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: Config(api_key="offline-secret", model="scripted")))
    monkeypatch.setattr(checkpoints, "CHECKPOINTS_FILE", tmp_path / "initial-checkpoints.json")
    monkeypatch.setattr(cli_sessions, "SESSIONS_DIR", tmp_path / "cli-sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkpoints.clear()

    def make(script=()):
        return Service(workspace, tmp_path / "state", llm_factory=lambda: ScriptedLLM(list(script)), extensions=False)

    yield make
    checkpoints.clear()


def until(events, event_type):
    received = []
    while True:
        event = events.get(timeout=5)
        received.append(event)
        if event["type"] == event_type:
            return event, received


def write_script(content="hello\n"):
    return [LLMResponse(tool_calls=[ToolCall("w1", "write_file", {"file_path": "note.txt", "content": content})]),
            LLMResponse(content="完成。")]


class SummaryScriptedLLM(ScriptedLLM):
    """Offline provider exposing a mixed known/unknown cost summary."""

    @property
    def cost_summary(self):
        return CostSummary(Decimal("1.2300"), 17, 23, 2, 1)

    @property
    def estimated_cost(self):
        return None


def test_bootstrap_never_exposes_key_and_agents_are_independent(factory):
    service = factory()
    a, b = service.get(service.create()["id"]), service.get(service.create()["id"])
    a.agent._todo.execute([{"content": "A only", "status": "pending"}])
    assert a.agent._tool_by_name["agent"]._parent_agent is a.agent
    assert b.agent._tool_by_name["agent"]._parent_agent is b.agent
    assert b.agent._todo._tasks == []
    assert "offline-secret" not in json.dumps(service.bootstrap())
    assert service.bootstrap()["key_configured"]


def test_approval_stream_write_diff_and_conflict_safe_undo(factory):
    service = factory(write_script())
    sid = service.create()["id"]
    events = service.start(sid, "write a note")
    permission, _ = until(events, "permission")
    assert not (service.workspace / "note.txt").exists()
    service.approve(sid, permission["id"], "once")
    _, received = until(events, "done")
    assert any(e["type"] == "tool_result" for e in received)
    assert any(e["type"] == "token" for e in received)
    assert (service.workspace / "note.txt").read_text() == "hello\n"
    session = service.get(sid)
    assert session.changes()[0]["diff"].startswith("--- a/note.txt")
    assert session.snapshot()["undo_count"] == 1
    agent_after = (service.workspace / "note.txt").read_bytes()
    (service.workspace / "note.txt").write_text("human change")
    with pytest.raises(RequestError, match="发生变化"):
        service.action(sid, {"action": "undo"})
    assert (service.workspace / "note.txt").read_text() == "human change"
    (service.workspace / "note.txt").write_bytes(agent_after)
    service.action(sid, {"action": "undo"})
    assert not (service.workspace / "note.txt").exists()
    assert session.changes() == []


def test_web_undo_rejects_crlf_only_external_change(factory):
    service = factory(write_script())
    sid = service.create()["id"]
    events = service.start(sid, "write a note")
    permission, _ = until(events, "permission")
    service.approve(sid, permission["id"], "once")
    until(events, "done")
    path = service.workspace / "note.txt"
    agent_after = path.read_bytes()
    assert agent_after == b"hello\n"
    path.write_bytes(b"hello\r\n")
    with pytest.raises(RequestError):
        service.action(sid, {"action": "undo"})
    assert path.read_bytes() == b"hello\r\n"
    assert service.get(sid).snapshot()["undo_count"] == 1
    path.write_bytes(agent_after)
    service.action(sid, {"action": "undo"})
    assert not path.exists()


def test_plan_refuses_mutation_without_permission_dialog(factory):
    service = factory(write_script())
    sid = service.create()["id"]
    service.action(sid, {"action": "plan", "enabled": True})
    _, events = until(service.start(sid, "make a plan"), "done")
    assert not any(e["type"] == "permission" for e in events)
    assert not (service.workspace / "note.txt").exists()
    assert "Plan mode is on" in service.get(sid).agent.messages[2]["content"]


def test_denied_write_never_executes(factory):
    service = factory(write_script())
    sid = service.create()["id"]
    events = service.start(sid, "write")
    permission, _ = until(events, "permission")
    service.approve(sid, permission["id"], "deny")
    _, received = until(events, "done")
    assert not (service.workspace / "note.txt").exists()
    assert "Permission denied" in service.get(sid).agent.messages[2]["content"]
    result = next(e for e in received if e["type"] == "tool_result")
    assert result["status"] == "refused"
    assert result["refused"] is True


def test_invalid_arguments_emit_failed_outcome_before_permission_or_side_effect(factory):
    script = [
        LLMResponse(tool_calls=[ToolCall("bad", "write_file", {"file_path": "note.txt"})]),
        LLMResponse(content="参数已修正。"),
    ]
    service = factory(script)
    sid = service.create()["id"]
    _, received = until(service.start(sid, "write"), "done")
    result = next(e for e in received if e["type"] == "tool_result")
    assert result["status"] == "failed"
    assert result["stage"] == "validate"
    assert "invalid_arguments" in result["result"]
    assert not any(e["type"] == "tool_start" for e in received)
    assert not any(e["type"] == "permission" for e in received)
    assert not (service.workspace / "note.txt").exists()


def test_max_rounds_is_published_without_a_false_complete(factory):
    script = [LLMResponse(content="仍在处理", tool_calls=[ToolCall("r1", "read_file", {"file_path": "missing.txt"})])]
    service = factory(script)
    sid = service.create()["id"]
    service.get(sid).agent.max_rounds = 1
    _, received = until(service.start(sid, "inspect"), "done")
    result = next(e for e in received if e["type"] == "run_result")
    assert result["status"] == "max_rounds"
    assert result["finish_reason"] == "max_rounds"
    assert result["rounds"] == 1
    assert not any(e["type"] == "complete" for e in received)
    error = next(e for e in received if e["type"] == "error")
    assert error["status"] == "max_rounds"
    assert error["tool_errors"]
    assert service.get(sid).snapshot()["busy"] is False


def test_web_snapshot_preserves_known_and_unknown_cost_summary(factory):
    service = factory([LLMResponse(content="done")])
    sid = service.create()["id"]
    session = service.get(sid)
    session.agent.llm = SummaryScriptedLLM([LLMResponse(content="done")])
    until(service.start(sid, "hello"), "done")
    summary = service.get(sid).snapshot()["usage"]["cost_summary"]
    assert summary == {
        "known_cost": "1.2300",
        "unknown_prompt_tokens": 17,
        "unknown_completion_tokens": 23,
        "unknown_requests": 2,
        "missing_usage_requests": 1,
    }
    assert service.get(sid).snapshot()["usage"]["cost"] is None


def test_web_snapshot_keeps_repeated_call_id_outcomes_in_order(factory):
    script = [
        LLMResponse(tool_calls=[ToolCall("same", "read_file", {"file_path": "note.txt"})]),
        LLMResponse(content="第一次完成。"),
        LLMResponse(tool_calls=[ToolCall("same", "write_file", {"file_path": "note.txt"})]),
        LLMResponse(content="第二次完成。"),
    ]
    service = factory(script)
    (service.workspace / "note.txt").write_text("保留")
    sid = service.create()["id"]
    until(service.start(sid, "read"), "done")
    until(service.start(sid, "write"), "done")
    outcomes = service.get(sid).snapshot()["tool_outcomes"]
    assert [entry["call_id"] for entry in outcomes] == ["same", "same"]
    assert [entry["status"] for entry in outcomes] == ["succeeded", "failed"]


def test_stop_pending_approval_releases_runner_and_repairs_history(factory):
    service = factory(write_script())
    sid = service.create()["id"]
    events = service.start(sid, "write")
    until(events, "permission")
    with pytest.raises(RequestError) as error:
        service.start(sid, "second run")
    assert error.value.status == 409
    service.stop(sid)
    _, received = until(events, "done")
    assert not service.get(sid).busy
    assert not service.execution.locked()
    assert service.get(sid).agent.messages[-1]["tool_call_id"] == "w1"
    assert not (service.workspace / "note.txt").exists()
    cancelled = next(e for e in received if e["type"] == "tool_result")
    assert cancelled["status"] == "cancelled"


def test_save_restore_and_reset_keep_file_history(factory):
    service = factory([LLMResponse(content="saved reply")])
    sid = service.create()["id"]
    until(service.start(sid, "remember this"), "done")
    service.action(sid, {"action": "save"})
    other = service.create()["id"]
    restored = service.action(other, {"action": "import", "saved_id": f"web_{sid}"})["session"]
    assert restored["messages"][0]["content"] == "remember this"
    assert restored["messages"][1]["content"] == "saved reply"
    again = factory([LLMResponse(content="next")])
    assert again.get(sid).agent.messages[-1]["content"] == "saved reply"
    service.action(other, {"action": "reset"})
    assert service.get(other).agent.messages == []


def test_relative_file_paths_follow_session_cwd_and_protect_secrets(factory):
    service = factory()
    session = service.get(service.create()["id"])
    sub = service.workspace / "sub"
    sub.mkdir()
    (sub / "marker.txt").write_text("SUB")
    (service.workspace / "marker.txt").write_text("ROOT")
    session.state.cwd = sub
    read = session.agent._tool_by_name["read_file"]
    assert "SUB" in read.execute(file_path="marker.txt")
    assert "limited" in read.execute(file_path=str(service.workspace.parent / "outside"))
    assert "credential" in read.execute(file_path=str(service.workspace / ".env"))
    (service.workspace / ".env").write_text("secret_value=do-not-send")
    (sub / ".env.local").write_text("secret_value=also-private")
    grep = session.agent._tool_by_name["grep"]
    assert "No matches" in grep.execute(pattern="secret_value", path=str(service.workspace))
    assert "credential" in read.execute(file_path=str(sub / ".env.local"))


def test_subagent_cancellation_and_tasks_are_isolated(factory):
    script = [LLMResponse(tool_calls=[ToolCall("a1", "agent", {"task": "subtask"})]),
              LLMResponse(tool_calls=[ToolCall("t1", "todo_write", {
                  "tasks": [{"content": "child task", "status": "pending"}]})]),
              *write_script(), LLMResponse(content="parent done")]
    service = factory(script)
    sid = service.create()["id"]
    session = service.get(sid)
    session.agent._todo.execute([{"content": "parent task", "status": "pending"}])
    events = service.start(sid, "delegate")
    permission, _ = until(events, "permission")
    assert permission["name"] == "agent"
    service.approve(sid, permission["id"], "once")
    permission, _ = until(events, "permission")
    assert permission["name"] == "write_file"
    service.stop(sid)
    _, remaining = until(events, "done")
    assert session.agent._todo._tasks[0]["content"] == "parent task"
    assert session.agent.messages[-1]["tool_call_id"] == "a1"
    assert not (service.workspace / "note.txt").exists()
    assert not service.execution.locked()
    assert any(e["type"] == "notice" for e in remaining)


@pytest.fixture
def http_server(factory, tmp_path):
    service = factory([LLMResponse(content="streamed response")])
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<h1>Workbench</h1>")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, frontend))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield service, server.server_port
    server.shutdown()
    server.server_close()


def request(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, json.dumps(body) if body is not None else None, headers or {})
    response = connection.getresponse()
    status, payload = response.status, response.read().decode()
    connection.close()
    return status, payload


def test_http_token_origin_and_static_path_guards(http_server):
    service, port = http_server
    assert request(port, "GET", "/")[0] == 200
    assert "offline-secret" not in request(port, "GET", "/api/bootstrap")[1]
    assert request(port, "POST", "/api/sessions", {}, {"Content-Type": "application/json"})[0] == 403
    valid = {"Content-Type": "application/json", "X-CoreCoder-Token": service.token}
    assert request(port, "POST", "/api/sessions", {}, {**valid, "Origin": "https://foreign.example"})[0] == 403
    assert request(port, "GET", "/%2e%2e/secret")[0] == 403
    assert request(port, "GET", "/api/bootstrap", headers={"Host": "foreign.example"})[0] == 403
    assert request(port, "POST", "/api/sessions", {}, valid)[0] == 201


def test_http_chat_sse_completes_and_exposes_final_snapshot(http_server):
    service, port = http_server
    sid = service.create()["id"]
    headers = {"Content-Type": "application/json", "X-CoreCoder-Token": service.token}
    status, payload = request(port, "POST", "/api/chat", {"id": sid, "prompt": "hello"}, headers)
    assert status == 200
    events = [json.loads(block[6:]) for block in payload.split("\n\n") if block.startswith("data: ")]
    assert events[-1]["type"] == "done"
    assert "streamed response" == next(e for e in events if e["type"] == "token")["text"]
    assert next(e for e in events if e["type"] == "snapshot")["session"]["busy"] is False


def test_http_rejects_invalid_json_shape(http_server):
    service, port = http_server
    status, _ = request(port, "POST", "/api/sessions", [], {
        "Content-Type": "application/json", "X-CoreCoder-Token": service.token})
    assert status == 400
