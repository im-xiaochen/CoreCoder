"""Offline, isolated repository behavior evaluation. No network or model calls."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import nullcontext
from pathlib import Path

from .git_workspace import GitWorkspace
from .project_checks import ProjectChecks


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def crash_fixture(root):
    from web_backend.service import Service

    from .demo import ScriptedLLM
    from .llm import LLMResponse, ToolCall

    root = Path(root)

    class CrashLLM(ScriptedLLM):
        def chat(self, *args, **kwargs):
            if not self._turns:
                while not (root / "run.json").exists():
                    time.sleep(0.01)
                os._exit(23)
            return super().chat(*args, **kwargs)

    script = [
        LLMResponse(
            content="",
            tool_calls=[ToolCall(id="one", name="write_file", arguments={"file_path": str(root / "effect"), "content": "once"})],
        )
    ]
    service = Service(root, store=root / "store", llm_factory=lambda: CrashLLM(script), extensions=False)
    sid = service.create()["id"]
    service.get(sid).agent.permission._always.add("write_file")
    rid = service.start(sid, "Write the fixture marker.", legacy_events=False)
    (root / "run.json").write_text(json.dumps({"sid": sid, "rid": rid}), encoding="utf-8")
    while True:
        time.sleep(0.1)


def evaluate(output, task=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    report = {
        "schema_version": 1,
        "suite": "offline-repository",
        "model": "offline-scripted",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "git": subprocess.run(["git", "--version"], capture_output=True, text=True, check=True, timeout=10).stdout.strip(),
            "optimize": sys.flags.optimize,
        },
        "fixture_version": 1,
        "started_at": started,
        "tasks": [],
        "cost": {"known_cost": None, "missing_usage": True, "source": "no model calls"},
        "limitations": ["Scripted model responses do not measure model quality or real spending."],
    }
    names = ["git_index", "checks", "agent", "cancel", "recovery", "usage"] if task is None else [task]
    for name in names:
        row = {
            "id": name,
            "status": "failed",
            "assertions": [],
            "human_interventions": 0,
            "budget": {"command_timeout_seconds": 30, "repair_attempts": 0},
        }
        report["tasks"].append(row)
        try:
            if name not in {"git_index", "checks", "agent", "cancel", "recovery", "usage"}:
                raise ValueError("Unknown task")
            task_dir = output / "tasks" / name
            task_dir.mkdir(parents=True, exist_ok=True)
            with nullcontext(tempfile.mkdtemp(prefix="run-", dir=task_dir)) as d:
                root = Path(d)
                row["artifact_path"] = str(root)
                env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
                env.update(
                    GIT_CONFIG_GLOBAL=os.devnull,
                    GIT_CONFIG_NOSYSTEM="1",
                    GIT_TERMINAL_PROMPT="0",
                    GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000",
                    GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000",
                )

                def git(*args, root=root, env=env):
                    return subprocess.run(
                        ["git", "-c", "core.hooksPath=" + os.devnull, *args],
                        cwd=root,
                        env=env,
                        check=True,
                        capture_output=True,
                        timeout=30,
                    ).stdout

                git("init", "-b", "fixture")
                git("config", "user.name", "Fixture")
                git("config", "user.email", "fixture@example.invalid")
                (root / "a.txt").write_text("baseline", encoding="utf-8")
                git("add", "a.txt")
                git("commit", "-m", "fixture")
                row["initial_tree"] = git("rev-parse", "HEAD^{tree}").decode().strip()
                if name == "git_index":
                    (root / "a.txt").write_text("staged", encoding="utf-8")
                    git("add", "a.txt")
                    (root / "a.txt").write_text("later", encoding="utf-8")
                    (root / "user.txt").write_text("preserve", encoding="utf-8")
                    repo = GitWorkspace(root)
                    review = repo.status()
                    result = repo.commit(["a.txt"], review["review_token"], "reviewed")
                    require(git("show", "HEAD:a.txt") == b"staged", "wrong committed blob")
                    require(
                        (root / "a.txt").read_text() == "later" and (root / "user.txt").read_text() == "preserve",
                        "user work changed",
                    )
                    row["assertions"] = ["committed reviewed index", "preserved worktree and untracked file"]
                    row["commit"] = result["commit"]
                elif name == "checks":
                    (root / ".corecoder").mkdir()
                    (root / ".corecoder" / "checks.json").write_text(
                        json.dumps({"version": 1, "checks": [{"id": "fail", "name": "failure", "command": "exit 7", "timeout": 30}]}),
                        encoding="utf-8",
                    )
                    checks = ProjectChecks(root, root / "results")
                    config = checks.inspect()
                    result = checks.run("fail", config["token"], authorized=True)
                    require(result["status"] == "failed" and result["exit_code"] == 7, "wrong check result")
                    require(checks.get(result["id"])["exit_code"] == 7, "result was not persisted")
                    row["assertions"] = ["real exit code and persisted result"]
                    row["exit_code"] = result["exit_code"]
                elif name == "agent":
                    (root / 'calc.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
                    from .agent import Agent
                    from .demo import ScriptedLLM
                    from .llm import LLMResponse, ToolCall
                    from .permissions import Permission
                    from .state import SessionState
                    from .tools import create_tools

                    state = SessionState(workspace_root=root, cwd=root)
                    script = [
                        LLMResponse(
                            content="",
                            tool_calls=[
                                ToolCall(
                                    id="fix",
                                    name="write_file",
                                    arguments={
                                        "file_path": str(root / "calc.py"),
                                        "content": "def add(a, b):\n    return a + b\n",
                                    },
                                ),
                                ToolCall(
                                    id="test",
                                    name="write_file",
                                    arguments={
                                        "file_path": str(root / "verify.py"),
                                        "content": "from calc import add\nprint(add(2, 3))\n",
                                    },
                                ),
                            ],
                        ),
                        LLMResponse(content="done"),
                    ]
                    agent = Agent(
                        llm=ScriptedLLM(script),
                        state=state,
                        tools=create_tools(state),
                        permission=Permission(allow_all=True),
                        hooks=None,
                    )
                    result = agent.chat_result("Fix addition and add verification.")
                    require(result.status == "completed" and not result.tool_errors, "agent failed")
                    verify = subprocess.run(
                        [os.sys.executable, str(root / "verify.py")], cwd=root, capture_output=True, timeout=30, check=False
                    )
                    require(verify.returncode == 0 and verify.stdout.strip() == b"5", "verification failed")
                    oracle = subprocess.run([sys.executable, '-c', 'from calc import add; import sys; sys.exit(0 if add(2,3)==5 and add(0,-7)==-7 else 1)'], cwd=root, capture_output=True, timeout=30, check=False)
                    require(oracle.returncode == 0, 'independent arithmetic oracle failed')
                    row.update(
                        assertions=["agent wrote two files", "verification exited zero"],
                        exit_code=verify.returncode,
                        rounds=result.rounds,
                    )
                elif name == "cancel":
                    row["budget"] = {"command_timeout_seconds": 10, "cancel_fallback_seconds": 5, "repair_attempts": 0}
                    from .command_runner import run_command

                    cancel = threading.Event()
                    observed = threading.Event()

                    def on_output(stream, text, sequence, observed=observed, cancel=cancel):
                        if text:
                            observed.set()
                            if sequence >= 20:
                                cancel.set()

                    timer = threading.Timer(5.0, cancel.set)
                    timer.start()
                    try:
                        command = f'"{sys.executable}" -c "import sys,time; print(\'x\'*3000000,flush=True); time.sleep(20)"'
                        result = run_command(command, str(root), timeout=10, cancel=cancel, on_output=on_output)
                    finally:
                        timer.cancel()
                    require(result.status == "cancelled", "managed command was not cancelled")
                    require(observed.is_set() and bool(result.stdout), "no output observed before cancellation")
                    row.update(
                        assertions=["managed process cancelled"],
                        command_status=result.status,
                        truncated=result.truncated,
                        exit_code=result.exit_code,
                    )
                elif name == "recovery":
                    from web_backend.service import Service

                    from .demo import ScriptedLLM

                    factory = lambda: ScriptedLLM([])
                    store = root / "store"
                    crashed = subprocess.run(
                        [sys.executable, "-m", "corecoder.evaluation", "--crash-fixture", str(root)], check=False, timeout=30
                    )
                    require(crashed.returncode == 23, "fixture did not crash at agent boundary")
                    ids = json.loads((root / "run.json").read_text(encoding="utf-8"))
                    rid = ids["rid"]
                    require((root / "effect").read_text() == "once", "agent did not write effect")
                    (root / "effect").write_text("after-crash", encoding="utf-8")
                    restored = Service(root, store=store, llm_factory=factory, extensions=False)
                    require(restored.runs.view(rid)["status"] == "interrupted", "run not interrupted after restart")
                    require((root / "effect").read_text() == "after-crash", "effect changed during restart")
                    row.update(assertions=["restart marks active run interrupted", "no replayed effect"], run_id=rid)
                else:
                    from decimal import Decimal

                    from .llm import LLM
                    from .session import load_session_full, save_session
                    from .usage import UsageEntry

                    entries = [
                        UsageEntry(
                            "known",
                            "a",
                            "fixture",
                            "local",
                            "fixed",
                            prompt_tokens=1000,
                            input_price=Decimal(1),
                            output_price=Decimal(1),
                            estimated_cost=Decimal("0.001"),
                        ),
                        UsageEntry("unknown", "b", "fixture", "local", "unpriced", prompt_tokens=5),
                        UsageEntry("missing", "c", "fixture", "local", "missing", usage_known=False),
                    ]
                    llm = LLM.__new__(LLM)
                    sid = save_session([], "offline-scripted", usage_ledger=entries)
                    restored = load_session_full(sid)
                    require(restored is not None, "session could not be loaded")
                    (root / "saved-session.json").write_text(json.dumps(restored, ensure_ascii=False, indent=2), encoding="utf-8")
                    llm.restore_usage(restored["usage_ledger"])
                    require(llm.usage_ledger == tuple(entries), "usage roundtrip mismatch")
                    require(
                        llm.cost_summary.known_cost == Decimal("0.001")
                        and llm.cost_summary.missing_usage_requests == 1
                        and llm.cost_summary.unknown_requests == 1,
                        "usage cost mismatch",
                    )
                    row.update(
                        assertions=["synthetic known, unknown and missing usage restored"],
                        synthetic_cost=str(llm.cost_summary.known_cost),
                        unknown_requests=llm.cost_summary.unknown_requests,
                        missing_usage_requests=llm.cost_summary.missing_usage_requests,
                        cost_source="synthetic, not real spending",
                    )
                row["status"] = "passed"
        except Exception as exc:  # noqa: BLE001 - record fixture failures
            row["error"] = str(exc)
    report["ended_at"] = time.time()
    report["status"] = "passed" if all(row["status"] == "passed" for row in report["tasks"]) else "failed"
    path = output / "report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--task", choices=["git_index", "checks", "agent", "cancel", "recovery", "usage"])
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--crash-fixture", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.crash_fixture:
        crash_fixture(args.crash_fixture)
        return 1
    if not args.output:
        parser.error("--output is required")
    if not args.worker:
        output = str(Path(args.output).resolve())
        with tempfile.TemporaryDirectory() as isolated:
            env = {
                key: os.environ[key]
                for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
                if key in os.environ
            }
            env.update(
                HOME=isolated,
                USERPROFILE=isolated,
                XDG_CONFIG_HOME=isolated,
                GIT_CONFIG_GLOBAL=os.devnull,
                GIT_CONFIG_NOSYSTEM="1",
                PYTHONPATH=str(Path(__file__).resolve().parent.parent),
            )
            Path(isolated, ".env").write_text("", encoding="utf-8")
            command = [
                sys.executable,
                *(["-O"] if sys.flags.optimize else []),
                "-m",
                "corecoder.evaluation",
                "--worker",
                "--output",
                output,
            ]
            if args.task:
                command += ["--task", args.task]
            return subprocess.run(command, env=env, cwd=isolated, check=False).returncode
    report = evaluate(args.output, args.task)
    print(json.dumps({"status": report["status"], "report": str(Path(args.output).resolve() / "report.json")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
