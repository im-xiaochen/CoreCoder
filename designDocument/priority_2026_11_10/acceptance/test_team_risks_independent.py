"""Focused validation of the stage-two static review corrections."""
import os
import subprocess
import threading

import pytest

from corecoder.budget import BudgetLimits
from corecoder.tools.agent import AgentTool
from corecoder.tools.read import ReadFileTool
from sdk_fixture import llm_with_transport, stream
from test_slice2_independent import make_agent, make_team, recorded_team, tool_delta, user_text, wire_body


def test_plan_does_not_trust_subclass_claiming_the_production_module(tmp_path, monkeypatch):
    class ForgedAgentTool(AgentTool):
        __module__ = "corecoder.tools.agent"

        def execute(self, task=None, tasks=None):
            pytest.fail("A custom subclass was allowed to enter plan execution")

    def respond(request, number):
        if number == 1:
            return stream(tool_delta("agent", {"tasks": make_team(("one",))}, call_id="team-parent"))
        return stream({"content": "plan only"})

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(
            tmp_path, llm,
            alter_tools=lambda tools: [ForgedAgentTool() if tool.name == "agent" else tool for tool in tools],
        )
        agent.plan_mode = True
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=2))
        assert result.status == "completed" and len(calls) == 2
        assert result.tool_errors[0]["status"] == "refused"
        assert not list(agent.state.artifact_dir.glob("**/*"))
    finally:
        sdk.close()


def test_linked_managed_artifact_ancestor_creates_no_external_directories(tmp_path, monkeypatch):
    def respond(request, number):
        if number == 1:
            return stream(tool_delta("agent", {"tasks": make_team(("one",))}, call_id="team-parent"))
        return stream({"content": "the member still completed"})

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, llm)
        external = tmp_path / "outside"
        external.mkdir()
        if os.name == "nt":
            subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(agent.state.artifact_dir), str(external)],
                check=True, capture_output=True,
            )
        else:
            agent.state.artifact_dir.symlink_to(external, target_is_directory=True)
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=3))
        assert result.status == "completed" and len(calls) == 3
        task = recorded_team(agent)["tasks"][0]
        assert task["status"] == "completed"
        assert task["artifact_error"] and not task["evidence"]
        assert list(external.iterdir()) == []
    finally:
        sdk.close()


def test_member_clone_exception_does_not_erase_other_member_results(tmp_path, monkeypatch):
    original_clone = ReadFileTool.clone_for_state
    first_clone = threading.Event()
    lock = threading.Lock()

    def fail_first_clone(self, state):
        with lock:
            if not first_clone.is_set():
                first_clone.set()
                raise RuntimeError("independent clone failure")
        return original_clone(self, state)

    def respond(request, number):
        if number == 1:
            return stream(tool_delta("agent", {"tasks": make_team(("one", "two"))}, call_id="team-parent"))
        prompt = user_text(wire_body(request))
        return stream({"content": "parent final" if prompt == "TEAM_PARENT" else "surviving member"})

    llm, calls, sdk = llm_with_transport(monkeypatch, respond)
    try:
        agent = make_agent(tmp_path, llm)
        monkeypatch.setattr(ReadFileTool, "clone_for_state", fail_first_clone)
        result = agent.chat_result("TEAM_PARENT", budget_limits=BudgetLimits(requests=3))
        assert result.status == "completed" and len(calls) == 3
        tasks = recorded_team(agent)["tasks"]
        assert [task["name"] for task in tasks] == ["one", "two"]
        assert {task["status"] for task in tasks} == {"completed", "failed"}
        failed = next(task for task in tasks if task["status"] == "failed")
        assert failed["id"] and "independent clone failure" in failed["reason"]
    finally:
        sdk.close()
