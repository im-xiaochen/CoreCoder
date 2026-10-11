"""R07: malformed calls never reach side effects or leave an incomplete chain."""

from types import SimpleNamespace
from typing import ClassVar

import pytest

from corecoder.agent import Agent
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLM, LLMResponse, ToolCall
from corecoder.permissions import Permission
from corecoder.tools.base import Tool
from corecoder.tools.write import WriteFileTool
from designDocument.tests.support import assert_complete_tool_chain, stream_chunk, tool_reply


@pytest.mark.parametrize("arguments", [[], None, "text", 42, {"file_path": 7, "content": "x"},
                                           {"file_path": "x"}, {"file_path": "x", "content": "x", "surprise": True}])
def test_invalid_arguments_are_replied_before_callbacks(arguments):
    called = []
    hooks = SimpleNamespace(run_pre=lambda *a: called.append("pre"), run_post=lambda *a: called.append("post"))
    permission = Permission(ask=lambda *a: called.append("permission") or "once")
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("bad", "write_file", arguments)]),
        LLMResponse(content="recovered"), LLMResponse(content="next turn"),
    ]), tools=[WriteFileTool()], permission=permission, hooks=hooks)
    agent.chat("go", on_tool=lambda *a: called.append("display"))
    assert "error" in tool_reply(agent, "bad").lower()
    assert called == []
    assert_complete_tool_chain(agent.messages)
    agent.chat("continue")
    assert_complete_tool_chain(agent.messages)


def test_python_signature_error_is_before_callbacks():
    called = []

    class UnderspecifiedTool(Tool):
        name = "underspecified"
        description = "Schema omits a required Python argument"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}

        def execute(self, required):
            called.append("execute")
            return required

    hooks = SimpleNamespace(
        run_pre=lambda *a: called.append("pre"),
        run_post=lambda *a: called.append("post"),
    )
    agent = Agent(
        ScriptedLLM([
            LLMResponse(tool_calls=[ToolCall("signature", "underspecified", {})]),
            LLMResponse(content="done"),
        ]),
        tools=[UnderspecifiedTool()],
        hooks=hooks,
    )
    assert agent.chat_result("call").status == "completed"
    assert "invalid_arguments" in tool_reply(agent, "signature")
    assert called == []


def test_truncated_json_retains_error_and_raw_arguments():
    llm = LLM(model="offline", api_key="offline-test")
    response = llm._drain(iter([stream_chunk('{"file_path":')]), None, None)
    call = response.tool_calls[0]
    assert getattr(call, "arguments_error", None), "Malformed JSON must not silently become {}"
    assert getattr(call, "raw_arguments", None) == '{"file_path":'


@pytest.mark.parametrize("stage", ["display", "pre", "permission"])
def test_pre_execution_exceptions_produce_reply_without_write(tmp_path, stage):
    target = tmp_path / "must-not-exist.txt"

    def fail(*args):
        raise RuntimeError(f"{stage} failed")

    hooks = SimpleNamespace(run_pre=fail if stage == "pre" else lambda *a: None,
                            run_post=lambda *a: None)
    permission = Permission(ask=fail if stage == "permission" else lambda *a: "once")
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "write_file", {"file_path": str(target), "content": "x"})]),
        LLMResponse(content="done"),
    ]), tools=[WriteFileTool()], permission=permission, hooks=hooks)
    agent.chat("go", on_tool=fail if stage == "display" else None)
    assert not target.exists()
    assert "error" in tool_reply(agent, "c1").lower()
    assert_complete_tool_chain(agent.messages)


def test_post_hook_failure_keeps_executed_outcome(tmp_path):
    target = tmp_path / "written.txt"

    def fail(*args):
        raise RuntimeError("post failed")

    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("c1", "write_file", {"file_path": str(target), "content": "x"})]),
        LLMResponse(content="done"),
    ]), tools=[WriteFileTool()], hooks=SimpleNamespace(run_pre=lambda *a: None, run_post=fail))
    agent.chat("go")
    assert target.read_text(encoding="utf-8") == "x"
    result = tool_reply(agent, "c1")
    assert "Wrote" in result and "post" in result.lower()
    assert_complete_tool_chain(agent.messages)


def test_mixed_batch_and_unknown_tool_are_answered_individually(tmp_path):
    target = tmp_path / "valid.txt"
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[
            ToolCall("bad", "write_file", []), ToolCall("unknown", "not_registered", {}),
            ToolCall("good", "write_file", {"file_path": str(target), "content": "OK"}),
        ]), LLMResponse(content="done"),
    ]), tools=[WriteFileTool()])
    agent.chat("go")
    assert "error" in tool_reply(agent, "bad").lower()
    assert "unknown" in tool_reply(agent, "unknown").lower()
    assert target.read_text(encoding="utf-8") == "OK"
    assert_complete_tool_chain(agent.messages)
