"""Regression tests for the local doctor command."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_doctor(*args, env=None):
    merged = os.environ.copy()
    merged.update(env or {})
    merged["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-m", "corecoder", "doctor", *args], cwd=ROOT, env=merged, capture_output=True, text=True, check=False)


def test_invalid_argument_does_not_echo_secret():
    result = run_doctor("secret-canary")
    assert result.returncode == 2
    assert "secret-canary" not in result.stdout + result.stderr


def test_malformed_numeric_is_reported_without_values():
    result = run_doctor("--json", env={"CORECODER_MAX_TOKENS": "secret-canary", "OPENAI_BASE_URL": "secret-canary"})
    assert result.returncode == 1
    assert any(c["id"] == "config.corecoder_max_tokens" and c["status"] == "fail" for c in json.loads(result.stdout)["checks"])
    assert "secret-canary" not in result.stdout + result.stderr


def test_dotenv_interpolation_uses_inherited_environment(tmp_path):
    (tmp_path / ".env").write_text("BASE=4096\nCORECODER_MAX_TOKENS=${BASE}\n")
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(ROOT), "BASE": "invalid-secret"})
    env.pop("CORECODER_MAX_TOKENS", None)
    env.pop("PYTHON_DOTENV_DISABLED", None)
    result = subprocess.run([sys.executable, "-m", "corecoder", "doctor", "--json"], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert any(c["id"] == "config.corecoder_max_tokens" and c["status"] == "fail" for c in json.loads(result.stdout)["checks"])
    assert "invalid-secret" not in result.stdout + result.stderr


def test_entry_routes_doctor_without_loading_cli():
    script = "import sys; sys.modules['corecoder.cli'] = None; sys.argv=['corecoder','doctor','--json']; import corecoder.entry; corecoder.entry.main()"
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode in (0, 1)
    assert '"checks"' in result.stdout
    assert not result.stderr


def test_dotenv_case_normalization_matches_platform(tmp_path):
    (tmp_path / ".env").write_text("corecoder_max_tokens=broken\nCORECODER_MAX_TOKENS=4096\n")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env.pop("CORECODER_MAX_TOKENS", None)
    env.pop("PYTHON_DOTENV_DISABLED", None)
    result = subprocess.run([sys.executable, "-m", "corecoder", "doctor", "--json"], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    check = next(c for c in json.loads(result.stdout)["checks"] if c["id"] == "config.corecoder_max_tokens")
    assert check["status"] == ("fail" if os.name == "nt" else "pass")
