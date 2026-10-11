"""Reproducible DeepSeek/live and deterministic acceptance checks.

The live checks intentionally use only a small fixture created in a temporary
directory.  Credentials are read by Config.from_env/OpenAI and are never
printed, serialized, or included in exception text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from corecoder.agent import Agent
from corecoder.config import Config
from corecoder.context import ContextManager
from corecoder.llm import LLM, LLMResponse, ToolCall
from corecoder.permissions import Permission
from corecoder.state import SessionState
from corecoder.tools import create_tools
from corecoder.validation import validate_session

# live_2026_10_11 -> validation -> designDocument -> CoreCoder
ROOT = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).resolve().parent
SECRET_KEYS = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^,}\s]+")


def scrub(value):
    text = str(value)
    text = SECRET_KEYS.sub(lambda m: m.group(1) + "=<redacted>", text)
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", text)
    return text


def assertion(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}" if detail else label)


class ScriptedLLM:
    """Small deterministic LLM double for boundaries that do not need a model."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def run_scope(self):
        from contextlib import nullcontext

        return nullcontext(None)

    def chat(self, messages, tools=None, on_token=None, on_reasoning=None):
        response = self.responses.pop(0)
        return response


def offline_checks(root):
    results = []
    state = SessionState(workspace_root=root, session_id="offline-parent")
    child = state.fork()
    assertion("R01 child session id isolated", child.session_id != state.session_id)
    assertion("R01 child todo isolated", child.todos is not state.todos)
    assertion("R01 child cwd inherited", child.cwd == state.cwd)

    target = root / "offline.txt"
    target.write_bytes(b"before\n")
    writer = next(t for t in create_tools(state) if t.name == "write_file")
    assert "Wrote" in writer.execute(file_path="offline.txt", content="after\n")
    target.write_text("external\n", encoding="utf-8")
    conflict = state.checkpoint_store.undo()
    assertion("R03 external conflict protected", "Conflict:" in conflict, conflict)
    # Path.write_text uses platform newline translation on Windows; match the
    # write tool's newline="" byte semantics so the verified after hash is
    # actually reproduced before undo.
    with target.open("w", encoding="utf-8", newline="") as fh:
        fh.write("after\n")
    restored = state.checkpoint_store.undo()
    assertion("R03 verified undo restores bytes", "Restored" in restored, restored)
    assertion("R03 restored exact bytes", target.read_bytes() == b"before\n")

    # Plan mode is exercised with the real Agent/tool pipeline, with a
    # deterministic model response so no provider behavior is assumed.
    plan_target = root / "plan.txt"
    plan_target.write_bytes(b"unchanged\n")
    plan_state = SessionState(workspace_root=root, session_id="offline-plan")
    plan_llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("plan-write", "write_file", {"file_path": "plan.txt", "content": "changed\n"})]),
        LLMResponse(content="plan inspected"),
    ])
    plan_agent = Agent(plan_llm, state=plan_state, permission=Permission(allow_all=True), max_rounds=3)
    plan_agent.plan_mode = True
    plan_result = plan_agent.chat_result("In plan mode, attempt the requested write and report the result.")
    assertion("R01/R07 plan mode run completes", plan_result.status == "completed", plan_result.status)
    assertion("R01/R07 plan mode refuses mutation", any(e["status"] == "refused" for e in plan_result.tool_errors))
    assertion("R01/R07 plan mode has no side effect", plan_target.read_text(encoding="utf-8") == "unchanged\n")

    # Budget and summary evidence are deterministic and provider-independent.
    manager = ContextManager(max_tokens=300)
    history = [{"role": "user", "content": "Keep the constraint KEEP_THIS and finish the remaining task."}]
    prepared = manager.prepare_request(history, system="system", tools=[], output_reserve=100, artifact_dir=root / "artifacts")
    assertion("R05 budget success is within limit", prepared.budget_ok and prepared.input_tokens + prepared.output_reserve <= prepared.limit)
    tiny = ContextManager(max_tokens=40)
    too_large = tiny.prepare_request(
        [{"role": "user", "content": "KEEP_THIS " + ("long context " * 80)}],
        system="system", tools=[], output_reserve=20, artifact_dir=root / "artifacts",
    )
    assertion("R05 impossible budget rejected", not too_large.budget_ok and too_large.reason == "context_budget_exceeded")
    assertion("R05 rejected budget preserves original history", too_large.messages != [] and "KEEP_THIS" in too_large.messages[0]["content"])
    summary_messages = [
        {"role": "user", "content": "Constraint: preserve KEEP_THIS; remaining: verify output."},
        {"role": "assistant", "tool_calls": [{"id": "ok1", "function": {"name": "write_file", "arguments": {"file_path": "fact.txt", "content": "x"}}}]},
        {"role": "tool", "tool_call_id": "ok1", "content": "Wrote fact.txt", "status": "succeeded"},
        {"role": "assistant", "tool_calls": [{"id": "bad1", "function": {"name": "edit_file", "arguments": {"file_path": "missing.txt"}}}]},
        {"role": "tool", "tool_call_id": "bad1", "content": "Error: missing.txt not found", "status": "failed"},
    ]
    summary = ContextManager()._get_summary(summary_messages, None)
    for fact in ("KEEP_THIS", "fact.txt", "FAILURES", "bad1", "missing.txt"):
        assertion("R06 summary preserves concrete evidence", fact in summary, f"missing {fact}: {summary}")
    results.extend(["R01 state isolation", "R03 undo conflict/exact restore", "R07 plan refusal", "R05 strict budget gates", "R06 evidence summary facts/constraints/failures"])
    return results


def live_agent_check(root, config):
    fixture = root / "input.txt"
    fixture.write_bytes(b"LIVE_FIXTURE_ALPHA\n")
    state = SessionState(workspace_root=root, session_id="live-agent", artifact_dir=root / "artifacts")
    llm = LLM(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=0,
        max_tokens=256,
    )
    agent = Agent(llm, state=state, permission=Permission(allow_all=True), max_rounds=6)
    prompt = (
        "First use the agent tool to spawn a child sub-agent. Its task is read-only: "
        "use read_file on input.txt and report the marker LIVE_CHILD_DONE; it must not "
        "write any file. After the child returns, use read_file on input.txt yourself, "
        "then use write_file to create output.txt with exactly LIVE_FIXTURE_BETA on one "
        "line, then use edit_file to replace LIVE_FIXTURE_BETA with LIVE_FIXTURE_GAMMA. "
        "After all tools, answer exactly LIVE_AGENT_DONE. Do not inspect other files."
    )
    result = agent.chat_result(prompt)
    assertion("live Agent status completed", result.status == "completed", result.status)
    assertion("live Agent tool_errors empty", not result.tool_errors, result.tool_errors)
    session_valid = validate_session({"schema_version": 2, "id": "live-agent", "model": config.model, "messages": agent.messages})
    assertion("live Agent message chain validates", session_valid)
    assertion("live Agent made at least one model round", result.rounds >= 1)
    tools_seen = {r["tool_name"] for r in state.execution_records}
    for tool_name in ("agent", "read_file", "write_file", "edit_file"):
        assertion(f"live Agent recorded {tool_name} tool", tool_name in tools_seen, state.execution_records)
    assertion("live child task returned marker", "LIVE_CHILD_DONE" in "\n".join(r["result"] for r in state.execution_records if r["tool_name"] == "agent"), state.execution_records)
    assertion("live Agent edit side effect", (root / "output.txt").read_text(encoding="utf-8").strip() == "LIVE_FIXTURE_GAMMA")
    assertion("live Agent response marker", "LIVE_AGENT_DONE" in result.text, result.text)
    assertion("live usage ledger has entries", len(llm.usage_ledger) >= 1)
    usage_known = [entry.usage_known for entry in llm.usage_ledger]
    assertion("live usage entries report provider usage", all(usage_known), usage_known)
    # Summary semantics are covered by the separately captured, strict
    # one-request verifier. Reuse its persisted evidence here so the final
    # runner does not issue a duplicate summary request or accept fallback
    # text merely because a ledger entry exists.
    summary_path = REPORT / "live_summary_report.json"
    summary_evidence = json.loads(summary_path.read_text(encoding="utf-8"))
    live_summary = summary_evidence.get("summary", "")
    assertion("independent summary evidence is model-sourced", summary_evidence.get("summary_source") == "model", summary_evidence)
    assertion("independent summary evidence passed", summary_evidence.get("result") == "PASS", summary_evidence)
    assertion("independent summary usage is known", all(summary_evidence.get("usage_known", [])), summary_evidence)
    assertion("independent summary preserves pending work", "REMAINING/UNVERIFIED" in live_summary and "no successful verification" in live_summary.lower(), live_summary)
    for fact in ("LIVE_SUMMARY_CONSTRAINT", "live-summary.txt", "live-summary-fail", "missing-live-summary.txt"):
        assertion("independent model summary preserves concrete evidence", fact in live_summary, f"missing {fact}: {live_summary}")
    summary_requests = 0
    usage = {
        "requests": len(llm.usage_ledger),
        "prompt_tokens": sum(e.prompt_tokens for e in llm.usage_ledger),
        "completion_tokens": sum(e.completion_tokens for e in llm.usage_ledger),
        "unknown_requests": llm.cost_summary.unknown_requests,
        "missing_usage_requests": llm.cost_summary.missing_usage_requests,
        "model": config.model,
        "provider": llm.provider,
        "endpoint": llm.endpoint,
        "summary_requests": summary_requests,
        "usage_known": usage_known,
    }
    # External conflict is verified against the checkpoint generated by the
    # real write tool; this action is local and does not issue another request.
    (root / "output.txt").write_bytes(b"EXTERNAL_CHANGE\n")
    conflict = state.checkpoint_store.undo()
    assertion("live write checkpoint protects external conflict", "Conflict:" in conflict, conflict)
    return {
        "status": result.status,
        "rounds": result.rounds,
        "text": result.text,
        "tool_outcomes": [{"tool": r["tool_name"], "status": r["status"]} for r in state.execution_records],
        "summary": live_summary,
        "tool_errors": result.tool_errors,
        "message_chain_valid": session_valid,
        "usage": usage,
        "external_conflict": conflict.splitlines()[0],
    }


def live_cli_check(config, root):
    env = os.environ.copy()
    env.update({"CORECODER_MODEL": config.model, "OPENAI_BASE_URL": config.base_url or "", "CORECODER_MAX_TOKENS": "128", "PYTHONPATH": str(ROOT), "CORECODER_DATA_DIR": str(root / "cli-data")})
    # Config.from_env has already selected the credential; subprocess inherits
    # it without the script ever placing it in output or report data.
    command = [sys.executable, "-m", "corecoder", "--json", "--yes", "--max-rounds", "1", "-p", "Reply exactly LIVE_CLI_DONE"]
    proc = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=90, check=False)
    assertion("live CLI process exits zero", proc.returncode == 0, scrub(proc.stderr[-1000:]))
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assertion("live CLI emits one JSON line", len(lines) == 1, scrub(proc.stdout))
    payload = json.loads(lines[0])
    assertion("live CLI schema version", payload.get("schema_version") == 1, payload)
    assertion("live CLI completed", payload.get("status") == "completed", payload)
    assertion("live CLI answer marker", "LIVE_CLI_DONE" in payload.get("text", ""), payload)
    assertion("live CLI has exit_code zero", payload.get("exit_code") == 0, payload)
    # Preserve only the safe, structured fields needed for audit.
    return {
        "status": payload.get("status"),
        "rounds": payload.get("rounds"),
        "text": payload.get("text"),
        "usage": payload.get("usage", {}),
        "exit_code": payload.get("exit_code"),
        "stderr_tail": scrub(proc.stderr[-500:]),
    }


def live_cli_max_rounds_check(config, root):
    """Prepare a one-request CLI boundary check for the final frozen run."""
    scratch = root / "cli-max-rounds"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "input.txt").write_bytes(b"CLI_MAX_ROUNDS_FIXTURE\n")
    env = os.environ.copy()
    env.update({"CORECODER_MODEL": config.model, "OPENAI_BASE_URL": config.base_url or "", "CORECODER_MAX_TOKENS": "128", "PYTHONPATH": str(ROOT), "CORECODER_DATA_DIR": str(root / "cli-max-data")})
    command = [
        sys.executable, "-m", "corecoder", "--json", "--yes", "--max-rounds", "1", "-p",
        "Your first action MUST be read_file on input.txt. Do not answer until you have read it, then reply CLI_MAX_ROUNDS_DONE.",
    ]
    proc = subprocess.run(command, cwd=scratch, env=env, text=True, capture_output=True, timeout=90, check=False)
    assertion("live CLI max-rounds exits 2", proc.returncode == 2, scrub(proc.stderr[-1000:]))
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assertion("live CLI max-rounds emits one JSON line", len(lines) == 1, scrub(proc.stdout))
    payload = json.loads(lines[0])
    assertion("live CLI max-rounds schema version", payload.get("schema_version") == 1, payload)
    assertion("live CLI max-rounds status", payload.get("status") == "max_rounds", payload)
    assertion("live CLI max-rounds result exit code", payload.get("exit_code") == 2, payload)
    assertion("live CLI max-rounds one round", payload.get("rounds") == 1, payload)
    assertion("live CLI max-rounds stderr diagnosis", "max_rounds" in proc.stderr, scrub(proc.stderr))
    assertion("live CLI max-rounds tool errors empty", not payload.get("tool_errors"), payload.get("tool_errors"))
    files = sorted(p.relative_to(scratch).as_posix() for p in scratch.rglob("*") if p.is_file())
    assertion("live CLI max-rounds has no extra fixture files", files == ["input.txt"], files)
    return {
        "status": payload.get("status"),
        "rounds": payload.get("rounds"),
        "text": payload.get("text"),
        "usage": payload.get("usage", {}),
        "exit_code": payload.get("exit_code"),
        "tool_errors": payload.get("tool_errors", []),
        "stderr_tail": scrub(proc.stderr[-500:]),
        "fixture_files": files,
    }


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    config = Config.from_env()
    assertion("DeepSeek API key configured", bool(config.api_key))
    assertion("DeepSeek endpoint configured", "deepseek" in (config.base_url or "").lower())
    workspace = Path(tempfile.mkdtemp(prefix="corecoder-live-", dir=str(REPORT)))
    report = {
        "date": "2026-10-11",
        "config": {"provider": config.provider, "model": config.model, "endpoint": (config.base_url or "").split("?", 1)[0]},
        "workspace": "<temporary-redacted>",
        "offline": [],
        "live_agent": None,
        "live_cli": None,
        "live_cli_max_rounds": None,
        "coverage": {
            "live": ["OpenAI-compatible DeepSeek streaming Agent/read/write/edit tool calls", "real parent/child Agent path", "real write side effect and checkpoint external conflict", "strict independently captured model summary", "CLI --json completed result and usage", "CLI --json max_rounds=1 boundary"],
            "offline": ["R01 child state isolation", "plan mode refusal and side-effect guard", "R03 exact undo restore", "R05 budget gate/R06 summary evidence"],
            "not_covered": ["broader summary semantic tasks beyond the fixed evidence set", "third-party MCP server/network", "provider billing price verification", "cross-process crash/power-loss", "symlink permission branch on this Windows host"],
        },
    }
    try:
        report["offline"] = offline_checks(workspace)
        report["live_agent"] = live_agent_check(workspace, config)
        report["live_cli"] = live_cli_check(config, workspace)
        report["live_cli_max_rounds"] = live_cli_max_rounds_check(config, workspace)
        report["result"] = "PASS"
    except Exception as exc:  # report a scrubbed failure while preserving traceback locally in stderr
        report["result"] = "FAIL"
        report["failure"] = scrub(exc)
        raise
    finally:
        # Only the isolated fixture is removed. Report JSON is retained.
        shutil.rmtree(workspace, ignore_errors=True)
        final_requests = 0
        if report.get("result") == "PASS":
            final_requests = (report.get("live_agent") or {}).get("usage", {}).get("requests", 0)
            for cli_key in ("live_cli", "live_cli_max_rounds"):
                final_requests += len(((report.get(cli_key) or {}).get("usage") or {}).get("request_ids", []))
        report["execution_history"] = {
            "offline_preflight_failure": {"api_requests": 0, "cause": "fixture newline assertion; corrected before live call"},
            "complete_read_write_pass": {"api_requests": 4, "model_requests": 3, "cli_requests": 1, "result": "pass"},
            "enhanced_edit_child_pass": {"api_requests": 8, "model_requests": 7, "cli_requests": 1, "result": "pass"},
            "enhanced_live_summary_pass": {"api_requests": 9, "model_requests": 8, "cli_requests": 1, "summary_requests": 1, "result": "pass"},
            "standalone_live_summary_pass": {"api_requests": 1, "model_requests": 1, "summary_source": "model", "usage_known": True, "result": "pass"},
            "summary_semantic_fail_before_prompt_fix": {"api_requests": 1, "result": "fail"},
            "summary_semantic_fix_pass": {"api_requests": 1, "model_requests": 1, "summary_source": "model", "usage_known": True, "result": "pass"},
            "final_complete_runner": {"api_requests": final_requests, "result": report.get("result", "FAIL")},
            "total_api_requests_across_runs": 24 + final_requests,
            "pricing": "unknown; endpoint usage was retained and no price was asserted",
        }
        safe = json.loads(json.dumps(report, ensure_ascii=False, default=str))
        (REPORT / "acceptance_report.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(safe, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
