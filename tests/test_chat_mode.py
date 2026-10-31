"""Chat mode never grants project access, even to unexpected provider calls."""

from copy import deepcopy
from typing import ClassVar
from unittest.mock import Mock

from corecoder import Agent, SessionState
from corecoder.context import ContextManager
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse, ToolCall
from corecoder.permissions import Permission
from corecoder.tools import create_tools
from corecoder.tools.base import Tool


class CapturingLLM(ScriptedLLM):
    def __init__(self, script):
        super().__init__(script)
        self.requests = []

    def chat(self, messages, tools=None, on_token=None, on_reasoning=None):
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        return super().chat(messages, tools, on_token, on_reasoning)


class ExtensionTool(Tool):
    name = "mcp_example"
    description = "An external tool that must not run in chat mode."
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self):
        raise AssertionError("MCP tool ran in chat mode")


def test_chat_streams_without_tool_schemas_or_project_instructions(tmp_path):
    llm = CapturingLLM([LLMResponse(content="可以讨论这段代码。")])
    agent = Agent(llm, state=SessionState(workspace_root=tmp_path))
    agent._todo.execute([{"content": "private task", "status": "pending"}])
    agent.chat_mode = True
    tokens = []

    assert agent.chat("解释代码", on_token=tokens.append) == "可以讨论这段代码。"
    assert tokens == ["可以讨论这段代码。"]
    messages, schemas = llm.requests[0]
    assert schemas == []
    system = messages[0]["content"]
    assert "chat mode" in system and "No tools are available" in system
    assert str(tmp_path) not in system
    assert "private task" not in system
    assert "# Tools" not in system
    assert list(tmp_path.iterdir()) == []


def test_unexpected_tool_batch_is_refused_before_callbacks_hooks_permissions_or_tools(tmp_path):
    state = SessionState(workspace_root=tmp_path)
    calls = [
        ToolCall("read", "read_file", {"file_path": "private.txt"}),
        ToolCall("glob", "glob", {"pattern": "*"}),
        ToolCall("grep", "grep", {"pattern": "secret"}),
        ToolCall("write", "write_file", {"file_path": "created.txt", "content": "changed"}),
        ToolCall("edit", "edit_file", {"file_path": "private.txt", "old_string": "private", "new_string": "public"}),
        ToolCall("shell", "bash", {"command": "echo changed"}),
        ToolCall("child", "agent", {"task": "read the project"}),
        ToolCall("todo", "todo_write", {"tasks": [{"content": "new", "status": "pending"}]}),
        ToolCall("external", "mcp_example", {}),
        ToolCall("unknown", "unoffered_tool", {}),
        ToolCall("invalid", "write_file", []),
    ]
    llm = CapturingLLM([LLMResponse(tool_calls=calls), LLMResponse(content="请切换模式以读取项目。")])
    hooks = Mock()
    permission = Mock()
    on_tool = Mock()
    tools = [*create_tools(state), ExtensionTool()]
    for tool in tools:
        # Tool-side validation is extension code too; chat must not enter it.
        tool.validate_arguments = Mock(side_effect=AssertionError("tool validation ran"))
        tool.execute = Mock(side_effect=AssertionError("tool executed"))
    agent = Agent(llm, tools=tools, state=state, permission=permission, hooks=hooks)
    private = tmp_path / "private.txt"
    private.write_text("private", encoding="utf-8")
    agent.chat_mode = True

    result = agent.chat_result("请处理项目", on_tool=on_tool)

    assert result.status == "completed"
    assert len(result.tool_errors) == len(calls)
    assert all(error["status"] == "refused" for error in result.tool_errors)
    assert all(schemas == [] for _, schemas in llm.requests)
    assert hooks.mock_calls == []
    assert permission.mock_calls == []
    on_tool.assert_not_called()
    for tool in tools:
        tool.validate_arguments.assert_not_called()
        tool.execute.assert_not_called()
    assert private.read_text(encoding="utf-8") == "private"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["private.txt"]
    assert agent._todo._tasks == []
    assert not state.changed_files
    outcomes = [message for message in agent.messages if message["role"] == "tool"]
    assert len(outcomes) == len(calls)
    assert {message["tool_call_id"] for message in outcomes} == {call.id for call in calls}
    assert all("Chat mode is on" in message["content"] for message in outcomes)
    assert len(state.execution_records) == len(calls)
    assert all(record["status"] == "refused" for record in state.execution_records)
    # Restored canonical history has no explicit status fields. Compression
    # must retain these refusals instead of inventing successful file changes.
    records = ContextManager.extract_records(agent.messages)
    assert all(record.status == "refused" for record in records)
    memory = ContextManager.build_memory(agent.messages)
    assert memory.changes == []
    assert len(memory.failures) == len(calls)
    assert "status=succeeded" not in ContextManager._flatten(agent.messages)
    summary = agent.context._get_summary(agent.messages, None)
    assert "FAILURES:" in summary and "status=refused" in summary
    assert "CHANGES:" not in summary


def test_leaving_chat_restores_coding_prompt_schemas_and_execution(tmp_path):
    llm = CapturingLLM([
        LLMResponse(content="先讨论。"),
        LLMResponse(tool_calls=[ToolCall("write", "write_file", {"file_path": "note.txt", "content": "hello"})]),
        LLMResponse(content="已写入。"),
    ])
    agent = Agent(llm, state=SessionState(workspace_root=tmp_path), permission=Permission(allow_all=True))
    agent.chat_mode = True
    agent.chat("讨论方案")
    agent.chat_mode = False
    agent.chat("实施方案")

    assert llm.requests[0][1] == []
    assert llm.requests[1][1]
    assert "# Tools" in llm.requests[1][0][0]["content"]
    assert "chat mode" not in llm.requests[1][0][0]["content"]
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello"
