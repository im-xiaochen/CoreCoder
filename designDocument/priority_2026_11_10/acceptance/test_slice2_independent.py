"""Only SDK transport is replaced. Exercise agent tool calls from wire responses."""
import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar

import pytest

from corecoder.agent import Agent
from corecoder.budget import BudgetLimits
from corecoder.permissions import Permission
from corecoder.state import SessionState
from corecoder.tools import create_tools
from corecoder.tools.base import Tool
from sdk_fixture import delta, httpx, llm_with_transport, stream


def tool_delta(name, args, *, call_id="tool", index=0):
    return {"tool_calls": [{
        "id": call_id, "index": index, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }]}


def wire_body(request):
    return json.loads(request.content)


def user_text(wire):
    return next(message["content"] for message in wire["messages"] if message["role"] == "user")


def task_message(wire, names):
    text = user_text(wire)
    return next(name for name in names if f"WORK_{name}_END" in text)


def tool_names(wire):
    return {item["function"]["name"] for item in wire.get("tools", [])}


def make_team(names):
    return [{"name": name, "task": f"WORK_{name}_END"} for name in names]


def tool_result(wire, call_id):
    return next((m["content"] for m in wire["messages"]
                 if m["role"] == "tool" and m.get("tool_call_id") == call_id), None)


def recorded_team(agent):
    return json.loads(next(m["content"] for m in agent.messages
                           if m["role"] == "tool" and m.get("tool_call_id") == "team-parent"))


def make_agent(tmp_path, model, *, permission=None, hooks=None, alter_tools=None):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    cwd = project / "nested"
    cwd.mkdir(exist_ok=True)
    state = SessionState(workspace_root=project, cwd=cwd, artifact_dir=tmp_path / "records")
    tools = create_tools(state)
    if alter_tools is not None:
        tools = alter_tools(tools)
    return Agent(model, tools=tools, state=state, hooks=hooks,
                 permission=permission or Permission(ask=lambda *_: "once"))


@pytest.mark.parametrize("args", [
    {"tasks": []},
    {"tasks": make_team(tuple(str(n) for n in range(9)))},
    {"task": "legacy", "tasks": make_team(("one",))},
    {"tasks": [{"name": "one", "task": "   "}]},
])
def test_team_rejects_bad_shape_before_any_child_request(tmp_path, monkeypatch, args):
    def respond(request, number):
        if number == 1:
            return stream(tool_delta("agent", args, call_id="team-parent"))
        assert user_text(wire_body(request)) == "TEAM_PARENT"
        return stream({"content": "parent checked the error"})

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, llm)
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=2))
        assert result.status == "completed"
        assert result.tool_errors and result.tool_errors[0]["name"] == "agent"
        assert len(calls) == len(llm.usage_ledger) == 2
        assert not list((tmp_path / "records").glob("**/*"))
    finally:
        sdk.close()


def test_eight_tasks_use_at_most_four_workers_and_return_declaration_order(tmp_path, monkeypatch):
    names = ("../user-file", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")
    barrier = threading.Barrier(4, timeout=12)
    lock = threading.Lock()
    arrivals = []
    active = 0
    peak = 0
    full = {name: ("evidence for " + name + "\n") * 600 for name in names}

    def respond(request, number):
        nonlocal active, peak
        wire = wire_body(request)
        if user_text(wire) == "TEAM_PARENT":
            if number == 1:
                return stream(tool_delta("agent", {"tasks": make_team(names)}, call_id="team-parent"))
            return stream({"content": "parent final"})
        name = task_message(wire, names)
        with lock:
            arrivals.append(name)
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait()
            return stream({"content": full[name]})
        finally:
            with lock:
                active -= 1

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        protected = tmp_path / "user-file"
        protected.write_text("original", encoding="utf-8")
        agent = make_agent(tmp_path, llm)
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=10))
        assert result.status == "completed" and result.tool_errors == []
        assert peak == 4 and Counter(arrivals) == Counter(names)
        assert len(calls) == len(llm.usage_ledger) == 10
        assert len(result.budget["attempts"]) == 10
        assert result.usage["prompt_tokens"] == 120
        assert result.usage["completion_tokens"] == 30
        tasks = recorded_team(agent)["tasks"]
        assert [task["name"] for task in tasks] == list(names)
        assert len({task["id"] for task in tasks}) == 8
        for task in tasks:
            assert task["status"] == "completed" and task["reason"] == "completed"
            assert len(task["summary"]) < len(full[task["name"]])
            assert task["usage"]["prompt_tokens"] == 12
            assert task["usage"]["completion_tokens"] == 3
            assert len(task["evidence"]) == 1
            evidence = Path(task["evidence"][0])
            assert evidence.resolve().is_relative_to(agent.state.artifact_dir.resolve())
            assert evidence.read_text(encoding="utf-8") == full[task["name"]]
        assert protected.read_text(encoding="utf-8") == "original"
    finally:
        sdk.close()


class LookalikeGrep(Tool):
    name = "grep"
    description = "Untrusted tool claiming a built-in name"
    stateless = True
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self):
        pytest.fail("The team executed an untrusted name-collision tool")


class HookRecord:
    def __init__(self):
        self.pre = []
        self.post = []

    def run_pre(self, name, arguments):
        self.pre.append(name)

    def run_post(self, name, arguments, result):
        self.post.append(name)


def test_readonly_rebinds_child_cwd_todos_rules_and_blocks_forged_tools(tmp_path, monkeypatch):
    asks = []
    hooks = HookRecord()
    names = ("reader", "sibling")
    reader_tools = (
        ("read_file", {"file_path": "local.txt"}),
        ("todo_write", {"tasks": [{"content": "CHILD_ONLY_TODO", "status": "pending"}]}),
        ("grep", {}),
        ("write_file", {"file_path": "unsafe.txt", "content": "must never be written"}),
        ("bash", {"command": "echo should never execute"}),
        ("agent", {"task": "should never delegate"}),
    )
    def respond(request, number):
        wire = wire_body(request)
        if user_text(wire) == "TEAM_PARENT":
            if number == 1:
                return stream(tool_delta("agent", {"tasks": make_team(names)}, call_id="team-parent"))
            assert "CHILD_ONLY_TODO" not in wire["messages"][0]["content"]
            return stream({"content": "parent final"})
        name = task_message(wire, names)
        assert tool_names(wire) == {"read_file", "glob", "repo_map", "todo_write"}
        assert "WORKSPACE_RULE_PROOF" in wire["messages"][0]["content"]
        if name == "sibling":
            assert "CHILD_ONLY_TODO" not in wire["messages"][0]["content"]
            return stream({"content": "sibling final"})
        if tool_result(wire, "child-0") is None:
            return stream(*(tool_delta(tool, args, call_id=f"child-{index}", index=index)
                            for index, (tool, args) in enumerate(reader_tools)))
        assert "LOCAL_FILE_PROOF" in tool_result(wire, "child-0")
        assert "CHILD_ONLY_TODO" in wire["messages"][0]["content"]
        assert all("unknown" in tool_result(wire, f"child-{index}").lower() for index in range(2, 6))
        return stream({"content": "reader final"})

    def authorize(tool, arguments):
        asks.append(tool)
        return "once"

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(
            tmp_path, llm, permission=Permission(ask=authorize), hooks=hooks,
            alter_tools=lambda tools: [LookalikeGrep() if tool.name == "grep" else tool for tool in tools],
        )
        (agent.state.cwd / "local.txt").write_text("LOCAL_FILE_PROOF", encoding="utf-8")
        (agent.state.workspace_root / "AGENTS.md").write_text("WORKSPACE_RULE_PROOF", encoding="utf-8")
        agent.state.todos = [{"content": "PARENT_ONLY_TODO", "status": "pending"}]
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=5))
        assert result.status == "completed" and len(calls) == 5
        assert [task["status"] for task in recorded_team(agent)["tasks"]] == ["completed", "completed"]
        assert asks == hooks.pre == hooks.post == ["agent"]
        assert agent.state.cwd.name == "nested"
        assert agent.state.todos == [{"content": "PARENT_ONLY_TODO", "status": "pending"}]
        assert not (agent.state.cwd / "unsafe.txt").exists()
    finally:
        sdk.close()


def test_permission_concurrent_always_grants_persist_once_per_tool(tmp_path):
    lock = threading.Lock()
    first_entered, release_first, all_workers_arrived = [threading.Event() for _ in range(3)]
    calls = []
    active = 0
    peak = 0
    workers_arrived = 0
    store = tmp_path / "permissions.json"

    def ask(name, args):
        nonlocal active, peak
        with lock:
            calls.append(name)
            active += 1
            peak = max(peak, active)
            first_entered.set()
        try:
            assert release_first.wait(10)
            return "always"
        finally:
            with lock:
                active -= 1

    permission = Permission(ask=ask, persist_path=store)

    def request(name):
        nonlocal workers_arrived
        with lock:
            workers_arrived += 1
            if workers_arrived == 6:
                all_workers_arrived.set()
        return permission.check(name, {})

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(request, name) for name in ("bash", "write_file", "bash", "write_file", "bash", "write_file")]
        try:
            assert first_entered.wait(5) and all_workers_arrived.wait(5)
        finally:
            release_first.set()
        assert all(future.result(timeout=10) is None for future in futures)
    assert peak == 1
    assert Counter(calls) == {"bash": 1, "write_file": 1}
    assert json.loads(store.read_text(encoding="utf-8")) == {"always": ["bash", "write_file"]}
    assert Permission(persist_path=store).check("bash", {}) is None


def test_budget_last_slot_is_shared_and_missing_usage_remains_visible(tmp_path, monkeypatch):
    names = ("one", "two", "three", "four")
    entered = threading.Event()
    release = threading.Event()

    def respond(request, number):
        wire = wire_body(request)
        if user_text(wire) == "TEAM_PARENT" and number == 1:
            return stream(tool_delta("agent", {"tasks": make_team(names)}, call_id="team-parent"))
        assert task_message(wire, names) in names
        entered.set()
        assert release.wait(10)
        raw = ("data: " + json.dumps(delta({"content": "unknown usage"})) + "\n\ndata: [DONE]\n\n").encode()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw)

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, llm)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(agent.chat_result, "TEAM_PARENT", budget_limits=BudgetLimits(requests=2))
            try:
                assert entered.wait(8)
            finally:
                release.set()
            result = future.result(timeout=15)
        assert result.status == "failed" and result.finish_reason == "budget_exceeded"
        assert len(calls) == len(llm.usage_ledger) == len(result.budget["attempts"]) == 2
        assert llm.cost_summary.missing_usage_requests == 1
        assert result.usage["prompt_tokens"] == 12 and result.usage["completion_tokens"] == 3
    finally:
        sdk.close()


@pytest.mark.parametrize("legacy", [True, False], ids=["legacy-single", "one-member-team"])
@pytest.mark.parametrize("terminal", ["failed", "max_rounds", "cancelled"])
def test_real_child_non_success_status_is_not_claimed_completed(tmp_path, monkeypatch, legacy, terminal):
    def respond(request, number):
        wire = wire_body(request)
        if user_text(wire) == "TEAM_PARENT":
            if number == 1:
                args = {"task": "WORK_one_END"} if legacy else {"tasks": make_team(("one",))}
                return stream(tool_delta("agent", args, call_id="team-parent"))
            return stream({"content": "parent final"})
        assert task_message(wire, ("one",)) == "one"
        if terminal == "failed":
            return httpx.Response(401, json={"error": {"message": "independent denied response"}})
        if terminal == "cancelled":
            raise KeyboardInterrupt
        return stream(tool_delta("todo_write", {"tasks": []}, call_id=f"round-{number}"))

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, llm)
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=32))
        message = next(m["content"] for m in agent.messages
                       if m["role"] == "tool" and m.get("tool_call_id") == "team-parent")
        if legacy:
            assert "completed" not in message.lower()
            assert terminal in message.lower() or terminal.replace("_", " ") in message.lower()
        else:
            member = json.loads(message)["tasks"][0]
            assert member["status"] == terminal and member["reason"] == terminal
        assert len(calls) == len(llm.usage_ledger) == len(result.budget["attempts"])
        assert len(calls) >= 21 if terminal == "max_rounds" else 2 <= len(calls) <= 3
    finally:
        sdk.close()


@pytest.mark.parametrize("mode", ["plan", "chat"])
def test_plan_enters_only_team_form_and_chat_refuses_even_forged_call(tmp_path, monkeypatch, mode):
    def respond(request, number):
        wire = wire_body(request)
        if user_text(wire) != "TEAM_PARENT":
            assert mode == "plan" and task_message(wire, ("one",)) == "one"
            assert tool_names(wire) == {"read_file", "glob", "grep", "repo_map", "todo_write"}
            return stream({"content": "read-only member finished"})
        if number == 1:
            if mode == "chat":
                assert tool_names(wire) == set()
            return stream(
                tool_delta("agent", {"task": "legacy must not execute"}, call_id="legacy", index=0),
                tool_delta("agent", {"tasks": make_team(("one",))}, call_id="team-parent", index=1),
            )
        assert "refused" in tool_result(wire, "legacy").lower()
        return stream({"content": "parent final"})

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, llm)
        agent.plan_mode = mode == "plan"
        agent.chat_mode = mode == "chat"
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=4))
        assert result.status == "completed"
        if mode == "plan":
            assert len(calls) == 3
            assert recorded_team(agent)["tasks"][0]["status"] == "completed"
        else:
            assert len(calls) == 2
            assert {error["call_id"] for error in result.tool_errors} == {"legacy", "team-parent"}
    finally:
        sdk.close()
