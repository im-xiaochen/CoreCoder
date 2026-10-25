"""R02: actual shell cd and file operations agree without process chdir."""

import os

import pytest

from corecoder.agent import Agent
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse, ToolCall


@pytest.mark.parametrize("folder", ["sub", "含空格 子目录"])
def test_cd_then_all_file_tools_share_cwd(tmp_path, state_factory, folder):
    sub = tmp_path / folder
    sub.mkdir()
    (tmp_path / "marker.txt").write_text("ROOT", encoding="utf-8")
    (sub / "marker.txt").write_text("SUB", encoding="utf-8")
    process_cwd = os.getcwd()
    a = Agent(ScriptedLLM([]), state=state_factory())
    tools = a._tool_by_name
    result = tools["bash"].execute(f'cd "{folder}"')
    assert "Error" not in result
    assert a.state.cwd == sub.resolve()
    assert "SUB" in tools["read_file"].execute("marker.txt")
    tools["write_file"].execute("new.txt", "old")
    tools["edit_file"].execute("new.txt", "old", "updated")
    assert (sub / "new.txt").read_text(encoding="utf-8") == "updated"
    assert not (tmp_path / "new.txt").exists()
    assert str(sub / "new.txt") in tools["glob"].execute("new.txt")
    assert str(sub / "new.txt") in tools["grep"].execute("updated", path="new.txt")
    assert str(sub.resolve()) in a._full_messages()[0]["content"]
    assert os.getcwd() == process_cwd


def test_same_batch_cd_then_write_is_ordered(tmp_path, state_factory):
    (tmp_path / "sub").mkdir()
    a = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[
            ToolCall("cd", "bash", {"command": "cd sub"}),
            ToolCall("write", "write_file", {"file_path": "ordered.txt", "content": "OK"}),
        ]), LLMResponse(content="done"),
    ]), state=state_factory())
    a.chat("go")
    assert (tmp_path / "sub" / "ordered.txt").read_text(encoding="utf-8") == "OK"
    assert not (tmp_path / "ordered.txt").exists()


def test_child_starts_here_and_cannot_change_parent_cwd(tmp_path, state_factory):
    work = tmp_path / "work"
    (work / "nested").mkdir(parents=True)
    (work / "marker.txt").write_text("INHERITED", encoding="utf-8")
    a = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("spawn", "agent", {"task": "inspect"})]),
        LLMResponse(tool_calls=[ToolCall("read", "read_file", {"file_path": "marker.txt"})]),
        LLMResponse(tool_calls=[ToolCall("cd", "bash", {"command": "cd nested"})]),
        LLMResponse(tool_calls=[ToolCall("write", "write_file", {"file_path": "child.txt", "content": "OK"})]),
        LLMResponse(content="child done"), LLMResponse(content="parent done"),
    ]), state=state_factory(work))
    a.chat("go")
    assert (work / "nested" / "child.txt").exists()
    assert a.state.cwd == work.resolve()


def test_failed_cd_and_absolute_paths(tmp_path, state_factory):
    a = Agent(ScriptedLLM([]), state=state_factory())
    a._tool_by_name["bash"].execute("cd does-not-exist")
    assert a.state.cwd == tmp_path.resolve()
    target = tmp_path / "absolute.txt"
    a._tool_by_name["write_file"].execute(str(target), "ABS")
    assert "ABS" in a._tool_by_name["read_file"].execute(str(target))
