"""Search-and-replace file editing (Claude Code's key innovation).

The core idea: instead of sending whole-file rewrites or line-number patches,
the LLM specifies an *exact* substring to find and its replacement. The
substring must appear exactly once in the file, which eliminates ambiguity
and makes edits safe and reviewable.
"""

import difflib
import os
import uuid
from typing import ClassVar

from ..checkpoints import abort as _abort_checkpoint
from ..checkpoints import begin as _begin_checkpoint
from ..checkpoints import finalize as _finalize_checkpoint
from ..state import SessionState
from .base import Tool

# track files changed this session for /diff
_changed_files: set[str] = set()


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit a file by replacing an exact string match. "
        "old_string must appear exactly once in the file for safety. "
        "Include enough surrounding context to ensure uniqueness."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find (must be unique in file)",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def __init__(self, state: SessionState | None = None):
        self.state = state or SessionState()
        if state is None:
            self.state.checkpoint_store = None

    def clone_for_state(self, state: SessionState):
        return type(self)(state=state)

    def execute(self, file_path: str, old_string: str, new_string: str) -> str:
        from .base import FILE_MUTATION_LOCK

        try:
            with FILE_MUTATION_LOCK:
                p = self.state.resolve_path(file_path)
                if not p.exists():
                    return f"Error: {file_path} not found"

                try:
                    content = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    return f"Error: {file_path} is not a UTF-8 text file (edit_file only edits text files)"
                occurrences = content.count(old_string)

                if occurrences == 0:
                    preview = content[:500] + ("..." if len(content) > 500 else "")
                    return (
                        f"Error: old_string not found in {file_path}.\n"
                        f"File starts with:\n{preview}"
                    )
                if occurrences > 1:
                    return (
                        f"Error: old_string appears {occurrences} times in {file_path}. "
                        f"Include more surrounding lines to make it unique."
                    )

                new_content = content.replace(old_string, new_string, 1)
                store = self.state.checkpoint_store
                token = (store.begin(p) if store is not None else _begin_checkpoint(p))
                wrote = False
                try:
                    tmp = p.with_name(p.name + f".{uuid.uuid4().hex}.tmp")
                    try:
                        with tmp.open("w", encoding="utf-8", newline="") as fh:
                            fh.write(new_content)
                            fh.flush()
                            os.fsync(fh.fileno())
                        tmp.replace(p)
                    finally:
                        try: tmp.unlink(missing_ok=True)
                        except OSError: pass
                    wrote = True
                    if store is not None: store.finalize(token, p)
                    else: _finalize_checkpoint(token, p)
                except Exception:
                    if not wrote:
                        try: (store.abort(token) if store is not None else _abort_checkpoint(token))
                        except Exception: pass  # noqa: BLE001, S110
                    raise
                self.state.changed_files.add(str(p))
                _changed_files.add(str(p))

            # generate a unified diff so the user/LLM can see exactly what changed
            diff = _unified_diff(content, new_content, str(p))
            return f"Edited {p}\n{diff}"
        except Exception as e:  # noqa: BLE001
            # boundary: the agent gets an error string, not a traceback
            return f"Error: {e}"


def _unified_diff(old: str, new: str, filename: str, context: int = 3) -> str:
    """Generate a compact unified diff between old and new file content."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}", tofile=f"b/{filename}",
        n=context,
    )
    result = "".join(diff)
    # truncate enormous diffs
    if len(result) > 3000:
        result = result[:2500] + "\n... (diff truncated)\n"
    return result
