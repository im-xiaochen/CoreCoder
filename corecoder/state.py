"""Per-agent session state.

The state object is deliberately small and owns all mutable values that must
not leak between agents.  Tools use it for path resolution instead of relying
on process or thread cwd.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


def new_session_id() -> str:
    return uuid4().hex


@dataclass
class SessionState:
    workspace_root: Path | str | None = None
    cwd: Path | str | None = None
    session_id: str = field(default_factory=new_session_id)
    todos: list[dict] = field(default_factory=list)
    changed_files: set[str] = field(default_factory=set)
    parent_session_id: str | None = None
    # R03 may inject a per-session checkpoint store without coupling R01/R02
    checkpoint_store: object | None = None
    artifact_dir: Path | str | None = None
    # Derived execution evidence; canonical messages remain the source of truth.
    execution_records: list[dict] = field(default_factory=list)
    task_memory: object | None = None

    def __post_init__(self) -> None:
        base = Path(self.workspace_root or os.getcwd()).expanduser().resolve()
        self.workspace_root = base
        self.cwd = Path(self.cwd or base).expanduser()
        if not self.cwd.is_absolute():
            self.cwd = base / self.cwd
        self.cwd = self.cwd.resolve()
        if self.artifact_dir is None:
            self.artifact_dir = base / ".corecoder-artifacts" / self.session_id
        else:
            self.artifact_dir = Path(self.artifact_dir).expanduser().resolve()
        if self.checkpoint_store is None:
            from .checkpoints import CheckpointStore

            self.checkpoint_store = CheckpointStore(self.workspace_root, self.session_id)

    def resolve_path(self, value: str | os.PathLike) -> Path:
        """Resolve *value* against this session's cwd and return an absolute path."""
        p = Path(value).expanduser()
        if not p.is_absolute():
            p = self.cwd / p
        return p.resolve()

    def fork(self) -> "SessionState":
        """Create an isolated child state at the parent's current directory."""
        return SessionState(
            workspace_root=self.workspace_root,
            cwd=self.cwd,
            parent_session_id=self.session_id,
        )

    def record_outcome(
        self, call_id: str, tool_name: str, status: str, arguments=None, result: str = "", artifact_path: str | None = None,
        execution_cwd: str | Path | None = None,
    ) -> None:
        """Record one tool outcome and incrementally refresh structured memory."""
        from .context import ContextManager, _redact

        allowed = {"succeeded", "failed", "refused", "cancelled", "unknown"}
        status = status if status in allowed else "unknown"
        cwd = Path(execution_cwd or self.cwd).expanduser().resolve()
        derived_args = dict(arguments or {})
        for key in ("file_path", "path"):
            value = derived_args.get(key)
            if isinstance(value, str) and not Path(value).expanduser().is_absolute():
                derived_args[key] = str((cwd / value).resolve())
        def safe(value):
            try:
                return json.loads(_redact(value))
            except (TypeError, ValueError):
                return "[REDACTED]"

        entry = {
            "call_id": call_id,
            "tool_name": tool_name,
            "status": status,
            "arguments": safe(arguments or {}),
            "derived_arguments": safe(derived_args),
            "execution_cwd": str(cwd),
            "result": _redact(result),
            "artifact_path": artifact_path,
        }
        self.execution_records.append(entry)
        records = [
            {
                "role": "assistant",
                "tool_calls": [{"id": e["call_id"], "function": {"name": e["tool_name"], "arguments": e.get("derived_arguments", e["arguments"])}}],
            }
            for e in self.execution_records
        ]
        records += [
            {
                "role": "tool",
                "tool_call_id": e["call_id"],
                "content": e["result"],
                "status": e["status"],
                "artifact_path": e["artifact_path"],
            }
            for e in self.execution_records
        ]
        self.task_memory = ContextManager.build_memory(records)
