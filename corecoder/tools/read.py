"""File reading with line numbers."""

from typing import ClassVar

from ..state import SessionState
from .base import Tool


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file's contents with line numbers. "
        "Always read a file before editing it."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file",
            },
            "offset": {
                "type": "integer",
                "description": "Start line (1-based). Default 1.",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read. Default 2000.",
            },
            "char_offset": {"type": "integer", "description": "Start character offset for a single-line or archived output."},
            "char_limit": {"type": "integer", "description": "Maximum characters to return after char_offset."},
        },
        "required": ["file_path"],
    }

    def __init__(self, state: SessionState | None = None):
        self.state = state or SessionState()

    def clone_for_state(self, state: SessionState):
        return type(self)(state=state)

    def execute(self, file_path: str, offset: int = 1, limit: int = 2000,
                char_offset: int | None = None, char_limit: int | None = None) -> str:
        try:
            p = self.state.resolve_path(file_path)
            if not p.exists():
                return f"Error: {file_path} not found"
            if not p.is_file():
                return f"Error: {file_path} is a directory, not a file"

            # Preserve newline bytes for character pagination: offsets refer to
            # the archived/source character stream, including CRLF pairs.
            with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                text = fh.read()
            if char_offset is not None:
                start_char = max(0, char_offset)
                end_char = None if char_limit is None else start_char + max(0, char_limit)
                chunk = text[start_char:end_char]
                suffix = f"\n... (characters {start_char}-{start_char + len(chunk)} of {len(text)})" if end_char is not None and end_char < len(text) else ""
                return chunk + suffix or "(empty file)"
            lines = text.splitlines()
            total = len(lines)

            start = max(0, offset - 1)
            chunk = lines[start : start + limit]
            numbered = [f"{start + i + 1}\t{ln}" for i, ln in enumerate(chunk)]
            result = "\n".join(numbered)

            if total > start + limit:
                result += f"\n... ({total} lines total, showing {start+1}-{start+len(chunk)})"
            return result or "(empty file)"
        except Exception as e:  # noqa: BLE001
            # boundary: the agent gets an error string, not a traceback
            return f"Error: {e}"
