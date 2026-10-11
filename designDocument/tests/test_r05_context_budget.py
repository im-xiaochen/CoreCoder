"""R05: full requests fit the configured estimate or are refused explicitly."""

import copy
from pathlib import Path

import pytest

from corecoder import context
from corecoder.agent import Agent
from corecoder.context import ContextManager
from corecoder.llm import LLMResponse, ToolCall
from designDocument.tests.support import RecordingLLM, assert_complete_tool_chain, required_attr


def prepare(manager, messages, tmp_path, **overrides):
    prepare_request = required_attr(manager, "prepare_request")
    options = {"system": "system", "tools": [], "output_reserve": 128, "artifact_dir": tmp_path / "artifacts"}
    options.update(overrides)
    result = prepare_request(messages, **options)
    assert isinstance(result.budget_ok, bool)
    return result


@pytest.mark.parametrize("body", ["x" * 100_000, "中文长输出" * 20_000], ids=["single-line", "cjk"])
def test_single_line_output_is_archived_and_fits(tmp_path, body):
    calls = [ToolCall("read", "read_file", {"file_path": "huge.txt"})]
    messages = [LLMResponse(tool_calls=calls).message,
                {"role": "tool", "tool_call_id": "read", "content": body}]
    result = prepare(ContextManager(max_tokens=2048), messages, tmp_path)
    assert result.budget_ok and result.changed
    assert result.input_tokens + result.output_reserve <= result.limit
    assert result.artifacts
    archive = Path(result.artifacts[0])
    assert archive.is_relative_to(tmp_path)
    assert archive.read_text(encoding="utf-8") == body
    # Inspect the model-visible text, not list repr (which escapes Windows
    # backslashes and makes an otherwise valid native path fail this check).
    visible = "\n".join(message.get("content") or "" for message in result.messages)
    assert str(archive) in visible or archive.as_posix() in visible
    assert_complete_tool_chain(result.messages)


def test_oversized_last_group_keeps_every_reply_and_fits(tmp_path):
    calls = [ToolCall(str(i), "read_file", {"file_path": f"{i}.txt"}) for i in range(6)]
    messages = [LLMResponse(tool_calls=calls).message]
    messages.extend({"role": "tool", "tool_call_id": str(i), "content": "x" * 10_000} for i in range(6))
    result = prepare(ContextManager(max_tokens=4096), messages, tmp_path)
    assert result.budget_ok
    assert result.input_tokens + result.output_reserve <= 4096
    assert {m["tool_call_id"] for m in result.messages if m["role"] == "tool"} == {str(i) for i in range(6)}
    assert_complete_tool_chain(result.messages)


@pytest.mark.parametrize("fixed_part", ["system", "tools"])
def test_fixed_request_cost_can_refuse_without_mutating_history(tmp_path, fixed_part):
    messages = [{"role": "user", "content": "keep me"}]
    before = copy.deepcopy(messages)
    options = {fixed_part: "x" * 50_000} if fixed_part == "system" else {
        "tools": [{"type": "function", "function": {"name": "huge", "description": "x" * 50_000,
                                                     "parameters": {"type": "object"}}}],
    }
    result = prepare(ContextManager(max_tokens=1024), messages, tmp_path, **options)
    assert not result.budget_ok
    assert result.reason == "context_budget_exceeded"
    assert messages == before


def test_latest_user_request_is_never_silently_truncated(tmp_path):
    text = "用户约束不得丢失" * 10_000
    messages = [{"role": "user", "content": text}]
    result = prepare(ContextManager(max_tokens=512), messages, tmp_path)
    assert not result.budget_ok
    assert messages[0]["content"] == text


def test_count_includes_system_schemas_and_reserve(tmp_path):
    count_request = required_attr(context, "estimate_request_tokens")
    result = prepare(ContextManager(max_tokens=4096), [{"role": "user", "content": "hello"}], tmp_path,
                     system="rules" * 30)
    assert result.budget_ok
    assert result.input_tokens == count_request(result.messages, system="rules" * 30, tools=[])
    assert result.output_reserve == 128


def test_agent_refuses_before_sending_an_over_budget_request():
    exception_type = required_attr(context, "ContextBudgetExceeded")
    llm = RecordingLLM()
    agent = Agent(llm, max_context_tokens=64)
    with pytest.raises(exception_type):
        agent.chat("用户约束" * 10_000)
    assert llm.requests == []
