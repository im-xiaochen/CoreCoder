"""One final affected regression pass, isolated from user env/config/extensions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
repo = here.parents[2]
base = here / "validation/phase2-final-regression"
base.mkdir(exist_ok=False)
home = base / "fake-home"
home.mkdir()
(home / ".env").write_text("", encoding="utf-8")
env = {key: os.environ[key] for key in ["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"] if key in os.environ}
env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home),
           CORECODER_DATA_DIR=str(home / "data"), CORECODER_API_KEY="offline-fixture-only",
           PYTHONPATH=str(repo), PYTHONIOENCODING="utf-8", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
selected = [
    "tests/test_git_workspace.py", "tests/test_project_checks.py", "tests/test_repl.py",
    "tests/test_slash_commands.py", "tests/test_session.py", "tests/test_usage_persistence.py",
    "tests/test_command_runner.py", "tests/test_chat_mode.py", "tests/test_plan_mode.py",
    "web_backend/tests/test_web.py", "web_backend/tests/test_runs.py",
    "designDocument/tests/test_r02_workspace_paths.py", "designDocument/tests/test_r08_cost_ledger.py",
    "designDocument/tests/test_r09_cli_result.py", "designDocument/tests/test_r10_config_session_validation.py",
    "tests/test_core.py::test_readme_line_counts_are_current",
    "tests/test_core.py::test_doc_python_line_references_are_current",
]
files = sorted([*repo.glob("corecoder/**/*.py"), *repo.glob("web_backend/*.py"),
                repo / "README.md", repo / "README_CN.md", repo / "pyproject.toml"])
def hashes():
    return {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
before = hashes()
wrapper = base / "offline_pytest.py"
wrapper.write_text('''import socket
import sys
import pytest
real_connect = socket.socket.connect
def local_only(self, address):
    if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', 'localhost', '::1'):
        raise RuntimeError('Offline regression blocked network connection')
    return real_connect(self, address)
socket.socket.connect = local_only
raise SystemExit(pytest.main(sys.argv[1:]))
''', encoding="utf-8")
command = [sys.executable, str(wrapper), *[str(repo / path) for path in selected], "-q",
           "--basetemp", str(base / "tmp"), "--junitxml", str(base / "result.xml")]
with (base / "pytest.log").open("w", encoding="utf-8") as log:
    result = subprocess.run(command, cwd=home, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
after = hashes()
evidence = {"command": command, "cwd": str(home), "exit_code": result.returncode,
            "files_before": before, "files_after": after, "hashes_unchanged": before == after,
            "scope": "new Git/checks plus affected CLI/session/managed command/modes/Web/run/workspace/cost/docs; not old full suite"}
(base / "driver.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
print((base / "pytest.log").read_text(encoding="utf-8"))
print(json.dumps({"exit_code": result.returncode, "hashes_unchanged": before == after}))
raise SystemExit(result.returncode if before == after else 99)
