"""R01: each top-level agent owns its tools and children keep its authority."""

from corecoder.agent import Agent
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse, ToolCall
from corecoder.permissions import Permission


def test_default_tools_and_parent_are_distinct():
    a = Agent(ScriptedLLM([]))
    b = Agent(ScriptedLLM([]))
    assert a._tool_by_name.keys() == b._tool_by_name.keys()
    assert all(a._tool_by_name[name] is not b._tool_by_name[name] for name in a._tool_by_name)
    assert a._tool_by_name["agent"]._parent_agent is a
    assert b._tool_by_name["agent"]._parent_agent is b


def test_todo_and_reset_do_not_cross_agents():
    a = Agent(ScriptedLLM([]))
    b = Agent(ScriptedLLM([]))
    a._todo.execute([{"content": "A_PRIVATE", "status": "pending"}])
    b._todo.execute([{"content": "B_PRIVATE", "status": "pending"}])
    assert "A_PRIVATE" in a._full_messages()[0]["content"]
    assert "B_PRIVATE" not in a._full_messages()[0]["content"]
    a.reset()
    assert "B_PRIVATE" in b._full_messages()[0]["content"]


def test_child_uses_own_parents_model_and_denial(tmp_path):
    target = tmp_path / "must-not-exist.txt"
    asked = []

    def ask(name, arguments):
        asked.append(name)
        return "once" if name == "agent" else "deny"

    a = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("spawn", "agent", {"task": "write"})]),
        LLMResponse(tool_calls=[ToolCall("write", "write_file", {
            "file_path": str(target), "content": "forbidden",
        })]),
        LLMResponse(content="child finished"), LLMResponse(content="parent finished"),
    ]), permission=Permission(ask=ask))
    b = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("wrong", "write_file", {
            "file_path": str(target), "content": "wrong authority",
        })]), LLMResponse(content="wrong model"),
    ]), permission=Permission(allow_all=True))
    a.chat("go")
    assert asked == ["agent", "write_file"]
    assert not target.exists()
    assert b.llm._turns, "B's model must not have been consumed"


def test_child_todo_does_not_replace_parent_todo():
    a = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("spawn", "agent", {"task": "plan"})]),
        LLMResponse(tool_calls=[ToolCall("todo", "todo_write", {"tasks": [
            {"content": "CHILD_ONLY", "status": "done"},
        ]})]), LLMResponse(content="child done"), LLMResponse(content="parent done"),
    ]), permission=Permission(allow_all=True))
    a._todo.execute([{"content": "PARENT_ONLY", "status": "pending"}])
    a.chat("go")
    assert "PARENT_ONLY" in a._todo.render()
    assert "CHILD_ONLY" not in a._todo.render()
