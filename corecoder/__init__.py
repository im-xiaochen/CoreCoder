"""CoreCoder - Minimal AI coding agent inspired by Claude Code's architecture."""

__version__ = "0.8.0"

from corecoder.agent import Agent
from corecoder.config import Config
from corecoder.llm import LLM
from corecoder.results import RunResult
from corecoder.state import SessionState
from corecoder.tools import ALL_TOOLS, create_tools

__all__ = ["ALL_TOOLS", "LLM", "Agent", "Config", "RunResult", "SessionState", "__version__", "create_tools"]
