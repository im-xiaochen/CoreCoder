"""R09: completion states, exit codes, and JSON output are machine-readable."""

import json
from types import SimpleNamespace

import pytest

from corecoder import cli
from corecoder.agent import Agent
from corecoder.config import Config
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse, ToolCall
from corecoder.tools.read import ReadFileTool
from designDocument.tests.support import assert_complete_tool_chain, required_attr


def make_agent(mode, tmp_path):
    if mode == "completed":
        return Agent(ScriptedLLM([LLMResponse(content="DONE")]), tools=[])
    if mode == "max_rounds":
        target = tmp_path / "read.txt"
        target.write_text("OK", encoding="utf-8")
        return Agent(ScriptedLLM([LLMResponse(tool_calls=[
            ToolCall("read", "read_file", {"file_path": str(target)}),
        ])]), tools=[ReadFileTool()], max_rounds=1)

    class BrokenLLM(ScriptedLLM):
        def chat(self, *args, **kwargs):
            if mode == "cancelled":
                raise KeyboardInterrupt
            raise RuntimeError("offline failure")

    return Agent(BrokenLLM([]), tools=[])


@pytest.mark.parametrize("mode", ["completed", "max_rounds", "cancelled", "failed"])
def test_structured_result_has_stable_finish_status(tmp_path, mode):
    agent = make_agent(mode, tmp_path)
    result = required_attr(agent, "chat_result")("go")
    assert result.status == mode
    assert isinstance(result.text, str)
    assert isinstance(result.tool_errors, list)
    assert isinstance(result.rounds, int)
    assert_complete_tool_chain(agent.messages)


@pytest.mark.parametrize("mode,code", [("completed", 0), ("max_rounds", 2), ("cancelled", 130), ("failed", 1)])
def test_one_shot_exit_code_and_clean_json(tmp_path, capsys, mode, code):
    agent = make_agent(mode, tmp_path)
    assert cli._run_once(agent, "go", json_output=True) == code
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["schema_version"] == 1
    assert result["status"] == mode
    assert "text" in result and "tool_errors" in result and "usage" in result
    assert result["exit_code"] == code


def test_text_mode_reports_exhaustion_to_stderr(tmp_path, capsys):
    code = cli._run_once(make_agent("max_rounds", tmp_path), "go")
    captured = capsys.readouterr()
    assert code == 2
    assert "max_rounds" in captured.err
    assert "> read_file" not in captured.out


@pytest.mark.parametrize("code", [0, 1, 2, 130])
def test_main_propagates_one_shot_exit_code(monkeypatch, code):
    args = SimpleNamespace(demo=False, model=None, base_url=None, api_key=None, yes=True,
                           prompt="go", resume=None, json=True)
    monkeypatch.setattr(cli, "_parse_args", lambda: args)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: Config(api_key="offline-test")))
    monkeypatch.setattr(cli, "LLM", lambda **kwargs: object())
    monkeypatch.setattr(cli, "Agent", lambda **kwargs: object())
    monkeypatch.setattr(cli, "load_mcp_tools", list)
    monkeypatch.setattr(cli, "load_hooks", lambda: None)
    monkeypatch.setattr(cli, "_run_once", lambda *args, **kwargs: code)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == code


def test_completed_result_keeps_tool_failures_visible(tmp_path):
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("bad", "read_file", {"file_path": str(tmp_path / "missing")})]),
        LLMResponse(content="The loop has ended"),
    ]), tools=[ReadFileTool()])
    result = required_attr(agent, "chat_result")("go")
    assert result.status == "completed"
    assert result.tool_errors and result.tool_errors[0]["call_id"] == "bad"
