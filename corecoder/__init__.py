"""CoreCoder - Minimal AI coding agent inspired by Claude Code's architecture."""
from importlib import import_module

__version__ = "0.8.0"
__all__ = ["ALL_TOOLS", "LLM", "Agent", "Config", "RunResult", "SessionState", "__version__", "create_tools"]
_EXPORTS = {"Agent": "agent", "Config": "config", "LLM": "llm", "RunResult": "results", "SessionState": "state", "ALL_TOOLS": "tools", "create_tools": "tools"}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module("." + _EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
