"""Bounded workspace file pattern matching."""

import fnmatch
from functools import lru_cache
from typing import ClassVar

from ..repository_search import inventory
from ..state import SessionState
from .base import Tool


def _matches(path, pattern):
    parts = path.split("/")
    patterns = pattern.replace("\\", "/").split("/")

    @lru_cache(None)
    def match(i, j):
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == "**":
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)

    return match(0, 0)


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["pattern"],
    }

    def __init__(self, state=None):
        self.state = state or SessionState()

    def clone_for_state(self, state):
        return type(self)(state=state)

    def execute(self, pattern, path=".", offset=0, limit=100):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 200:
            return "Error: invalid pagination"
        try:
            base = (self.state.cwd / path).absolute()
            files, backend, truncated = inventory(self.state.workspace_root, base)
            matches = [
                p
                for p in files
                if _matches((p.relative_to(base) if base.is_dir() else p.relative_to(base.parent)).as_posix(), pattern)
            ]
            lines = [str(p) for p in matches[offset : offset + limit]]
            result = "\n".join(lines) or "No files matched."
            if len(matches) > offset + limit:
                result += f"\n... next_offset={offset + limit}"
            if truncated:
                result += "\n... incomplete: file scan limit"
            return result + f"\n[backend: {backend}; live scan]"
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"
