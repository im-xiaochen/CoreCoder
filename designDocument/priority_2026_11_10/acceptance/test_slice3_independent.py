"""Exercise the shared scheduler through Agent and the actual loopback Web API."""
import _thread
import json
import os
import queue
import subprocess
import sys
import threading
from collections import Counter
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from time import monotonic, sleep
from urllib.request import Request, urlopen

import pytest

import corecoder
from corecoder.budget import BudgetLimits
from sdk_fixture import httpx, llm_with_transport, stream
from test_slice2_independent import (
    make_agent, make_team, recorded_team, task_message, tool_delta, tool_names,
    tool_result, user_text, wire_body,
)
from web_backend.server import make_handler
from web_backend.service import Service


def graph():
    return [
        {"name": "finish", "task": "WORK_finish_END", "depends_on": ["left", "right"]},
        {"name": "left", "task": "WORK_left_END", "depends_on": ["start"]},
        {"name": "right", "task": "WORK_right_END", "depends_on": ["start"]},
        {"name": "start", "task": "WORK_start_END"},
        {"name": "isolated", "task": "WORK_isolated_END"},
    ]


@pytest.mark.parametrize("tasks", [
    [{"name": "a", "task": "first"}, {"name": "a", "task": "duplicate"}],
    [{"name": "a", "task": "unknown", "depends_on": ["missing"]}],
    [{"name": "a", "task": "self", "depends_on": ["a"]}],
    [{"name": "a", "task": "duplicate edge", "depends_on": ["b", "b"]}, {"name": "b", "task": "b"}],
    [{"name": "a", "task": "cycle", "depends_on": ["b"]},
     {"name": "b", "task": "cycle", "depends_on": ["c"]},
     {"name": "c", "task": "cycle", "depends_on": ["a"]},
     {"name": "unrelated", "task": "would otherwise start"}],
])
def test_invalid_graph_starts_no_member_and_creates_no_artifact(tmp_path, monkeypatch, tasks):
    def respond(request, number):
        assert user_text(wire_body(request)) == "TEAM_PARENT"
        if number == 1:
            return stream(tool_delta("agent", {"tasks": tasks}, call_id="team-parent"))
        return stream({"content": "validation checked"})

    model, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, model)
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=2))
        assert result.status == "completed"
        assert result.tool_errors and result.tool_errors[0]["name"] == "agent"
        assert len(calls) == len(model.usage_ledger) == 2
        assert not list(agent.state.artifact_dir.glob("**/*"))
    finally:
        sdk.close()


@pytest.mark.parametrize("failed", [False, True], ids=["diamond", "failed-branch"])
def test_diamond_passes_only_direct_dependencies_and_isolates_failed_branch(tmp_path, monkeypatch, failed):
    tasks = graph()
    names = tuple(task["name"] for task in tasks)
    declared = {task["name"]: task.get("depends_on", []) for task in tasks}
    sent = []
    lock = threading.Lock()
    proofs = {name: f"PROOF_{name}_TAIL" for name in names}
    bounded_root = proofs["start"] + (" evidence\n" * 1500) + "NOT_IN_CHILD_CONTEXT"

    def respond(request, number):
        wire = wire_body(request)
        if user_text(wire) == "TEAM_PARENT":
            if number == 1:
                return stream(tool_delta("agent", {"tasks": tasks}, call_id="team-parent"))
            return stream({"content": "parent final"})
        name = task_message(wire, names)
        payload = json.dumps(wire, ensure_ascii=False)
        with lock:
            assert set(declared[name]).issubset(sent)
            sent.append(name)
        assert tool_names(wire) == {"read_file", "glob", "grep", "repo_map", "todo_write"}
        for prior, evidence in proofs.items():
            assert (evidence in payload) is (prior in declared[name])
        assert "NOT_IN_CHILD_CONTEXT" not in payload
        if failed and name == "left":
            return httpx.Response(401, json={"error": {"message": "left provider failure"}})
        return stream({"content": bounded_root if name == "start" else proofs[name]})

    model, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, model)
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=7))
        assert result.status == "completed"
        records = recorded_team(agent)["tasks"]
        assert [task["name"] for task in records] == list(names)
        members = {task["name"]: task for task in records}
        if failed:
            assert members["left"]["status"] == "failed"
            assert members["finish"]["status"] == "skipped"
            assert "left" in json.dumps(members["finish"], ensure_ascii=False)
            assert Counter(sent) == Counter(name for name in names if name != "finish")
            assert model.cost_summary.missing_usage_requests == 1
        else:
            assert Counter(sent) == Counter(names)
            assert all(task["status"] == "completed" for task in records)
        assert members["isolated"]["status"] == members["right"]["status"] == "completed"
        assert len(calls) == len(model.usage_ledger) == len(result.budget["attempts"]) == len(sent) + 2
        for member in records:
            if member["status"] == "completed":
                assert member["evidence"], (member["name"], member["artifact_error"])
        for body in calls:
            if user_text(body) != "TEAM_PARENT":
                name = task_message(body, names)
                prompt = user_text(body)
                for upstream in declared[name]:
                    assert json.dumps(members[upstream]["evidence"][0], ensure_ascii=False) in prompt
    finally:
        sdk.close()


class WebClient:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}/api/"
        self.token = self.json("bootstrap")["token"]

    def json(self, path, data=None):
        headers = {"Content-Type": "application/json", "X-CoreCoder-Token": self.token} if data is not None else {}
        request = Request(self.base + path, None if data is None else json.dumps(data).encode(), headers)
        with urlopen(request, timeout=12) as response:
            return json.load(response)

    @contextmanager
    def events(self, rid, after=0):
        with urlopen(self.base + f"run/events?id={rid}&after={after}", timeout=15) as response:
            assert response.headers.get_content_type() == "text/event-stream"
            yield (json.loads(line[6:]) for line in response if line.startswith(b"data: "))

    def start(self, sid, prompt="TEAM_PARENT", **options):
        return self.json("runs", {"id": sid, "prompt": prompt, **options})["id"]

    def approve(self, sid, event):
        self.json("approve", {"id": sid, "permission_id": event["id"], "answer": "once"})


@contextmanager
def serve(service, workspace):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, workspace))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield WebClient(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def consume(client, sid, rid, *, after=0, until=None):
    collected = []
    with client.events(rid, after) as events:
        for event in events:
            collected.append(event)
            if event["type"] == "permission":
                client.approve(sid, event)
            if until is not None and until(event):
                break
    return collected


def test_web_browser_team_keeps_scoped_tools_and_reconnects_without_resending(tmp_path, monkeypatch):
    names = ("source", "dependent")
    tasks = make_team(names)
    tasks[1]["depends_on"] = ["source"]
    checked = []

    def respond(request, number):
        wire = wire_body(request)
        if user_text(wire) == "TEAM_PARENT":
            if number == 1:
                return stream(tool_delta("agent", {"tasks": tasks}, call_id="team-parent"))
            return stream({"content": "parent final"})
        assert tool_names(wire) == {"read_file", "glob", "grep", "repo_map", "todo_write"}
        name = task_message(wire, names)
        if name == "dependent":
            assert "SOURCE_OK" in json.dumps(wire)
            return stream({"content": "DEPENDENT_OK"})
        if tool_result(wire, "read-public") is None:
            return stream(
                tool_delta("read_file", {"file_path": "visible.txt"}, call_id="read-public", index=0),
                tool_delta("read_file", {"file_path": ".env"}, call_id="read-credential", index=1),
                tool_delta("read_file", {"file_path": "../outside.txt"}, call_id="read-outside", index=2),
            )
        assert "PUBLIC_FILE" in tool_result(wire, "read-public")
        assert "credential" in tool_result(wire, "read-credential").lower()
        assert "workspace" in tool_result(wire, "read-outside").lower()
        assert "DO_NOT_EXPOSE" not in json.dumps(wire)
        checked.append(name)
        return stream({"content": "SOURCE_OK"})

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("DO_NOT_EXPOSE", encoding="utf-8")
    (workspace / "visible.txt").write_text("PUBLIC_FILE", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("DO_NOT_EXPOSE", encoding="utf-8")
    model, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        store = tmp_path / "store"
        service = Service(workspace, store=store, llm_factory=lambda: model, extensions=False)
        with serve(service, workspace) as client:
            sid = client.json("sessions", {})["id"]
            rid = client.start(sid, budget={"requests": 5})
            prefix = consume(client, sid, rid, until=lambda e: e["type"] == "permission")
            assert prefix[-1]["type"] == "permission"
            suffix = consume(client, sid, rid, after=prefix[-1]["seq"])
            all_events = prefix + suffix
            assert [event["seq"] for event in all_events] == list(range(1, all_events[-1]["seq"] + 1))
            assert all_events[-1]["type"] == "done" and all_events[-1]["status"] == "completed"
            assert checked == ["source"]
            assert len(calls) == len(model.usage_ledger) == 5
            journal = (store / "runs" / f"{rid}.jsonl").read_text(encoding="utf-8")
            assert [json.loads(line) for line in journal.splitlines()] == all_events
            task_events = [event for event in all_events if event["type"] == "team_task"]
            assert task_events and all(event["run_id"] == rid for event in task_events)
            final = {event["name"]: event for event in task_events if event["status"] == "completed"}
            assert set(final) == set(names)
            assert final["dependent"]["depends_on"] == ["source"]
            assert final["source"]["task_id"] != final["dependent"]["task_id"]
            assert final["source"]["team_id"] == final["dependent"]["team_id"]
            assert final["source"]["seq"] < final["dependent"]["seq"] < all_events[-1]["seq"]
            assert consume(client, sid, rid, after=all_events[-1]["seq"]) == []
            assert len(calls) == 5
    finally:
        sdk.close()


@pytest.mark.parametrize(("stop", "retry"), [
    pytest.param(False, False, id="budget-exhausted"),
    pytest.param(True, False, id="user-stop"),
    pytest.param(True, True, id="user-stop-during-retryable-send"),
])
def test_web_team_stops_at_next_boundary_and_never_sends_waiting_members(tmp_path, monkeypatch, stop, retry):
    names = ("first", "second", "third", "fourth", "waiting", "blocked")
    tasks = make_team(names)
    for task in tasks[4:]:
        task["depends_on"] = list(names[:4])
    lock = threading.Lock()
    four_sent, release = threading.Event(), threading.Event()
    sent = []

    def respond(request, number):
        wire = wire_body(request)
        if number == 1:
            assert user_text(wire) == "TEAM_PARENT"
            return stream(tool_delta("agent", {"tasks": tasks}, call_id="team-parent"))
        name = task_message(wire, names)
        with lock:
            sent.append(name)
            if len(sent) == 4:
                four_sent.set()
        assert release.wait(12)
        if retry:
            return httpx.Response(503, json={"error": {"message": "retryable offline provider failure"}})
        return stream(tool_delta("todo_write", {"tasks": []}, call_id="next-round"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        service = Service(workspace, store=tmp_path / "store", llm_factory=lambda: model, extensions=False)
        with serve(service, workspace) as client:
            sid = client.json("sessions", {})["id"]
            rid = client.start(sid, budget={"requests": 10 if stop else 5})
            prefix = consume(client, sid, rid, until=lambda e: e["type"] == "permission")
            try:
                assert four_sent.wait(10)
                if stop:
                    assert client.json("stop", {"id": sid}) == {"ok": True}
            finally:
                release.set()
            suffix = consume(client, sid, rid, after=prefix[-1]["seq"])
            terminal = suffix[-1]
            assert terminal["type"] == "done"
            assert terminal["status"] == ("cancelled" if stop else "failed")
            result = next(event for event in suffix if event["type"] == "run_result")
            assert result["finish_reason"] == ("cancelled" if stop else "budget_exceeded")
            assert Counter(sent) == Counter(names[:4])
            assert len(calls) == len(model.usage_ledger) == len(result["budget"]["attempts"]) == 5
            assert len({entry.attempt_id for entry in model.usage_ledger}) == 5
            if retry:
                assert model.cost_summary.missing_usage_requests == 4
            final_states = {}
            for event in suffix:
                if event["type"] == "team_task":
                    final_states[event["name"]] = event
            assert set(final_states) == set(names)
            for name in names[4:]:
                assert final_states[name]["status"] in {"cancelled", "skipped"}
                assert final_states[name]["reason"]
            assert all(event["seq"] < terminal["seq"] for event in final_states.values())
    finally:
        release.set()
        sdk.close()


def wait_for_status(client, rid, statuses):
    deadline = monotonic() + 12
    while monotonic() < deadline:
        run = client.json(f"run?id={rid}")
        if run["status"] in statuses:
            return run
        sleep(0.02)
    pytest.fail(f"Run did not reach {statuses}: {run}")


def test_web_reset_uses_authoritative_team_state_after_journal_compaction(tmp_path, monkeypatch):
    from web_backend import run_store

    monkeypatch.setattr(run_store, "MAX_EVENTS", 8)
    names = ("first", "last")
    tasks = make_team(names)
    tasks[1]["depends_on"] = ["first"]

    def respond(request, number):
        if number == 1:
            return stream(tool_delta("agent", {"tasks": tasks}, call_id="team-parent"))
        wire = wire_body(request)
        if user_text(wire) == "TEAM_PARENT":
            return stream({"content": "final"})
        return stream({"content": f"proof {task_message(wire, names)}"})

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        service = Service(workspace, store=tmp_path / "store", llm_factory=lambda: model, extensions=False)
        with serve(service, workspace) as client:
            sid = client.json("sessions", {})["id"]
            rid = client.start(sid, budget={"requests": 4})
            permission = consume(client, sid, rid, until=lambda event: event["type"] == "permission")
            assert permission[-1]["type"] == "permission"
            wait_for_status(client, rid, {"completed"})
            snapshot = consume(client, sid, rid, until=lambda event: event["type"] == "reset")
            assert len(snapshot) == 1 and snapshot[0]["type"] == "reset"
            reset = snapshot[0]
            assert reset["status"] == "completed" and reset["run_id"] == rid
            states = reset.get("team_tasks", reset["session"].get("team_tasks"))
            assert states, reset
            assert {task["name"]: task["status"] for task in states.values()} == {
                "first": "completed", "last": "completed",
            }
            assert len(calls) == 4
    finally:
        sdk.close()


@contextmanager
def server_process(workspace, store, sends):
    script = Path(__file__).with_name("web_restart_fixture.py")
    repo = Path(corecoder.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo), "PYTHONUTF8": "1"}
    proc = subprocess.Popen(
        [sys.executable, str(script), str(workspace), str(store), str(sends)],
        cwd=workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    line = queue.Queue()
    reader = threading.Thread(target=lambda: line.put(proc.stdout.readline()), daemon=True)
    reader.start()
    try:
        port = line.get(timeout=15)
        assert port.strip().isdigit(), f"Fixture failed to start: {port}"
        yield WebClient(int(port)), proc
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)
        proc.stdout.close()
        reader.join(timeout=3)


def test_real_process_restart_interrupts_active_team_without_replaying(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = tmp_path / "store"
    sends = tmp_path / "physical-sends.jsonl"
    with server_process(workspace, store, sends) as (client, proc):
        old_sid = client.json("sessions", {})["id"]
        old_rid = client.start(old_sid, "plain run", budget={"requests": 1})
        assert consume(client, old_sid, old_rid)[-1]["status"] == "completed"
        sid = client.json("sessions", {})["id"]
        rid = client.start(sid, "restart run", budget={"requests": 6})
        prefix = consume(client, sid, rid, until=lambda e: e["type"] == "permission")
        assert prefix[-1]["type"] == "permission"
        deadline = monotonic() + 10
        while monotonic() < deadline:
            if "blocked member" in sends.read_text(encoding="utf-8"):
                break
            sleep(0.02)
        else:
            pytest.fail("Second member never physically sent before restart")
        assert client.json(f"run?id={rid}")["status"] == "running"
        proc.terminate()
        proc.wait(timeout=10)

    before = sends.read_bytes()
    physical = [json.loads(line)["prompt"] for line in before.splitlines()]
    assert len(physical) == 4
    assert "unreachable member" not in "\n".join(physical)
    with server_process(workspace, store, sends) as (client, _):
        old = client.json(f"run?id={old_rid}")
        assert old["status"] == "completed"
        run = client.json(f"run?id={rid}")
        assert run["status"] == "interrupted"
        events = consume(client, sid, rid, after=prefix[-1]["seq"])
        states = {}
        for event in events:
            if event["type"] == "team_task":
                states[event["name"]] = event["status"]
        assert states == {
            "completed-first": "completed",
            "interrupted-next": "interrupted",
            "never-started": "interrupted",
        }
        assert events[-1]["type"] == "done" and events[-1]["status"] == "interrupted"
        assert consume(client, sid, rid, after=events[-1]["seq"]) == []
        assert len(run["budget"]["attempts"]) == 3
        assert sends.read_bytes() == before
        assert client.json(f"session?id={sid}")["busy"] is False


def test_cli_root_interrupt_stops_running_members_before_sdk_retry(tmp_path, monkeypatch):
    from corecoder.team import TeamStop

    names = ("one", "two", "three", "four", "queued")
    four_sent, stopped, release = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    seen = []
    observed_stop = []
    original_set = TeamStop.set

    def mark_stop(self, reason):
        original_set(self, reason)
        if reason == "cancelled":
            stopped.set()

    monkeypatch.setattr(TeamStop, "set", mark_stop)

    def respond(request, number):
        wire = wire_body(request)
        if number == 1:
            return stream(tool_delta("agent", {"tasks": make_team(names)}, call_id="team-parent"))
        with lock:
            seen.append(task_message(wire, names))
            if len(seen) == 4:
                four_sent.set()
        assert release.wait(10)
        return httpx.Response(503, json={"error": {"message": "must not retry after root interrupt"}})

    def interrupt_when_active():
        try:
            if four_sent.wait(6):
                _thread.interrupt_main()
                observed_stop.append(stopped.wait(6))
        finally:
            release.set()

    model, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, model)
        controller = threading.Thread(target=interrupt_when_active)
        controller.start()
        try:
            result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=10))
        finally:
            release.set()
            controller.join(timeout=13)
        assert observed_stop == [True]
        assert result.status == "cancelled" and result.finish_reason == "cancelled"
        assert Counter(seen) == Counter(names[:4])
        assert len(calls) == len(model.usage_ledger) == len(result.budget["attempts"]) == 5
        assert model.cost_summary.missing_usage_requests == 4
    finally:
        sdk.close()
