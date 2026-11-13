"""Local, read-only installation diagnostics."""
import argparse
import contextlib
import importlib.util
import io
import json
import logging
import math
import os
import sys


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "corecoder doctor: invalid arguments\n")


def main(argv=None):
    parser = _Parser(prog="corecoder doctor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    checks = []

    def add(check_id, status, message, remediation):
        checks.append({"id": check_id, "status": status, "message": message, "remediation": remediation})

    ok = sys.version_info >= (3, 10)
    add("python", "pass" if ok else "fail", "Python version is supported." if ok else "Python 3.10 or later is required.", "Upgrade Python." if not ok else "")
    for module in ("openai", "rich", "prompt_toolkit", "dotenv", "jsonschema"):
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError, AttributeError):
            available = False
        add("dependency." + module, "pass" if available else "fail", "Required dependency is available." if available else "Required dependency is missing.", "Install CoreCoder dependencies." if not available else "")

    env = dict(os.environ)
    try:
        from dotenv import main as dotenv_main
        DotEnv = dotenv_main.DotEnv
        is_disabled = getattr(dotenv_main, "_load_dotenv_disabled", lambda: False)
        from .config import _dotenv_path
        path = _dotenv_path()
        if not is_disabled() and path.exists() and not path.is_file():
            raise OSError("Configuration is not a regular file")
        if not is_disabled():
            logger = logging.getLogger("dotenv.main")
            was_disabled = logger.disabled
            try:
                logger.disabled = True
                with contextlib.redirect_stderr(io.StringIO()):
                    values = DotEnv(path, override=False).dict()
            finally:
                logger.disabled = was_disabled
            for key, value in values.items():
                effective_key = key.upper() if os.name == "nt" else key
                if value is not None and effective_key not in env:
                    env[effective_key] = value
        add("config.dotenv", "pass", "Local configuration was checked.", "")
    except (ImportError, OSError, ValueError, UnicodeError):
        add("config.dotenv", "warn", "Local configuration could not be checked.", "Check the .env file and python-dotenv installation.")
    for key, convert in (("CORECODER_MAX_TOKENS", int), ("CORECODER_MAX_CONTEXT", int), ("CORECODER_TEMPERATURE", float)):
        try:
            value = convert(env.get(key, "0"))
            valid = math.isfinite(value)
        except (ValueError, TypeError, OverflowError):
            valid = False
        add("config." + key.lower(), "pass" if valid else "fail", "Numeric configuration is valid." if valid else "Numeric configuration is invalid.", "Correct this configuration setting." if not valid else "")
    key_present = any(env.get(key) for key in ("CORECODER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"))
    add("config.api_key", "pass" if key_present else "warn", "API key is configured." if key_present else "No API key is configured.", "Configure an API key before using a model." if not key_present else "")
    if env.get("CORECODER_PROVIDER", "openai") == "litellm":
        try:
            available = importlib.util.find_spec("litellm") is not None
        except (ImportError, ValueError, AttributeError):
            available = False
        add("dependency.litellm", "pass" if available else "fail", "LiteLLM is available." if available else "LiteLLM is missing.", "Install the LiteLLM extra." if not available else "")
    if os.name == "nt":
        from .shell import _git_bash
        available = _git_bash() is not None
        add("shell.git_bash", "pass" if available else "warn", "Git Bash is available." if available else "Git Bash was not found; commands use the Windows shell.", "Install Git for Windows to use Git Bash." if not available else "")
    if args.json:
        print(json.dumps({"checks": checks}, ensure_ascii=False))
    else:
        for check in checks:
            print(f"{check['status'].upper()} {check['id']}: {check['message']}")
            if check["remediation"]:
                print(f"  {check['remediation']}")
    return int(any(check["status"] == "fail" for check in checks))
