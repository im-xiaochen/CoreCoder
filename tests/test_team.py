import json
import signal
import threading
import time
from types import SimpleNamespace

import pytest

from corecoder.budget import BudgetExceeded
from corecoder.state import SessionState
from corecoder.team import run_team, validate_tasks
from corecoder.tools.agent import AgentTool
from corecoder.tools.read import ReadFileTool


def test_team_validation_and_concurrency():
    for tasks in ([], [{"name": "a", "task": "x"}] * 2, [{"name": str(i), "task": "x"} for i in range(9)]):
        with pytest.raises(ValueError):
            validate_tasks(tasks)
    gate = threading.Barrier(4)
    tasks = [{"name": str(i), "task": "x"} for i in range(4)]
    results = run_team(tasks, lambda item: (gate.wait(), {"name": item["name"], "status": "completed"})[1])
    assert [result["name"] for result in results] == ["0", "1", "2", "3"]


def test_team_uses_trusted_tools_and_persists_full_text(tmp_path, monkeypatch):
    state = SessionState(workspace_root=tmp_path)
    tool = AgentTool()
    class Impostor(ReadFileTool):
        pass
    parent = SimpleNamespace(state=state, tools=[ReadFileTool(state=state), Impostor(state=state)],
                             llm=None, context=SimpleNamespace(max_tokens=1000), permission=None)
    tool.bind_parent(parent)
    full = "proof" * 2000
    def make_child(child_state, tools):
        assert len(tools) == 1 and type(tools[0]) is ReadFileTool
        assert child_state is not state
        return SimpleNamespace(chat_result=lambda task: SimpleNamespace(
            status="completed", finish_reason="completed", error=None, text=full, usage={}))
    monkeypatch.setattr(tool, "_make_child", make_child)
    result = json.loads(tool.execute(tasks=[{"name": "../unsafe", "task": "inspect"}]))["tasks"][0]
    assert len(result["summary"]) == 4500
    assert (tmp_path / ".corecoder-artifacts" / state.session_id / "team" / (result["id"] + ".txt")).read_text() == full


def test_budget_stop_publishes_every_terminal_state():
    events = []
    tasks = [{"name": str(i), "task": "x"} for i in range(8)]
    def member(item):
        if item["name"] == "0":
            raise BudgetExceeded("limit")
        time.sleep(0.02)
        return {"name": item["name"], "status": "completed", "reason": "completed"}
    with pytest.raises(BudgetExceeded) as caught:
        run_team(tasks, member, events.append)
    results = caught.value.team_results
    assert len(results) == 8 and all(result["id"] for result in results)
    assert all(result["status"] in {"completed", "cancelled", "failed"} for result in results)
    assert len([event for event in events if event["status"] in {"completed", "cancelled", "failed"}]) == 8


def test_dependency_failure_skips_only_dependent():
    tasks = [{"name": "a", "task": "x"}, {"name": "b", "task": "x", "depends_on": ["a"]},
             {"name": "c", "task": "x"}]
    def member(item):
        return {"name": item["name"], "status": "failed" if item["name"] == "a" else "completed"}
    results = run_team(tasks, member)
    assert [result["status"] for result in results] == ["failed", "skipped", "completed"]
    assert all(result["id"] for result in results)


def test_sigint_drains_started_member_and_restores_handler():
    started = threading.Event()
    release = threading.Event()
    events = []
    previous = signal.getsignal(signal.SIGINT)
    def member(item):
        started.set()
        release.wait(2)
        return {"name": item["name"], "status": "completed", "reason": "completed",
                "summary": "finished", "evidence": ["proof"], "usage": {}}
    def interrupt():
        assert started.wait(2)
        signal.raise_signal(signal.SIGINT)
        time.sleep(0.2)
        release.set()
    sender = threading.Thread(target=interrupt)
    sender.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            run_team([{"name": "a", "task": "x"}, {"name": "b", "task": "y", "depends_on": ["a"]}],
                     member, events.append)
    finally:
        release.set()
        sender.join()
    terminal = {event["name"]: event for event in events if event["status"] in {"completed", "cancelled"}}
    assert terminal["a"]["status"] == "completed" and terminal["a"]["evidence"] == ["proof"]
    assert terminal["b"]["status"] == "cancelled"
    assert signal.getsignal(signal.SIGINT) is previous
