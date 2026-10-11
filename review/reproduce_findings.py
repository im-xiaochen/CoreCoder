"""Offline review probes for CoreCoder c2d8417; no live LLM or user stores.

Run from the repository with: python review/reproduce_findings.py
CONFIRMED means the current implementation reproduced the reviewed problem.
These are diagnostic probes, not regression tests specifying desired behavior.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corecoder import Agent, checkpoints, session
from corecoder.cli import _ask_permission, _run_once
from corecoder.context import ContextManager, estimate_tokens
from corecoder.demo import ScriptedLLM
from corecoder.hooks import load_hooks
from corecoder.llm import LLM, LLMResponse, ToolCall
from corecoder.mcp import MCPClient, load_mcp_tools
from corecoder.permissions import Permission
from corecoder.tools.bash import BashTool, get_tracked_cwd, set_tracked_cwd
from corecoder.tools.read import ReadFileTool
from corecoder.tools.write import WriteFileTool


def check(label, condition, detail):
    assert condition, f"NOT REPRODUCED: {label}: {detail}"
    print(f"CONFIRMED {label}: {detail}")


def run(root):
    checkpoints.CHECKPOINTS_FILE = root / "checkpoints.json"
    checkpoints.clear()
    session.SESSIONS_DIR = root / "sessions"

    wrong_parent_file = root / "wrong-parent.txt"
    a_asked = []

    def ask_a(name, arguments):
        a_asked.append(name)
        return "once" if name == "agent" else "deny"

    a = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("spawn", "agent", {"task": "do subtask"})]),
        LLMResponse(content="A done"),
    ]), permission=Permission(ask=ask_a))
    b = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("write", "write_file", {
            "file_path": str(wrong_parent_file), "content": "B approval used"})]),
        LLMResponse(content="B subtask done"),
    ]), permission=Permission(allow_all=True))
    shared = a._tool_by_name["agent"]
    a._todo.execute([{"content": "A_PRIVATE_TASK", "status": "pending"}])
    a.chat("go")
    check("R01 shared agent state", shared._parent_agent is b
          and "A_PRIVATE_TASK" in b._full_messages()[0]["content"]
          and wrong_parent_file.exists() and a_asked == ["agent"],
          "A's sub-agent uses B's LLM/approval; A's write denial is bypassed; checklist shared")
    a.reset()

    previous_dir, previous_tracked = os.getcwd(), get_tracked_cwd()
    work = root / "work"
    (work / "sub").mkdir(parents=True)
    (work / "marker.txt").write_text("ROOT", encoding="utf-8")
    (work / "sub" / "marker.txt").write_text("SUB", encoding="utf-8")
    try:
        os.chdir(work)
        set_tracked_cwd(None)
        result = BashTool().execute("cd sub")
        read = ReadFileTool().execute("marker.txt")
        check("R02 cwd mismatch", get_tracked_cwd() == str(work / "sub") and "ROOT" in read,
              f"bash cd={result!r}; relative read still returns ROOT")
    finally:
        os.chdir(previous_dir)
        set_tracked_cwd(previous_tracked)

    target = root / "undo.txt"
    target.write_text("original", encoding="utf-8")
    WriteFileTool().execute(str(target), "agent change")
    target.write_text("later human change", encoding="utf-8")
    checkpoints.undo()
    check("R03 undo overwrites later changes", target.read_text() == "original",
          "later human change was overwritten without a conflict check")

    checkpoints.clear()
    WriteFileTool().execute(str(target), "agent change")
    try:
        with patch.object(Path, "write_bytes", side_effect=OSError("simulated write failure")):
            checkpoints.undo()
    except OSError:
        pass
    check("R04 undo failure loses in-memory entry", checkpoints.pending() == 0,
          "restore failed, but pending() is now 0")
    checkpoints.clear()

    calls = [ToolCall(str(i), "read_file", {"file_path": f"file{i}.txt"}) for i in range(6)]
    messages = [LLMResponse(tool_calls=calls).message]
    messages.extend({"role": "tool", "tool_call_id": str(i), "content": "x" * 1000}
                    for i in range(6))
    cm = ContextManager(max_tokens=100)
    before = estimate_tokens(messages)
    compressed = cm.maybe_compress(messages)
    after = estimate_tokens(messages)
    check("R05 compression does not bound context", compressed and after >= before and after > 100,
          f"compression reported success: {before} -> {after} estimated tokens; limit=100")
    flat = cm._flatten([LLMResponse(tool_calls=[ToolCall("t", "edit_file", {
        "file_path": "only-in-args.py", "new_string": "UNIQUE_DECISION"})]).message])
    check("R06 summary omits tool arguments", "UNIQUE_DECISION" not in flat,
          "tool name/path/arguments with no assistant prose are absent from summary input")

    llm = LLM.__new__(LLM)
    llm.total_prompt_tokens = llm.total_completion_tokens = 0
    malformed_chunk = SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(
        content=None, tool_calls=[SimpleNamespace(index=0, id="bad", function=SimpleNamespace(
            name="write_file", arguments="[]"))]))])
    malformed = llm._drain(iter([malformed_chunk]), None, None)
    agent = Agent(ScriptedLLM([malformed]), tools=[WriteFileTool()],
                  permission=Permission(ask=_ask_permission))
    raised = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            agent.chat("go")
    except AttributeError as exc:
        raised = type(exc).__name__
    missing_reply = not any(m.get("role") == "tool" for m in agent.messages)
    check("R07 malformed args poison history", raised == "AttributeError" and missing_reply,
          "JSON [] reaches permission formatting; raises AttributeError and leaves pending tool call")

    llm.model = "gpt-4o"
    llm.total_prompt_tokens, llm.total_completion_tokens = 1_000_000, 0
    cost_before = llm.estimated_cost
    llm.model = "gpt-4o-mini"
    cost_after = llm.estimated_cost
    check("R08 model switch reprices history", cost_before != cost_after,
          f"same past token usage: ${cost_before} -> ${cost_after}, without another request")

    short_agent = Agent(ScriptedLLM([LLMResponse(tool_calls=[
        ToolCall("r", "read_file", {"file_path": str(target)})])]),
        tools=[ReadFileTool()], max_rounds=1)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        _run_once(short_agent, "go")
    check("R09 one-shot hides exhaustion", "maximum" not in output.getvalue(),
          "max_rounds reached, _run_once returns normally and omits the exhaustion message")

    for label, loader, data in [
        ("hooks", load_hooks, {"PreToolUse": [42]}),
        ("mcp", load_mcp_tools, {"mcpServers": []}),
    ]:
        # A non-empty invalid mcpServers list triggers .items(), without starting a server.
        if label == "mcp":
            data = {"mcpServers": [42]}
        cfg = root / f"bad-{label}.json"
        cfg.write_text(json.dumps(data), encoding="utf-8")
        error = None
        try:
            loader(cfg)
        except AttributeError as exc:
            error = type(exc).__name__
        check(f"R10 invalid {label} structure", error == "AttributeError",
              f"valid JSON of invalid shape crashes loader with {error}")
    session.SESSIONS_DIR.mkdir()
    (session.SESSIONS_DIR / "bad.json").write_text("[]", encoding="utf-8")
    error = None
    try:
        session.load_session("bad")
    except TypeError as exc:
        error = type(exc).__name__
    check("R10 invalid session structure", error == "TypeError",
          "load_session('bad') with [] crashes instead of returning None")

    server = root / "paginated_server.py"
    server.write_text('''import json,sys
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", newline="\\n")
for line in sys.stdin:
    req=json.loads(line)
    if "id" not in req: continue
    if req["method"] == "initialize":
        out={"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"1"}}
    else:
        name="second" if req.get("params",{}).get("cursor") else "first"
        out={"tools":[{"name":name,"inputSchema":{"type":"object","properties":{}}}]}
        if name == "first": out["nextCursor"]="page2"
    print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":out}), flush=True)
''', encoding="utf-8")
    client = MCPClient("paged", sys.executable, [str(server)])
    try:
        names = [tool.name for tool in client.tools]
        check("R11 MCP pagination ignored", names == ["mcp__paged__first"],
              f"server advertises nextCursor=page2, registered tools={names}")
    finally:
        client.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="corecoder-review-") as scratch:
        run(Path(scratch))
    print("All offline review probes reproduced the current behavior.")
