"""Independent integration acceptance beyond the original R01-R06 examples."""

import copy
import json
from pathlib import Path
from typing import ClassVar

import pytest

from corecoder.agent import Agent
from corecoder.context import ContextManager, estimate_request_tokens
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse, ToolCall
from corecoder.permissions import Permission
from corecoder.tools.agent import AgentTool
from corecoder.tools.base import Tool
from corecoder.tools.read import ReadFileTool
from designDocument.tests.support import RecordingLLM, assert_complete_tool_chain, drive_repl


def test_bound_agent_tool_cannot_be_stolen():
    tool = AgentTool()
    original = Agent(ScriptedLLM([]), tools=[tool])
    with pytest.raises(ValueError):
        Agent(ScriptedLLM([]), tools=[tool])
    assert tool._parent_agent is original


def test_custom_mutable_tool_without_clone_cannot_cross_agents():
    class CounterTool(Tool):
        name = "counter"
        description = "test mutable custom tool"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}, "required": []}

        def __init__(self):
            self.values = []

        def execute(self):
            self.values.append(1)
            return str(len(self.values))

    tool = CounterTool()
    Agent(ScriptedLLM([]), tools=[tool])
    with pytest.raises(ValueError, match="clone|state|owner|bound|reuse"):
        Agent(ScriptedLLM([]), tools=[tool])


def test_child_preserves_custom_read_guard(tmp_path, state_factory):
    class GuardedRead(ReadFileTool):
        def execute(self, file_path, **kwargs):
            return "Error: custom read guard denied access"

    state = state_factory()
    target = tmp_path / "protected.txt"
    target.write_text("private fixture data", encoding="utf-8")
    llm = RecordingLLM([
        LLMResponse(tool_calls=[ToolCall("spawn", "agent", {"task": "read protected file"})]),
        LLMResponse(tool_calls=[ToolCall("read", "read_file", {"file_path": str(target)})]),
        LLMResponse(content="child done"), LLMResponse(content="parent done"),
    ])
    Agent(llm, tools=[GuardedRead(state=state), AgentTool()], state=state).chat("go")
    requests = json.dumps(llm.requests)
    assert "private fixture data" not in requests
    assert "custom read guard denied access" in requests


def test_reverse_authority_child_keeps_allowing_parent(tmp_path, state_factory):
    target = tmp_path / "allowed.txt"
    parent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("spawn", "agent", {"task": "write"})]),
        LLMResponse(tool_calls=[ToolCall("write", "write_file", {
            "file_path": str(target), "content": "parent authority",
        })]),
        LLMResponse(content="child finished"), LLMResponse(content="parent finished"),
    ]), permission=Permission(allow_all=True), state=state_factory())
    other = Agent(ScriptedLLM([]), permission=Permission())
    parent.chat("go")
    assert target.read_text(encoding="utf-8") == "parent authority"
    assert other.messages == []


def test_child_cannot_gain_tools_omitted_by_parent(tmp_path, state_factory):
    target = tmp_path / "unavailable.txt"
    parent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("spawn", "agent", {"task": "try writing"})]),
        LLMResponse(tool_calls=[ToolCall("write", "write_file", {
            "file_path": str(target), "content": "not allowed",
        })]),
        LLMResponse(content="child done"), LLMResponse(content="parent done"),
    ]), tools=[AgentTool()], permission=Permission(allow_all=True), state=state_factory())
    parent.chat("go")
    assert not target.exists()


def test_state_fork_isolates_mutable_values_and_cwd(tmp_path, state_factory):
    parent = state_factory()
    parent.todos.append({"content": "parent", "status": "pending"})
    parent.changed_files.add("parent.py")
    child = parent.fork()
    assert child.workspace_root == parent.workspace_root and child.cwd == parent.cwd
    assert child.session_id != parent.session_id
    assert child.parent_session_id == parent.session_id
    assert child.todos == [] and child.changed_files == set()
    child.cwd = tmp_path / "elsewhere"
    assert parent.cwd == tmp_path


def test_write_then_read_batch_observes_new_bytes(tmp_path, state_factory):
    target = tmp_path / "ordered.txt"
    target.write_text("before", encoding="utf-8")
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[
            ToolCall("write", "write_file", {"file_path": "ordered.txt", "content": "after"}),
            ToolCall("read", "read_file", {"file_path": "ordered.txt"}),
        ]), LLMResponse(content="done"),
    ]), state=state_factory())
    agent.chat("go")
    reply = next(m for m in agent.messages if m.get("tool_call_id") == "read")
    assert "after" in reply["content"] and "before" not in reply["content"]
    assert_complete_tool_chain(agent.messages)


def test_complex_cd_is_tracked_or_explicitly_reported(tmp_path, state_factory):
    sub = tmp_path / "sub"
    sub.mkdir()
    agent = Agent(ScriptedLLM([]), state=state_factory())
    result = agent._tool_by_name["bash"].execute("if true; then cd sub; fi")
    if agent.state.cwd != sub:
        assert agent.state.cwd == tmp_path
        assert "untracked" in result.lower() or "not tracked" in result.lower() or "未跟踪" in result


@pytest.mark.parametrize("command", ["false && cd sub; true", "test -f missing && cd sub; true"])
def test_skipped_conditional_cd_does_not_change_session_cwd(tmp_path, state_factory, command):
    (tmp_path / "sub").mkdir()
    agent = Agent(ScriptedLLM([]), state=state_factory())
    result = agent._tool_by_name["bash"].execute(command)
    assert agent.state.cwd == tmp_path
    assert "untracked" in result.lower() or "not tracked" in result.lower() or "未跟踪" in result


def test_hooks_follow_cwd_between_mutating_batch_calls(tmp_path, state_factory):
    (tmp_path / "sub").mkdir()
    seen = []

    class RecordingHooks:
        def run_pre(self, name, arguments, cwd=None):
            seen.append((name, Path(cwd)))

        def run_post(self, name, arguments, result, cwd=None):
            pass

    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[
            ToolCall("cd", "bash", {"command": "cd sub"}),
            ToolCall("write", "write_file", {"file_path": "inside.txt", "content": "inside"}),
        ]), LLMResponse(content="done"),
    ]), state=state_factory(), hooks=RecordingHooks())
    agent.chat("execute")
    assert seen == [("bash", tmp_path), ("write_file", tmp_path / "sub")]
    assert (tmp_path / "sub" / "inside.txt").read_text() == "inside"


@pytest.mark.parametrize("prefix", ["A" * 10_000, "head\r\n" * 2_000])
def test_character_pagination_can_read_a_single_line_archive(tmp_path, state_factory, prefix):
    target = tmp_path / "single-line.txt"
    target.write_bytes((prefix + "TARGET_PAGE" + "B" * 10_000).encode("utf-8"))
    agent = Agent(ScriptedLLM([]), state=state_factory())
    result = agent._tool_by_name["read_file"].execute(str(target), char_offset=len(prefix), char_limit=11)
    assert "TARGET_PAGE" in result and len(result) < 500


def test_empty_before_image_is_distinct_from_missing_file(tmp_path):
    from corecoder import checkpoints
    from corecoder.tools.write import WriteFileTool

    target = tmp_path / "empty.txt"
    target.write_bytes(b"")
    WriteFileTool().execute(str(target), "filled")
    result = checkpoints.undo()
    assert "Restored" in result
    assert target.exists() and target.read_bytes() == b""


def test_agent_checkpoint_stores_are_separate(tmp_path, state_factory):
    from corecoder import checkpoints

    a = Agent(ScriptedLLM([]), state=state_factory())
    b = Agent(ScriptedLLM([]), state=state_factory())
    a._tool_by_name["write_file"].execute("a.txt", "A")
    b._tool_by_name["write_file"].execute("b.txt", "B")
    assert a.state.checkpoint_store is not b.state.checkpoint_store
    assert a.state.checkpoint_store.pending() == b.state.checkpoint_store.pending() == 1
    assert checkpoints.pending() == 0
    a.state.checkpoint_store.undo()
    assert not (tmp_path / "a.txt").exists() and (tmp_path / "b.txt").read_text() == "B"
    assert b.state.checkpoint_store.pending() == 1


def test_cli_undo_never_selects_the_global_stack(tmp_path, state_factory, monkeypatch):
    from corecoder import checkpoints
    from corecoder.tools.write import WriteFileTool

    compatibility = tmp_path / "other-session.txt"
    WriteFileTool().execute(str(compatibility), "keep")
    agent = Agent(ScriptedLLM([]), state=state_factory())
    agent._tool_by_name["write_file"].execute("owned.txt", "undo this")
    drive_repl(monkeypatch, agent, ["/undo", "quit"])
    assert not (tmp_path / "owned.txt").exists()
    assert compatibility.read_text() == "keep" and checkpoints.pending() == 1


def _offline_cli(monkeypatch):
    from types import SimpleNamespace

    from corecoder import cli
    from corecoder.config import Config

    args = SimpleNamespace(demo=False, model=None, base_url=None, api_key=None,
                           yes=True, prompt=None, resume=None)
    monkeypatch.setattr(cli, "_parse_args", lambda: args)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: Config(api_key="offline", model="offline")))
    monkeypatch.setattr(cli, "LLM", lambda **kwargs: ScriptedLLM([], model="offline"))
    monkeypatch.setattr(cli, "load_mcp_tools", list)
    monkeypatch.setattr(cli, "load_hooks", lambda: None)
    return cli, args


def test_cli_entrypoint_uses_fresh_tools_on_each_start(tmp_path, monkeypatch):
    cli, _ = _offline_cli(monkeypatch)
    monkeypatch.chdir(tmp_path)
    agents = []
    monkeypatch.setattr(cli, "_repl", lambda agent, config: agents.append(agent))
    cli.main()
    cli.main()
    assert agents[0].state.session_id != agents[1].state.session_id
    for name in agents[0]._tool_by_name:
        assert agents[0]._tool_by_name[name] is not agents[1]._tool_by_name[name]
    assert all(a._tool_by_name["agent"]._parent_agent is a for a in agents)


def test_cli_save_resume_keeps_its_checkpoint_store(tmp_path, monkeypatch):
    from corecoder import session

    cli, args = _offline_cli(monkeypatch)
    monkeypatch.chdir(tmp_path)
    agents = []
    actual_repl = cli._repl

    def run_repl(agent, config):
        agents.append(agent)
        if args.resume is None:
            assert "Wrote" in agent._tool_by_name["write_file"].execute("resume.txt", "owned")
            commands = iter(["/save", "quit"])
        else:
            assert agent.state.checkpoint_store.pending() == 1
            commands = iter(["/undo", "quit"])
        monkeypatch.setattr(cli, "pt_prompt", lambda *a, **k: next(commands))
        actual_repl(agent, config)

    monkeypatch.setattr(cli, "_repl", run_repl)
    cli.main()
    saved = session.list_sessions()
    assert len(saved) == 1 and saved[0]["id"] == agents[0].state.session_id
    args.resume = saved[0]["id"]
    cli.main()
    assert agents[1].state.session_id == agents[0].state.session_id
    assert not (tmp_path / "resume.txt").exists()


@pytest.mark.parametrize("answer, expected", [("n", "human"), ("y", "before")])
def test_cli_force_undo_requires_a_targeted_choice(tmp_path, state_factory, monkeypatch, answer, expected):
    target = tmp_path / "force-choice.txt"
    target.write_text("before", encoding="utf-8")
    agent = Agent(ScriptedLLM([]), state=state_factory(), permission=Permission(allow_all=True))
    agent._tool_by_name["write_file"].execute(str(target), "agent")
    target.write_text("human", encoding="utf-8")
    consumed = drive_repl(monkeypatch, agent, ["/undo --force", answer, "quit"])
    assert consumed == ["/undo --force", answer, "quit"]
    assert target.read_text(encoding="utf-8") == expected
    assert agent.state.checkpoint_store.pending() == (1 if answer == "n" else 0)


def test_legacy_checkpoint_requires_explicit_force(tmp_path):
    from corecoder import checkpoints

    target = tmp_path / "legacy.txt"
    target.write_bytes(b"before")
    checkpoints.record(target)
    target.write_bytes(b"unverified after")
    assert "conflict" in checkpoints.undo().lower()
    assert target.read_bytes() == b"unverified after" and checkpoints.pending() == 1
    assert "Restored" in checkpoints.undo(force=True)
    assert target.read_bytes() == b"before"


def test_reload_does_not_follow_a_replacement_symlink(tmp_path):
    from corecoder import checkpoints
    from corecoder.tools.write import WriteFileTool

    target = tmp_path / "original.txt"
    unrelated = tmp_path / "unrelated.txt"
    target.write_bytes(b"before")
    WriteFileTool().execute(str(target), "agent")
    unrelated.write_bytes(b"agent")
    target.unlink()
    try:
        target.symlink_to(unrelated)
    except OSError as error:
        pytest.skip(f"Host does not permit creating test symlinks: {error}")
    checkpoints._stack.clear()
    checkpoints._loaded = False
    assert "conflict" in checkpoints.undo().lower()
    assert target.is_symlink() and unrelated.read_bytes() == b"agent"


def test_journal_persistence_failure_does_not_touch_target(tmp_path, monkeypatch):
    from corecoder import checkpoints
    from corecoder.tools.write import WriteFileTool

    target = tmp_path / "journal.txt"
    target.write_bytes(b"before")
    WriteFileTool().execute(str(target), "after")
    replace = Path.replace

    def fail_journal(path, destination):
        if Path(destination) not in {target, checkpoints.CHECKPOINTS_FILE}:
            raise OSError("journal storage unavailable")
        return replace(path, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail_journal)
        result = checkpoints.undo()
    assert "error" in result.lower() and "journal" in result.lower()
    assert target.read_bytes() == b"after" and checkpoints.pending() == 1
    assert "Restored" in checkpoints.undo()


def test_restart_after_compensated_undo_does_not_overwrite_external_change(tmp_path, monkeypatch):
    from corecoder import checkpoints
    from corecoder.tools.write import WriteFileTool

    target = tmp_path / "restart-conflict.txt"
    target.write_bytes(b"before")
    WriteFileTool().execute(str(target), "after")
    replace = Path.replace

    def deny_commit(path, destination):
        if Path(destination) == checkpoints.CHECKPOINTS_FILE:
            raise OSError("checkpoint unavailable")
        return replace(path, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", deny_commit)
        assert "error" in checkpoints.undo().lower()
    assert target.read_bytes() == b"after"
    target.write_bytes(b"human change after failed undo")
    checkpoints._stack.clear()
    checkpoints._loaded = False
    assert checkpoints.pending() == 1
    assert target.read_bytes() == b"human change after failed undo"
    retry = checkpoints.undo().lower()
    assert "conflict" in retry or "recovery" in retry
    assert target.read_bytes() == b"human change after failed undo"


def test_journal_phase_failure_compensates_or_blocks_following_write(tmp_path, monkeypatch):
    from corecoder import checkpoints
    from corecoder.tools.write import WriteFileTool

    target = tmp_path / "phase-failure.txt"
    target.write_bytes(b"before")
    WriteFileTool().execute(str(target), "after")
    replace = Path.replace
    journal_commits = []

    def fail_phase_update(path, destination):
        if "journal" in Path(destination).name:
            journal_commits.append(str(path))
            if len(journal_commits) == 2:
                raise OSError("journal phase update unavailable")
        return replace(path, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail_phase_update)
        result = checkpoints.undo()
    if len(journal_commits) < 2:
        assert "Restored" in result and target.read_bytes() == b"before"
        return
    assert "error" in result.lower() and checkpoints.pending() == 1
    if target.read_bytes() == b"before":
        blocked = tmp_path / "must-wait.txt"
        assert "error" in WriteFileTool().execute(str(blocked), "unsafe followup").lower()
        assert not blocked.exists()
    else:
        assert target.read_bytes() == b"after"


def test_failed_compensation_blocks_mutation_and_survives_restart(tmp_path, monkeypatch):
    from corecoder import checkpoints
    from corecoder.tools.write import WriteFileTool

    target = tmp_path / "compensation.txt"
    target.write_bytes(b"before")
    WriteFileTool().execute(str(target), "after")
    replace = Path.replace
    replacements = []

    def fail_commit_and_compensation(path, destination):
        if Path(destination) == checkpoints.CHECKPOINTS_FILE:
            raise OSError("commit unavailable")
        if Path(destination) == target:
            replacements.append(str(path))
            if len(replacements) > 1:
                raise OSError("compensation unavailable")
        return replace(path, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail_commit_and_compensation)
        result = checkpoints.undo()
    assert "recovery_required" in result.lower()
    blocked = tmp_path / "blocked.txt"
    assert "error" in WriteFileTool().execute(str(blocked), "must wait").lower()
    assert not blocked.exists() and checkpoints.pending() == 1
    target.write_bytes(b"external recovery conflict")
    checkpoints._stack.clear()
    checkpoints._loaded = False
    assert checkpoints.pending() == 1
    retry = checkpoints.undo().lower()
    assert "conflict" in retry or "recovery" in retry
    assert target.read_bytes() == b"external recovery conflict"
    assert "error" in WriteFileTool().execute(str(blocked), "must still wait").lower()
    assert not blocked.exists()


def test_finalize_storage_failure_keeps_original_or_recoverable_change(tmp_path, monkeypatch):
    from corecoder import checkpoints
    from corecoder.tools.write import WriteFileTool

    target = tmp_path / "finalize.txt"
    target.write_bytes(b"before")
    replace = Path.replace
    commits = []

    def fail_finalize_commit(path, destination):
        if Path(destination) == checkpoints.CHECKPOINTS_FILE:
            commits.append(str(path))
            if len(commits) == 2:
                raise OSError("finalize persistence unavailable")
        return replace(path, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail_finalize_commit)
        result = WriteFileTool().execute(str(target), "after")
    assert "error" in result.lower()
    checkpoints._stack.clear()
    checkpoints._loaded = False
    if target.read_bytes() == b"after":
        assert checkpoints.pending() == 1
        assert "Restored" in checkpoints.undo()
    assert target.read_bytes() == b"before"


def test_archive_failure_refuses_and_preserves_original(tmp_path, monkeypatch):
    original = [
        LLMResponse(tool_calls=[ToolCall("read", "read_file", {"file_path": "large.txt"})]).message,
        {"role": "tool", "tool_call_id": "read", "content": "important" * 20_000},
    ]
    before = copy.deepcopy(original)
    artifacts = tmp_path / "artifacts"
    real_mkdir = Path.mkdir

    def full_disk(path, *args, **kwargs):
        if path == artifacts:
            raise OSError("simulated artifact storage unavailable")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", full_disk)
    result = ContextManager(2048).prepare_request(
        original, system="rules", tools=[], output_reserve=128, artifact_dir=artifacts,
    )
    assert not result.budget_ok
    assert original == before


def test_prepared_output_is_idempotent_and_archive_is_exact(tmp_path):
    body = "first\r\n" + "payload" * 20_000 + "\r\nlast"
    messages = [
        LLMResponse(tool_calls=[ToolCall("read", "read_file", {"file_path": "large.txt"})]).message,
        {"role": "tool", "tool_call_id": "read", "content": body},
    ]
    manager = ContextManager(2048)
    options = {"system": "rules", "tools": [], "output_reserve": 128, "artifact_dir": tmp_path / "artifacts"}
    first = manager.prepare_request(messages, **options)
    assert first.budget_ok and first.artifacts
    assert first.input_tokens == estimate_request_tokens(first.messages, system="rules", tools=[])
    assert Path(first.artifacts[0]).read_bytes() == body.encode("utf-8")
    second = manager.prepare_request(first.messages, **options)
    assert second.budget_ok and second.messages == first.messages and not second.changed
    assert_complete_tool_chain(second.messages)


def test_every_main_round_passes_full_budget_gate(tmp_path, state_factory):
    target = tmp_path / "large.txt"
    target.write_text("large output\n" * 20_000, encoding="utf-8")

    class CheckedLLM(ScriptedLLM):
        max_tokens = 128

        def chat(self, messages, tools=None, **kwargs):
            assert estimate_request_tokens(messages[1:], system=messages[0]["content"], tools=tools) + 128 <= 8192
            assert_complete_tool_chain(messages[1:])
            return super().chat(messages, tools=tools, **kwargs)

    llm = CheckedLLM([
        LLMResponse(tool_calls=[ToolCall("read", "read_file", {"file_path": "large.txt", "limit": 30_000})]),
        LLMResponse(content="done"),
    ])
    agent = Agent(llm, state=state_factory(), max_context_tokens=8192)
    assert agent.chat("read the large output") == "done"
    assert not llm._turns


def test_missing_reply_remains_unknown_in_memory():
    messages = [LLMResponse(tool_calls=[ToolCall("operation-17", "write_file", {
        "file_path": "unconfirmed.txt", "content": "proposal",
    })]).message]
    summary = ContextManager()._get_summary(messages, None)
    assert "unconfirmed.txt" in summary
    assert "unknown" in summary.lower() or "pending" in summary.lower()


@pytest.mark.parametrize("reply, status", [
    ("Permission denied: user refused this call", "refused"),
    ("Blocked by hook: blocked write", "refused"),
    ("Plan mode is on, so this call was refused: plan mode is read-only.", "refused"),
    ("⚠ Blocked: force recursive delete", "refused"),
    ("terminated\n[exit code: -9]", "failed"),
    ("[interrupted]", "cancelled"),
    ("Error: old_string not found", "failed"),
    ("Edited successfully", "succeeded"),
])
def test_execution_evidence_keeps_actual_outcome(reply, status):
    messages = [
        LLMResponse(tool_calls=[ToolCall("operation-18", "edit_file", {
            "file_path": "evidence.py", "old_string": "before", "new_string": "decision",
        })]).message,
        {"role": "tool", "tool_call_id": "operation-18", "content": reply},
    ]
    records = ContextManager.extract_records(messages)
    assert len(records) == 1 and records[0].status == status
    assert records[0].call_id == "operation-18" and "decision" in records[0].arguments


def test_agent_records_real_bash_failure_and_successful_source_read(tmp_path, state_factory):
    (tmp_path / "notes.txt").write_text("refused failed blocked error are ordinary words", encoding="utf-8")
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[
            ToolCall("failure", "bash", {"command": "false"}),
            ToolCall("source", "read_file", {"file_path": "notes.txt"}),
        ]), LLMResponse(content="done"),
    ]), state=state_factory())
    agent.chat("inspect")
    evidence = {record["call_id"]: record for record in agent.state.execution_records}
    assert evidence["failure"]["status"] == "failed"
    assert evidence["source"]["status"] == "succeeded"
    source = next(record for record in agent.state.task_memory.records if record.call_id == "source")
    assert Path(source.target) == tmp_path / "notes.txt"


def test_agent_interruption_records_unknown_and_reset_clears_memory(state_factory):
    class InterruptedTool(Tool):
        name = "interrupt_now"
        description = "Offline interrupt fixture"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}

        def execute(self):
            raise KeyboardInterrupt

    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("interrupted-call", "interrupt_now", {})]),
    ]), state=state_factory(), tools=[InterruptedTool()])
    with pytest.raises(KeyboardInterrupt):
        agent.chat("run")
    assert_complete_tool_chain(agent.messages)
    # R07 distinguishes an entered execution from a call cancelled before start.
    assert agent.state.execution_records[-1]["status"] == "unknown"
    agent.reset()
    assert agent.messages == [] and agent.state.execution_records == [] and agent.state.task_memory is None


def test_interrupted_batch_retains_evidence_for_completed_write(tmp_path, state_factory):
    class InterruptedTool(Tool):
        name = "interrupt_now"
        description = "Offline batch interrupt fixture"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}

        def execute(self):
            raise KeyboardInterrupt

    from corecoder.tools.write import WriteFileTool

    state = state_factory()
    agent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[
            ToolCall("completed-write", "write_file", {"file_path": "completed.txt", "content": "saved"}),
            ToolCall("interrupted-call", "interrupt_now", {}),
        ]),
    ]), state=state, tools=[WriteFileTool(state), InterruptedTool()])
    with pytest.raises(KeyboardInterrupt):
        agent.chat("run")
    assert_complete_tool_chain(agent.messages)
    assert (tmp_path / "completed.txt").read_text() == "saved"
    evidence = {record["call_id"]: record["status"] for record in agent.state.execution_records}
    assert evidence == {"completed-write": "succeeded", "interrupted-call": "unknown"}


def test_pure_conversation_history_can_be_compressed_without_losing_latest_user(tmp_path):
    latest = "最新约束：兼容现有接口，并继续运行集成测试。"
    messages = []
    for i in range(30):
        messages.extend([
            {"role": "user", "content": f"Inspect component {i}"},
            {"role": "assistant", "content": f"Observation {i}: " + "repeated background " * 150},
        ])
    messages.append({"role": "user", "content": latest})
    before = copy.deepcopy(messages)
    result = ContextManager(4096).prepare_request(
        messages, system="rules", tools=[], output_reserve=128, artifact_dir=tmp_path / "artifacts",
    )
    assert result.budget_ok and result.changed
    assert result.input_tokens + result.output_reserve <= result.limit
    assert result.messages[-1]["content"] == latest
    assert messages == before
    histories = [Path(p) for p in result.artifacts if Path(p).name.startswith("history-")]
    assert histories
    archived = json.loads(histories[0].read_text(encoding="utf-8"))
    assert archived == before[:len(archived)]
    assert "Observation 15" in histories[0].read_text(encoding="utf-8")


def test_summary_fallback_preserves_failure_status_and_scrubs_result_secrets():
    secret = "offline-secret-do-not-expose"
    messages = [
        {"role": "user", "content": "必须保留现有接口"},
        LLMResponse(tool_calls=[ToolCall("deny18", "write_file", {"file_path": "unchanged.py"})]).message,
        {"role": "tool", "tool_call_id": "deny18", "content": f'Permission denied: api_key="{secret}"'},
    ]
    summary = ContextManager(256)._get_summary(messages, None)
    assert secret not in summary
    assert "refused" in summary and "deny18" in summary and "unchanged.py" in summary
    assert "必须保留现有接口" in summary


def test_tokenizer_fallback_is_marked_as_estimated():
    def unavailable_tokenizer(text):
        raise ValueError("tokenizer unavailable")

    result = ContextManager(4096, tokenizer=unavailable_tokenizer).prepare_request(
        [{"role": "user", "content": "hello"}], system="rules", output_reserve=128,
    )
    assert result.budget_ok and result.estimated


def test_sensitive_tool_fields_are_redacted_from_summary_memory():
    secret = "fake-secret-for-offline-acceptance"
    message = LLMResponse(tool_calls=[ToolCall("remote17", "mcp__remote", {
        "path": "keep-this-path.py", "credentials": {"api_key": secret}, "operation": "inspect",
    })]).message
    flat = ContextManager._flatten([message])
    summary = ContextManager()._get_summary([message], None)
    assert secret not in flat and secret not in summary
    assert "keep-this-path.py" in flat and "keep-this-path.py" in summary


def test_structured_memory_does_not_store_secret_fields(state_factory):
    from dataclasses import asdict

    secret = "offline-structured-record-secret"
    state = state_factory()
    state.record_outcome("remote-secret", "mcp__remote", "succeeded", {
        "operation": "inspect", "credentials": {"api_key": secret},
    }, json.dumps({"path": "keep.py", "access_token": secret}))
    assert secret not in json.dumps(state.execution_records)
    assert secret not in json.dumps(asdict(state.task_memory))
    assert "keep.py" in json.dumps(asdict(state.task_memory))


def test_summary_requests_also_fit_the_context_window():
    class CheckedSummarizer:
        max_tokens = 128

        def __init__(self):
            self.requests = []

        def chat(self, messages, **kwargs):
            assert estimate_request_tokens(messages[1:], system=messages[0]["content"], tools=[]) + 128 <= 2048
            self.requests.append(copy.deepcopy(messages))
            return LLMResponse(content="Observed with evidence; further tests remain.")

    llm = CheckedSummarizer()
    history = [{"role": "user", "content": f"Observation {i}: " + "background " * 300} for i in range(20)]
    ContextManager(2048)._get_summary(history, llm)
    assert llm.requests
    assert "Observation 0" in json.dumps(llm.requests)
    assert "Observation 19" in json.dumps(llm.requests)
