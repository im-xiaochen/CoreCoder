"""Primary-agent acceptance of runtime, ledger and persistence boundaries."""

import json
import logging
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from openai import APIConnectionError, BadRequestError

from corecoder import cli, mcp, session
from corecoder.agent import Agent
from corecoder.config import Config
from corecoder.context import ContextManager
from corecoder.demo import ScriptedLLM
from corecoder.hooks import load_hooks
from corecoder.llm import LLM, LLMResponse, ToolCall
from corecoder.permissions import Permission
from corecoder.tools.agent import AgentTool
from corecoder.tools.base import Tool
from corecoder.tools.write import WriteFileTool
from designDocument.tests.support import assert_complete_tool_chain, stream_chunk, tool_reply


class NestedTool(Tool):
    name = "nested_probe"
    description = "Offline schema probe"
    parameters: ClassVar[dict] = {
        "type": "object", "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"count": {"type": "integer"}, "mode": {"enum": ["read", "inspect"]}},
            "required": ["count", "mode"],
        }}}, "required": ["items"],
    }

    def __init__(self):
        self.calls = []

    def execute(self, items):
        self.calls.append(items)
        return "observed"


@pytest.mark.parametrize("item", [
    {"count": True, "mode": "read"}, {"count": 1, "mode": "write"},
    {"count": 1}, {"count": 1, "mode": "read", "extra": 7},
])
def test_nested_schema_rejects_before_any_callback(item, state_factory):
    tool, callbacks = NestedTool(), []
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("bad-nested", tool.name, {"items": [item]})]),
        LLMResponse(content="recovered"),
    ]), state=state_factory(), tools=[tool], permission=Permission(allow_all=True))
    assert agent.chat("inspect", on_tool=lambda *a: callbacks.append(a)) == "recovered"
    assert tool.calls == [] and callbacks == []
    assert "invalid_arguments" in tool_reply(agent, "bad-nested")
    assert_complete_tool_chain(agent.messages)


def test_remote_schema_keeps_explicit_additional_properties(state_factory):
    class RemoteTool(Tool):
        name = "mcp__offline__open"
        description = "Remote schema permits arbitrary input"
        parameters: ClassVar[dict] = {"type": "object", "additionalProperties": True}

        def execute(self, **kwargs):
            return json.dumps(kwargs)

    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("open-input", "mcp__offline__open", {"new_field": 42})]),
        LLMResponse(content="done"),
    ]), state=state_factory(), tools=[RemoteTool()])
    agent.chat("inspect")
    assert '"new_field": 42' in tool_reply(agent, "open-input")


@pytest.mark.parametrize("calls", [
    [ToolCall("", "write_file", {"file_path": "bad.txt", "content": "bad"})],
    [ToolCall("same", "write_file", {}), ToolCall("same", "write_file", {})],
    [ToolCall("missing-name", "", {})],
])
def test_invalid_call_envelope_is_rejected_before_history_or_side_effect(calls, state_factory, tmp_path):
    agent = Agent(ScriptedLLM([LLMResponse(tool_calls=calls)]), state=state_factory())
    result = agent.chat_result("inspect")
    assert result.status == "failed"
    assert result.rounds == 1
    assert not any(m.get("tool_calls") for m in agent.messages)
    assert not (tmp_path / "bad.txt").exists()
    assert_complete_tool_chain(agent.messages)


def test_reused_id_in_later_round_gets_its_own_reply(state_factory, tmp_path):
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("reused", "write_file", {"file_path": "same.txt", "content": "first"})]),
        LLMResponse(tool_calls=[ToolCall("reused", "write_file", {"file_path": "same.txt", "content": "second"})]),
        LLMResponse(content="done"),
    ]), state=state_factory())
    assert agent.chat_result("write").status == "completed"
    assert len([m for m in agent.messages if m.get("tool_call_id") == "reused"]) == 2
    assert (tmp_path / "same.txt").read_text() == "second"
    session.save_session(agent.messages, "offline", "reused-id")
    assert session.load_session("reused-id") == (agent.messages, "offline")


def test_reused_id_keeps_prior_success_separate_from_later_failure(state_factory):
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "same", "function": {
            "name": "write_file", "arguments": {"file_path": "first.txt", "content": "one"}
        }}]},
        {"role": "tool", "tool_call_id": "same", "status": "succeeded", "content": "Wrote first.txt"},
        {"role": "assistant", "tool_calls": [{"id": "same", "function": {
            "name": "write_file", "arguments": {"file_path": "second.txt", "content": "two"}
        }}]},
        {"role": "tool", "tool_call_id": "same", "status": "refused", "content": "Permission denied"},
    ]
    records = ContextManager.extract_records(messages)
    assert [(record.target, record.status, record.result) for record in records] == [
        ("first.txt", "succeeded", "Wrote first.txt"),
        ("second.txt", "refused", "Permission denied"),
    ]

    state = state_factory()
    state.record_outcome("same", "write_file", "succeeded", {"file_path": "first.txt"}, "Wrote first.txt")
    state.record_outcome("same", "write_file", "refused", {"file_path": "second.txt"}, "Permission denied")
    assert [(record.target, record.status) for record in state.task_memory.records] == [
        (str(state.cwd / "first.txt"), "succeeded"),
        (str(state.cwd / "second.txt"), "refused"),
    ]


def test_reused_id_does_not_inherit_old_result_on_validation_interrupt(state_factory, tmp_path):
    class InterruptValidator(WriteFileTool):
        def validate_arguments(self, arguments):
            if arguments.get("content") == "second":
                raise KeyboardInterrupt
            return super().validate_arguments(arguments)

    state = state_factory()
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("reused", "write_file", {"file_path": "same.txt", "content": "first"})]),
        LLMResponse(tool_calls=[ToolCall("reused", "write_file", {"file_path": "same.txt", "content": "second"})]),
    ]), tools=[InterruptValidator(state)], state=state)
    result = agent.chat_result("write")
    replies = [m["content"] for m in agent.messages if m.get("tool_call_id") == "reused"]
    assert result.status == "cancelled" and result.rounds == 2
    assert len(replies) == 2 and "Wrote" in replies[0] and "cancelled" in replies[1]
    assert (tmp_path / "same.txt").read_text() == "first"
    sid = session.save_session(agent.messages, "offline", "cancelled-reuse")
    assert session.load_session(sid) == (agent.messages, "offline")


def test_non_string_tool_result_is_serialized(state_factory):
    class MappingTool(Tool):
        name = "mapping"
        description = "Returns structured data"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}

        def execute(self):
            return {"result": 42}

    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("mapping-call", "mapping", {})]), LLMResponse(content="done"),
    ]), state=state_factory(), tools=[MappingTool()])
    assert agent.chat_result("inspect").status == "completed"
    assert "42" in tool_reply(agent, "mapping-call")
    assert_complete_tool_chain(agent.messages)


def test_post_interrupt_keeps_completed_write_and_cancels_remaining(state_factory, tmp_path):
    state = state_factory()

    def interrupt(*a, **k):
        raise KeyboardInterrupt

    agent = Agent(ScriptedLLM([LLMResponse(tool_calls=[
        ToolCall("written", "write_file", {"file_path": "saved.txt", "content": "saved"}),
        ToolCall("not-started", "write_file", {"file_path": "absent.txt", "content": "absent"}),
    ])]), tools=[WriteFileTool(state)], state=state,
        hooks=SimpleNamespace(run_pre=lambda *a, **k: None, run_post=interrupt))
    result = agent.chat_result("write")
    assert result.status == "cancelled"
    assert result.rounds == 1
    assert (tmp_path / "saved.txt").read_text() == "saved"
    assert not (tmp_path / "absent.txt").exists()
    assert "Wrote" in tool_reply(agent, "written")
    assert any(e["call_id"] == "not-started" and e["status"] == "cancelled" for e in result.tool_errors)
    assert_complete_tool_chain(agent.messages)


def test_outcome_record_failure_does_not_leave_dangling_tool_calls(state_factory, tmp_path, monkeypatch):
    state = state_factory()
    agent = Agent(ScriptedLLM([LLMResponse(tool_calls=[
        ToolCall("first", "write_file", {"file_path": "first.txt", "content": "one"}),
        ToolCall("second", "write_file", {"file_path": "second.txt", "content": "two"}),
    ])]), tools=[WriteFileTool(state)], state=state)

    def fail(*args, **kwargs):
        raise OSError("offline outcome storage failure")

    monkeypatch.setattr(state, "record_outcome", fail)
    result = agent.chat_result("write")
    assert result.status == "failed"
    assert result.rounds == 1
    assert (tmp_path / "first.txt").read_text() == "one"
    assert_complete_tool_chain(agent.messages)
    assert "Wrote" in tool_reply(agent, "first")
    if (tmp_path / "second.txt").exists():
        assert "Wrote" in tool_reply(agent, "second")


def test_validation_exception_after_write_preserves_batch_results(state_factory, tmp_path):
    class BrokenValidator(Tool):
        name = "broken_validator"
        description = "Local validator failure fixture"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}

        def validate_arguments(self, arguments):
            raise RuntimeError("offline validator failure")

        def execute(self):
            raise AssertionError("validation must prevent execution")

    state = state_factory()
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[
            ToolCall("written-first", "write_file", {"file_path": "valid-first.txt", "content": "saved"}),
            ToolCall("bad-validator", "broken_validator", {}),
        ]), LLMResponse(content="recovered"),
    ]), tools=[WriteFileTool(state), BrokenValidator()], state=state)
    result = agent.chat_result("write")
    assert result.status == "completed"
    assert (tmp_path / "valid-first.txt").read_text() == "saved"
    assert "Wrote" in tool_reply(agent, "written-first")
    assert result.tool_errors[0]["call_id"] == "bad-validator"
    assert result.tool_errors[0]["stage"] == "validate"
    assert_complete_tool_chain(agent.messages)


def test_interrupted_external_effect_is_unknown_not_cancelled(state_factory, tmp_path):
    target = tmp_path / "external-effect.txt"

    class ExternalTool(Tool):
        name = "external_change"
        description = "Ambiguous external execution fixture"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}

        def execute(self):
            target.write_text("side effect happened")
            raise KeyboardInterrupt

    agent = Agent(ScriptedLLM([LLMResponse(tool_calls=[ToolCall("external", "external_change", {})])]),
                  state=state_factory(), tools=[ExternalTool()])
    result = agent.chat_result("run")
    assert result.status == "cancelled" and target.exists()
    assert result.rounds == 1
    assert any(e["call_id"] == "external" and e["status"] == "unknown" for e in result.tool_errors)
    assert "unknown" in tool_reply(agent, "external").lower()
    assert_complete_tool_chain(agent.messages)


@pytest.mark.parametrize("rounds", [0, -1, True, 1.5])
def test_invalid_round_limit_is_rejected(rounds, state_factory):
    with pytest.raises((ValueError, TypeError)):
        Agent(ScriptedLLM([]), state=state_factory(), max_rounds=rounds)


def test_json_keeps_unicode_and_body_from_tool_rounds(state_factory, capsys):
    llm = ScriptedLLM([
        LLMResponse(content="第一阶段。", tool_calls=[ToolCall("noop", "missing", {})]),
        LLMResponse(content="完成。"),
    ])
    agent = Agent(llm, tools=[], state=state_factory())
    assert cli._run_once(agent, "继续", json_output=True) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert "第一阶段。" in payload["text"] and "完成。" in payload["text"]
    assert payload["tool_errors"][0]["call_id"] == "noop"
    assert payload["rounds"] == 2


def test_json_mode_with_default_permissions_has_no_plain_stdout(state_factory, capsys):
    agent = Agent(ScriptedLLM([LLMResponse(content="done")]), tools=[], state=state_factory(), permission=Permission())
    assert cli._run_once(agent, "inspect", json_output=True) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "done"


def test_text_mode_does_not_duplicate_streamed_body(state_factory, capsys):
    agent = Agent(ScriptedLLM([LLMResponse(content="unique-response")]), tools=[], state=state_factory())
    assert cli._run_once(agent, "inspect") == 0
    assert capsys.readouterr().out.count("unique-response") == 1


def test_budget_refusal_counts_no_main_model_round(state_factory):
    llm = ScriptedLLM([])
    agent = Agent(llm, tools=[], state=state_factory(), max_context_tokens=1)
    result = agent.chat_result("cannot fit")
    assert result.status == "failed" and result.rounds == 0


@pytest.mark.parametrize("mode, code", [("completed", 0), ("max_rounds", 2), ("cancelled", 130), ("failed", 1)])
def test_real_module_entrypoint_has_clean_json_and_exit_code(tmp_path, mode, code):
    script = tmp_path / "offline_entrypoint.py"
    script.write_text('''
import runpy
import sys
from corecoder import cli
from corecoder.config import Config
from corecoder.agent import Agent
from corecoder.llm import LLMResponse, ToolCall
mode = sys.argv[1]
class OfflineLLM:
    model = "offline"
    total_prompt_tokens = 0
    total_completion_tokens = 0
    def __init__(self, **kwargs): pass
    def chat(self, messages, **kwargs):
        if mode == "failed": raise RuntimeError("offline error")
        if mode == "cancelled": raise KeyboardInterrupt
        if mode == "max_rounds": return LLMResponse(tool_calls=[ToolCall("probe", "missing", {})])
        return LLMResponse(content="中文完成")
Config.from_env = classmethod(lambda cls: Config(api_key="offline", model="offline"))
cli.LLM = OfflineLLM
cli.Agent = lambda **kwargs: Agent(max_rounds=1, **kwargs)
cli.load_mcp_tools = list
cli.load_hooks = lambda: None
sys.argv = ["corecoder", "--yes", "--json", "-p", "inspect"]
runpy.run_module("corecoder", run_name="__main__")
''', encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-X", "utf8", str(script), mode], cwd=tmp_path,
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)
    assert result.returncode == code, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == mode and payload["exit_code"] == code
    if mode == "completed":
        assert payload["text"] == "中文完成"


def test_repeated_usage_chunks_are_cumulative_not_additive():
    llm = LLM("model-a", api_key="offline")
    llm._drain(iter([
        stream_chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2)),
        stream_chunk(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3)),
    ]), None, None)
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (12, 3)
    assert len(llm.usage_ledger) == 1
    entry = llm.usage_ledger[0]
    with pytest.raises((FrozenInstanceError, AttributeError)):
        entry.model = "tampered"
    assert isinstance(llm.usage_ledger, tuple)


def test_retry_accounts_for_failed_attempt_usage(monkeypatch):
    llm = LLM("model-a", api_key="offline")
    attempts = []
    monkeypatch.setattr("corecoder.llm.time.sleep", lambda *a: None)

    def failed_stream():
        yield stream_chunk(usage=SimpleNamespace(prompt_tokens=11, completion_tokens=2))
        raise APIConnectionError(request=SimpleNamespace())

    def open_stream(params):
        attempts.append(dict(params))
        return failed_stream() if len(attempts) == 1 else iter([
            stream_chunk(usage=SimpleNamespace(prompt_tokens=13, completion_tokens=3)),
        ])

    monkeypatch.setattr(llm, "_call_with_retry", open_stream)
    llm.chat([{"role": "user", "content": "inspect"}])
    assert len(llm.usage_ledger) == 2
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (24, 5)
    assert len({e.attempt_id for e in llm.usage_ledger}) == 2


def test_custom_endpoint_does_not_inherit_official_price_or_credentials(monkeypatch):
    monkeypatch.setitem(sys.modules["corecoder.llm"]._PRICING, "shared-name", (2.0, 8.0))
    secret = "offline-endpoint-secret"
    llm = LLM("shared-name", api_key="offline", base_url=f"https://user:{secret}@offline.test/v1?api_key={secret}")
    llm._drain(iter([stream_chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))]), None, None)
    assert llm.estimated_cost is None
    assert llm.cost_summary.unknown_requests == 1
    assert secret not in repr(llm.usage_ledger)


def test_shared_llm_run_usage_is_isolated_between_concurrent_agents(monkeypatch, state_factory):
    llm = LLM("model-a", api_key="offline")
    barrier = threading.Barrier(2)

    def open_stream(params):
        count = int(params["messages"][-1]["content"])

        def stream():
            yield stream_chunk(usage=SimpleNamespace(prompt_tokens=count, completion_tokens=1))
            barrier.wait(timeout=5)
            chunk = stream_chunk()
            chunk.choices[0].delta.content = "done"
            yield chunk

        return stream()

    monkeypatch.setattr(llm, "_call_with_retry", open_stream)
    agents = [Agent(llm, tools=[], state=state_factory()) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(agent.chat_result, str(count)) for agent, count in zip(agents, [11, 22], strict=True)]
        results = [future.result(timeout=10) for future in futures]
    assert [r.status for r in results] == ["completed", "completed"]
    assert [r.usage["prompt_tokens"] for r in results] == [11, 22]
    assert [r.usage["completion_tokens"] for r in results] == [1, 1]
    assert llm.total_prompt_tokens == 33


def test_callback_failure_still_records_request_usage(monkeypatch):
    llm = LLM("model-a", api_key="offline")
    text_chunk = stream_chunk()
    text_chunk.choices[0].delta.content = "visible"
    monkeypatch.setattr(llm, "_call_with_retry", lambda p: iter([
        stream_chunk(usage=SimpleNamespace(prompt_tokens=9, completion_tokens=2)),
        text_chunk,
    ]))

    def fail(token):
        raise RuntimeError("offline display failure")

    with pytest.raises(RuntimeError, match="display failure"):
        llm.chat([{"role": "user", "content": "inspect"}], on_token=fail)
    assert len(llm.usage_ledger) == 1
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (9, 2)


def test_create_fallback_and_stream_attempts_have_unique_ledger_entries(monkeypatch):
    try:
        import httpx
    except ModuleNotFoundError:
        import httpx2 as httpx

    llm = LLM("model-a", api_key="offline")
    request = httpx.Request("POST", "https://offline.test/v1/chat/completions")
    calls = []

    def create(**params):
        calls.append(dict(params))
        if len(calls) == 1:
            raise BadRequestError("unsupported 'max_tokens'", response=httpx.Response(400, request=request), body=None)
        return iter([stream_chunk(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=1))])

    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    llm.chat([{"role": "user", "content": "inspect"}])
    assert len(calls) == 2 and "max_completion_tokens" in calls[1]
    assert len(llm.usage_ledger) == 2
    assert len({e.attempt_id for e in llm.usage_ledger}) == 2
    assert llm.cost_summary.missing_usage_requests == 1
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (5, 1)


def test_nested_usage_scope_includes_child_and_summary_once():
    llm = LLM("model-a", api_key="offline")

    def consume(prompt):
        llm._drain(iter([stream_chunk(usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=1))]), None, None)

    with llm.run_scope() as parent:
        consume(7)
        with llm.run_scope() as child:
            consume(13)
        consume(3)
    assert child.as_dict()["prompt_tokens"] == 13
    assert parent.as_dict()["prompt_tokens"] == 23
    assert len(parent.as_dict()["request_ids"]) == 3
    assert len(llm.usage_ledger) == 3


def test_parent_run_usage_includes_actual_child_requests_once(monkeypatch, state_factory):
    llm = LLM("model-a", api_key="offline")
    streams = iter([
        [stream_chunk(arguments='{"task": "child task"}', name="agent"),
         stream_chunk(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=1))],
        [stream_chunk(usage=SimpleNamespace(prompt_tokens=13, completion_tokens=2))],
        [stream_chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1))],
    ])
    monkeypatch.setattr(llm, "_call_with_retry", lambda p: iter(next(streams)))
    agent = Agent(llm, tools=[AgentTool()], state=state_factory())
    result = agent.chat_result("parent task")
    assert result.status == "completed" and result.rounds == 2
    assert result.usage["prompt_tokens"] == 23 and result.usage["completion_tokens"] == 4
    assert len(result.usage["request_ids"]) == len(llm.usage_ledger) == 3
    assert_complete_tool_chain(agent.messages)
    sid = session.save_session(agent.messages, "model-a", "empty-final")
    assert session.load_session(sid) == (agent.messages, "model-a")


@pytest.mark.parametrize("role", [[], {}])
def test_session_bad_role_is_rejected_without_loader_crash(role, caplog):
    session.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = session.SESSIONS_DIR / "bad-role.json"
    path.write_text(json.dumps({"model": "offline", "messages": [{"role": role, "content": "secret"}]}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert session.load_session("bad-role") is None
        assert session.list_sessions() == []
    assert "messages[0].role" in caplog.text and "secret" not in caplog.text


def test_usage_details_and_replay_are_preserved_once():
    llm = LLM("model-a", api_key="offline")
    llm._drain(iter([stream_chunk(usage=SimpleNamespace(
        prompt_tokens=50, completion_tokens=10,
        prompt_tokens_details=SimpleNamespace(cached_tokens=20),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
    ))]), None, None)
    entry = llm.usage_ledger[0]
    assert entry.cached_tokens == 20 and entry.reasoning_tokens == 4
    assert entry.rate_sampled_at
    llm._ledger.append(entry)
    assert len(llm.usage_ledger) == 1 and llm.total_prompt_tokens == 50


def test_web_restores_legacy_usage_as_unknown_price(tmp_path, monkeypatch):
    from web_backend.service import Service, WebSession

    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: Config(api_key="offline", model="model-a")))
    service = Service(tmp_path, tmp_path / "web-state", llm_factory=lambda: LLM("model-a", api_key="offline"), extensions=False)
    restored = WebSession(service, {"id": "legacy", "prompt_tokens": 15, "completion_tokens": 3})
    assert restored.agent.llm.total_prompt_tokens == 15
    assert restored.agent.llm.total_completion_tokens == 3
    assert restored.agent.llm.estimated_cost is None
    assert restored.agent.llm.cost_summary.unknown_requests == 1


@pytest.mark.parametrize("phase", ["display", "pre", "permission"])
def test_interruption_before_execute_is_cancelled(phase, state_factory):
    tool = NestedTool()

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    hooks = SimpleNamespace(run_pre=interrupt if phase == "pre" else lambda *a, **k: None,
                            run_post=lambda *a, **k: None)
    permission = SimpleNamespace(check=interrupt if phase == "permission" else lambda *a: None)
    agent = Agent(ScriptedLLM([LLMResponse(tool_calls=[
        ToolCall("before", tool.name, {"items": []}),
    ])]), tools=[tool], state=state_factory(), hooks=hooks, permission=permission)
    result = agent.chat_result("inspect", on_tool=interrupt if phase == "display" else None)
    assert result.status == "cancelled" and tool.calls == []
    assert result.tool_errors[0]["status"] == "cancelled"
    assert_complete_tool_chain(agent.messages)


@pytest.mark.parametrize("phase", ["pre", "permission"])
def test_hook_or_permission_exception_has_correct_stage(phase, state_factory):
    tool = NestedTool()

    def fail(*args, **kwargs):
        raise RuntimeError("offline stage failure")

    hooks = SimpleNamespace(run_pre=fail if phase == "pre" else lambda *a, **k: None,
                            run_post=lambda *a, **k: None)
    permission = SimpleNamespace(check=fail if phase == "permission" else lambda *a: None)
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("stage", tool.name, {"items": []})]),
        LLMResponse(content="recovered"),
    ]), tools=[tool], state=state_factory(), hooks=hooks, permission=permission)
    result = agent.chat_result("inspect")
    assert result.status == "completed" and tool.calls == []
    assert result.tool_errors[0]["stage"] == phase


@pytest.mark.parametrize("version", [True, 1.0, 2.0, "2"])
def test_session_version_requires_an_integer(version):
    session.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = session.SESSIONS_DIR / "version.json"
    path.write_text(json.dumps({"schema_version": version, "id": "version", "model": "offline",
                                "messages": [{"role": "user", "content": "hello"}]}), encoding="utf-8")
    assert session.load_session("version") is None
    assert session.list_sessions() == []


@pytest.mark.parametrize("messages", [
    [{"role": "assistant", "content": None}],
    [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "read", "arguments": "{}"}},
    ]}],
    [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "read", "arguments": "{}"}},
        {"id": "a", "type": "function", "function": {"name": "read", "arguments": "{}"}},
    ]}, {"role": "tool", "tool_call_id": "a", "content": "done"}],
])
def test_save_rejects_invalid_chain_without_overwriting(messages):
    session.save_session([{"role": "user", "content": "original"}], "offline", "valid")
    path = session.SESSIONS_DIR / "valid.json"
    original = path.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        session.save_session(messages, "offline", "valid")
    assert path.read_bytes() == original


def test_non_utf8_archive_is_rejected_with_diagnostic(caplog):
    session.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = session.SESSIONS_DIR / "encoding.json"
    path.write_bytes(b"\xff\xfe\x00")
    with caplog.at_level(logging.WARNING):
        assert session.load_session("encoding") is None
        assert session.list_sessions() == []
    assert str(path) in caplog.text


def test_session_fsync_failure_leaves_old_file_and_cleans_own_temp(monkeypatch):
    session.save_session([{"role": "user", "content": "original"}], "offline", "durable")
    path = session.SESSIONS_DIR / "durable.json"
    original = path.read_bytes()
    siblings = set(session.SESSIONS_DIR.iterdir())

    def fail(fd):
        raise OSError("offline fsync failure")

    with monkeypatch.context() as scoped:
        scoped.setattr("corecoder.session.os.fsync", fail)
        with pytest.raises(OSError, match="fsync"):
            session.save_session([{"role": "user", "content": "new"}], "offline", "durable")
    assert path.read_bytes() == original
    assert set(session.SESSIONS_DIR.iterdir()) == siblings


def test_concurrent_session_save_keeps_one_complete_document():
    errors = []
    gate = threading.Barrier(4)

    def save(index):
        try:
            gate.wait(timeout=5)
            session.save_session([{"role": "user", "content": str(index) * 20_000}], "offline", "shared")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(t.is_alive() for t in threads) and errors == []
    loaded = session.load_session("shared")
    assert loaded and loaded[0][0]["content"] in {str(i) * 20_000 for i in range(4)}
    assert len(list(session.SESSIONS_DIR.iterdir())) == 1


def test_config_diagnostics_do_not_echo_credentials(tmp_path, caplog):
    secret = "offline-diagnostic-secret"
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"bad": {"command": "offline", "env": {"API_KEY": [secret]}}}}))
    with caplog.at_level(logging.WARNING):
        assert mcp.load_mcp_tools(path) == []
    assert secret not in caplog.text and "env" in caplog.text
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"PreToolUse": [{"command": {"api_key": secret}}]}))
    with caplog.at_level(logging.WARNING):
        assert not load_hooks(hooks)
    assert secret not in caplog.text


MCP_BOUNDARY_SERVER = r'''
import json
import sys
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
mode = sys.argv[1]
page = 0
for line in sys.stdin:
    req = json.loads(line)
    if "id" not in req:
        continue
    if req["method"] == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "boundary", "version": "1"}}
        if mode == "no-tools": result["capabilities"] = {}
        if mode == "bad-capability": result["capabilities"]["tools"] = True
        if mode == "bad-info": result["serverInfo"]["version"] = 42
        if mode == "bad-init": result = []
    elif req["method"] == "tools/list":
        if mode == "no-tools": sys.exit(3)
        tool = {"name": "echo", "inputSchema": {"type": "object", "properties": {}}}
        result = {"tools": [tool]}
        if mode == "empty-continue" and page == 0:
            result = {"tools": [], "nextCursor": "opaque:next"}
        elif mode == "empty-continue" and req.get("params", {}).get("cursor") != "opaque:next":
            sys.exit(4)
        if mode == "null-cursor": result["nextCursor"] = None
        if mode == "empty-cursor": result["nextCursor"] = ""
        if mode == "bad-schema": tool["inputSchema"] = {"type": 42}
        if mode == "non-object-schema": tool["inputSchema"] = {"type": "integer"}
        page += 1
    else:
        result = {"content": [{"type": "text", "text": "OK"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
'''


@pytest.fixture
def boundary_server(tmp_path, monkeypatch):
    path = tmp_path / "boundary_server.py"
    path.write_text(MCP_BOUNDARY_SERVER, encoding="utf-8")
    monkeypatch.setattr(mcp, "INIT_TIMEOUT", 2)
    return path


@pytest.mark.parametrize("mode, names", [("no-tools", []), ("empty-continue", ["mcp__boundary__echo"])])
def test_mcp_capability_and_empty_page_follow_protocol(boundary_server, mode, names):
    client = mcp.MCPClient("boundary", sys.executable, [str(boundary_server), mode])
    try:
        assert [t.name for t in client.tools] == names
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["bad-capability", "bad-info", "bad-init", "null-cursor",
                                 "empty-cursor", "bad-schema", "non-object-schema"])
def test_mcp_invalid_discovery_reaps_subprocess(boundary_server, monkeypatch, mode):
    processes = []
    popen = mcp.subprocess.Popen

    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(mcp.subprocess, "Popen", track)
    with pytest.raises(mcp.MCPError):
        mcp.MCPClient("boundary", sys.executable, [str(boundary_server), mode])
    assert processes and all(p.poll() is not None for p in processes)
