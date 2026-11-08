"""External F18 evidence oracle; only fake config and retained fixture artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VALIDATION = HERE / "validation"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def clean_env(home):
    env = {key: os.environ[key] for key in ["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"] if key in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home),
               PYTHONPATH=str(REPO), PYTHONIOENCODING="utf-8", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", CORECODER_DATA_DIR=str(home / "data"))
    return env


def verify_artifacts(label, optimized):
    base = VALIDATION / label
    driver = load(base / "driver.json")
    report = load(base / "output/report.json")
    require(driver["exit_code"] == 0 and not driver["fake_extension_executed"], f"{label}: CLI/isolation failure")
    require(driver["evaluation_sha256_before"] == driver["evaluation_sha256_after"], "source changed during evaluation")
    require(report["status"] == "passed" and report["environment"]["optimize"] == optimized, "worker optimize/status")
    require(report["fixture_version"] == 1 and report["model"] == "offline-scripted", "fixture/model identity")
    require(report["cost"]["known_cost"] is None and report["cost"]["missing_usage"] is True, "unknown real spending became zero")
    require("do not measure model quality" in " ".join(report["limitations"]), "scripted limitation missing")
    rows = {row["id"]: row for row in report["tasks"]}
    require(set(rows) == {"git_index", "checks", "agent", "cancel", "recovery", "usage"}, "task set mismatch")
    initial_commits, initial_trees = set(), set()
    for name, row in rows.items():
        root = Path(row["artifact_path"])
        require(root.is_dir() and root.is_relative_to(base), f"{name}: evidence not retained/in output")
        require(row["status"] == "passed" and row["human_interventions"] == 0, f"{name}: task metadata")
        require(row["budget"]["repair_attempts"] == 0, f"{name}: repair budget")
        env = clean_env(base / "fake-home")

        def git(*args):
            return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, *args], cwd=root,
                                  env=env, check=True, capture_output=True, timeout=15).stdout.decode().strip()

        first = git("rev-list", "--max-parents=0", "HEAD")
        tree = git("rev-parse", first + "^{tree}")
        require(tree == row["initial_tree"], f"{name}: initial tree mismatch")
        require(git("show", first + ":a.txt") == "baseline", "initial blob differs")
        require(git("show", "-s", "--format=%an|%ae|%aI|%cI", first) ==
                "Fixture|fixture@example.invalid|2000-01-01T00:00:00+00:00|2000-01-01T00:00:00+00:00", "identity/date leaked")
        require(git("branch", "--show-current") == "fixture", "caller Git branch config leaked")
        initial_commits.add(first)
        initial_trees.add(tree)
        if name == "git_index":
            require(git("show", "HEAD:a.txt") == "staged" and git("show", ":a.txt") == "staged", "reviewed blob/index")
            require((root / "a.txt").read_text() == "later" and (root / "user.txt").read_text() == "preserve", "user work lost")
            require(git("diff", "--cached") == "", "commit left staged patch")
        elif name == "checks":
            records = [load(p) for p in (root / "results").glob("*.json")]
            require(len(records) == 1 and records[0]["exit_code"] == 7 and records[0]["status"] == "failed", "real check log")
            require(records[0]["command"] == "exit 7" and records[0]["command_status"] == "exited", "check command evidence")
        elif name == "agent":
            require((root / "verify.py").is_file(), "second changed file absent")
            code = "from calc import add; import sys; cases=[(0,0),(-31,8),(12,-4),(0.125,0.5),(999999,1)]; sys.exit(0 if all(add(a,b)==a+b for a,b in cases) else 9)"
            oracle = subprocess.run([sys.executable, "-I", "-c", "import sys;sys.path.insert(0," + repr(str(root)) + ");" + code],
                                    cwd=root, env=env, capture_output=True, timeout=15)
            require(oracle.returncode == 0, "independent multi-case addition failed")
        elif name == "cancel":
            require(row["command_status"] == "cancelled" and row["exit_code"] != 0 and row["truncated"] is True,
                    "cancel state/truncation evidence")
        elif name == "recovery":
            ids = load(root / "run.json")
            meta = load(root / "store/runs" / (ids["rid"] + ".json"))
            events = [json.loads(line) for line in (root / "store/runs" / (ids["rid"] + ".jsonl")).read_text(encoding="utf-8").splitlines()]
            require(meta["status"] == "interrupted" and meta["id"] == row["run_id"], "run recovery evidence")
            require((root / "effect").read_text() == "after-crash" and len(events) >= 3, "recovery rewrote marker/no persisted activity")
        elif name == "usage":
            original = load(root / "saved-session.json")
            entries = {entry["request_id"]: entry for entry in original["usage_ledger"]}
            require(original["schema_version"] == 3 and len(entries) == 3, "session ledger incomplete")
            require(entries["known"]["estimated_cost"] == "0.001" and entries["known"]["input_price"] == "1", "known price snapshot")
            require(entries["unknown"]["estimated_cost"] is None and entries["unknown"]["input_price"] is None
                    and entries["unknown"]["prompt_tokens"] == 5, "unpriced usage became zero")
            require(entries["missing"]["usage_known"] is False and entries["missing"]["estimated_cost"] is None, "missing usage became zero")
            require(row["unknown_requests"] == 1 and row["missing_usage_requests"] == 1
                    and row["cost_source"] == "synthetic, not real spending", "synthetic price interpretation")
    require(len(initial_commits) == 1 and len(initial_trees) == 1, "fixtures not identical across tasks")
    return {"label": label, "optimize": optimized, "initial_commit": next(iter(initial_commits)),
            "initial_tree": next(iter(initial_trees)), "tasks_verified": sorted(rows), "source_sha256": driver["evaluation_sha256_after"]}


def negative_oracle(optimized):
    base = VALIDATION / ("f18-negative-optimized" if optimized else "f18-negative-normal")
    base.mkdir(exist_ok=False)
    home = base / "fake-home"
    home.mkdir()
    (home / ".env").write_text("", encoding="utf-8")
    # Deliberately wrong addition still prints the expected 5 in verify.py.
    # Only the independent arithmetic oracle may reject it, in normal AND -O.
    wrapper = base / "inject_wrong_addition.py"
    wrapper.write_text('''import runpy
import sys
from pathlib import Path
from corecoder.tools.write import WriteFileTool
real = WriteFileTool.execute
def corrupt(self, file_path, content):
    if Path(file_path).name == 'calc.py':
        content = 'def add(a, b):\\n    return 5\\n'
    return real(self, file_path, content)
WriteFileTool.execute = corrupt
sys.argv = ['corecoder.evaluation', '--worker', '--task', 'agent', '--output', sys.argv[1]]
runpy.run_module('corecoder.evaluation', run_name='__main__')
''', encoding="utf-8")
    command = [sys.executable, *(["-O"] if optimized else []), str(wrapper), str(base / "output")]
    result = subprocess.run(command, cwd=home, env=clean_env(home), capture_output=True, timeout=60)
    (base / "cli.log").write_bytes(result.stdout + result.stderr)
    report = load(base / "output/report.json")
    require(result.returncode == 1 and report["status"] == "failed", "bad arithmetic reported success")
    require(report["environment"]["optimize"] == optimized, "negative worker not optimized")
    row = report["tasks"][0]
    require(row["id"] == "agent" and row["error"] == "independent arithmetic oracle failed", "wrong failure path")
    root = Path(row["artifact_path"])
    require("return 5" in (root / "calc.py").read_text(), "negative fixture was not injected")
    verify = subprocess.run([sys.executable, str(root / "verify.py")], cwd=root, env=clean_env(home), capture_output=True, timeout=15)
    require(verify.returncode == 0 and verify.stdout.strip() == b"5", "negative did not fool the self-written verification")
    return {"optimize": optimized, "exit_code": result.returncode, "error": row["error"], "self_written_verify_exit": verify.returncode}


if __name__ == "__main__":
    outputs = [verify_artifacts("f18-normal", 0), verify_artifacts("f18-optimized", 1)]
    require(outputs[0]["initial_commit"] == outputs[1]["initial_commit"] and outputs[0]["initial_tree"] == outputs[1]["initial_tree"], "round initial state differs")
    negative = [negative_oracle(0), negative_oracle(1)]
    evidence = {"external_artifact_oracles": outputs, "negative_oracles": negative,
                "evaluation_sha256_now": hashlib.sha256((REPO / "corecoder/evaluation.py").read_bytes()).hexdigest()}
    (VALIDATION / "f18-independent-oracle.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence))
