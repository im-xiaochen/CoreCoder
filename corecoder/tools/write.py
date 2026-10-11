"""File creation / overwrite."""

import os
import uuid
from typing import ClassVar

from ..checkpoints import abort as _abort_checkpoint
from ..checkpoints import begin as _begin_checkpoint
from ..checkpoints import finalize as _finalize_checkpoint
from ..state import SessionState
from .base import Tool
from .edit import _changed_files


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new file or completely overwrite an existing one. "
        "For small edits to existing files, prefer edit_file instead."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path for the file",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write",
            },
        },
        "required": ["file_path", "content"],
    }

    def __init__(self, state: SessionState | None = None):
        self.state = state or SessionState()
        if state is None:
            self.state.checkpoint_store = None

    def clone_for_state(self, state: SessionState):
        return type(self)(state=state)

    def execute(self, file_path: str, content: str) -> str:
        from .base import FILE_MUTATION_LOCK

        try:
            with FILE_MUTATION_LOCK:
                p = self.state.resolve_path(file_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                store = self.state.checkpoint_store
                token = (store.begin(p) if store is not None else _begin_checkpoint(p))
                wrote = False
                try:
                    tmp = p.with_name(p.name + f".{uuid.uuid4().hex}.tmp")
                    try:
                        with tmp.open("w", encoding="utf-8", newline="") as fh:
                            fh.write(content)
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
                    # A write failure has no committed after-image and can be
                    # removed. A finalize failure means the target changed;
                    # retain its checkpoint for retryable undo.
                    if not wrote:
                        try: (store.abort(token) if store is not None else _abort_checkpoint(token))
                        except Exception: pass  # noqa: BLE001, S110
                    raise
                self.state.changed_files.add(str(p))
                _changed_files.add(str(p))
            n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return f"Wrote {n_lines} lines to {p}"
        except Exception as e:  # noqa: BLE001
            # boundary: the agent gets an error string, not a traceback
            return f"Error: {e}"
