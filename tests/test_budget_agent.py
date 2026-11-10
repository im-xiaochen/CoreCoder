import sys
from types import SimpleNamespace
from typing import ClassVar

import pytest

from corecoder.agent import Agent
from corecoder.budget import BudgetExceeded, BudgetLimits, RunBudget
from corecoder.context import ContextManager
from corecoder.llm import LiteLLM, budget_scope


def test_litellm_budget_rejected_before_import(monkeypatch):
    monkeypatch.setitem(sys.modules, "litellm", None)
    llm = LiteLLM("fake")
    with budget_scope(RunBudget()), pytest.raises(BudgetExceeded):
        llm.chat([{"role": "user", "content": "hello"}])
    assert not llm.usage_ledger


def test_litellm_default_still_calls(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=lambda **kw: calls.append(kw) or iter([])))
    LiteLLM("fake").chat([{"role": "user", "content": "hello"}])
    assert len(calls) == 1


def test_unsupported_agent_client_rejected_before_send():
    client = SimpleNamespace(chat=lambda **kw: pytest.fail("sent"))
    agent = Agent(llm=client, tools=[])
    result = agent.chat_result("hello", budget_limits=BudgetLimits(requests=1))
    assert result.status == "failed" and result.finish_reason == "budget_exceeded"
    assert result.budget["attempts"] == {}


def test_summary_does_not_swallow_budget_exceeded():
    client = SimpleNamespace(chat=lambda **kw: (_ for _ in ()).throw(BudgetExceeded("limit")))
    with pytest.raises(BudgetExceeded):
        ContextManager(max_tokens=100000)._get_summary([{"role": "user", "content": "hello"}], client)


def test_child_shares_parent_budget_and_propagates(tmp_path):
    from corecoder.llm import LLM
    from corecoder.state import SessionState
    from corecoder.tools.agent import AgentTool
    model = LLM("fake", "fake", max_tokens=4)
    parent = Agent(llm=model, state=SessionState(workspace_root=tmp_path), tools=[])
    tool = AgentTool()
    tool.bind_parent(parent)
    budget = RunBudget(BudgetLimits(requests=0))
    with budget_scope(budget), pytest.raises(BudgetExceeded):
        tool.execute("child")
    assert budget.snapshot()["attempts"] == {}


def test_parent_budget_exceeded_finish_reason(tmp_path):
    from corecoder.llm import LLM
    from corecoder.state import SessionState
    model = LLM("fake", "fake", max_tokens=4)
    agent = Agent(llm=model, state=SessionState(workspace_root=tmp_path), tools=[])
    result = agent.chat_result("hello", budget_limits=BudgetLimits(requests=0))
    assert result.status == "failed" and result.finish_reason == "budget_exceeded"
    assert result.budget["attempts"] == {}


def test_parent_and_child_share_last_requests(tmp_path):
    from corecoder.llm import LLM
    from corecoder.state import SessionState
    from corecoder.tools.agent import AgentTool

    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None, completion_tokens_details=None)
    function = SimpleNamespace(name="agent", arguments='{"task":"child"}')
    call = SimpleNamespace(index=0, id="call-1", function=function)
    delta = SimpleNamespace(content=None, reasoning_content=None, reasoning=None, tool_calls=[call])
    parent_stream = [SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta)]), SimpleNamespace(usage=usage, choices=[])]
    child_stream = [SimpleNamespace(usage=usage, choices=[])]
    class Transport:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)
            self.calls = 0
        def with_options(self, **kwargs):
            return self
        def create(self, **kwargs):
            self.calls += 1
            return iter([parent_stream, child_stream][self.calls - 1])
    model = LLM("fake", "fake", max_tokens=4)
    model.client = Transport()
    agent = Agent(llm=model, state=SessionState(workspace_root=tmp_path), tools=[AgentTool()])
    result = agent.chat_result("parent", budget_limits=BudgetLimits(requests=2))
    assert result.finish_reason == "budget_exceeded" and result.status == "failed"
    assert model.client.calls == 2
    assert result.budget["groups"]["actual"]["requests"] == 2


def test_started_tool_budget_failure_preserves_side_effect(tmp_path):
    from corecoder.llm import ToolCall
    from corecoder.state import SessionState
    from corecoder.tools.base import Tool

    class MutatingTool(Tool):
        name = "mutate"
        description = "test"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}
        def execute(self):
            (tmp_path / "written").write_text("done")
            raise BudgetExceeded("limit")

    agent = Agent(llm=SimpleNamespace(), state=SessionState(workspace_root=tmp_path), tools=[MutatingTool()])
    call = ToolCall(id="started", name="mutate", arguments={})
    pending = ToolCall(id="pending", name="mutate", arguments={})
    with pytest.raises(BudgetExceeded):
        agent._run_tool_call(call)
    agent._answer_pending_tool_calls([call, pending])
    assert (tmp_path / "written").read_text() == "done"
    assert "tool started" in agent.messages[-2]["content"]
    assert "cancelled before start" in agent.messages[-1]["content"]


def test_unsupported_client_updates_last_result():
    agent = Agent(llm=SimpleNamespace(), tools=[])
    result = agent.chat_result("hello", budget_limits=BudgetLimits(requests=1))
    assert agent.last_result is result and result.finish_reason == "budget_exceeded"
