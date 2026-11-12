"""Read-only delegated agents and bounded teams."""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from ..budget import BudgetExceeded
from ..state import SessionState
from ..team import run_team, validate_tasks
from .base import Tool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .repo_map import RepoMapTool
from .todo import TodoWriteTool

_ARTIFACT_LOCK = threading.RLock()

def _prepare_artifact_dir(state):
    directory = Path(state.artifact_dir) / "team"
    with _ARTIFACT_LOCK:
        for ancestor in (directory, *directory.parents):
            if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
                raise OSError("artifact directory must not contain links")
        if directory.resolve() != directory.absolute():
            raise OSError("artifact directory is not a managed path")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or directory.resolve() != directory.absolute():
            raise OSError("artifact directory is not a managed path")
    return directory


class AgentTool(Tool):
    name = "agent"
    description = "Delegate a task or a team of independent read-only tasks."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "minLength": 1},
            "tasks": {
                "type": "array", "minItems": 1, "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "task": {"type": "string", "minLength": 1},
                        "depends_on": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                    },
                    "required": ["name", "task"], "additionalProperties": False,
                },
            },
        },
        "oneOf": [{"required": ["task"]}, {"required": ["tasks"]}],
    }

    def __init__(self):
        self._parent_agent = None

    def bind_parent(self, parent):
        if self._parent_agent is not None and self._parent_agent is not parent:
            raise ValueError("agent tool is already bound to another parent Agent")
        self._parent_agent = parent

    def clone_for_state(self, state: SessionState):
        return type(self)()

    def _child_tools(self, state):
        trusted = (ReadFileTool, GlobTool, GrepTool, RepoMapTool, TodoWriteTool)
        return [tool.clone_for_state(state) for tool in self._parent_agent.tools if type(tool) in trusted]

    def _make_child(self, state, tools):
        from ..agent import Agent
        parent = self._parent_agent
        return Agent(llm=parent.llm, state=state, tools=tools,
                     max_context_tokens=parent.context.max_tokens, max_rounds=20,
                     permission=parent.permission, hooks=None)

    def _run_single(self, task):
        parent = self._parent_agent
        state = parent.state.fork()
        tools = []
        for tool in parent.tools:
            if isinstance(tool, AgentTool):
                continue
            clone = getattr(tool, "clone_for_state", None)
            if clone is not None:
                tools.append(clone(state))
            elif getattr(tool, "stateless", False):
                tools.append(tool)
            else:
                return f"Sub-agent error: custom tool {tool.name!r} cannot be cloned"
        sub = self._make_child(state, tools)
        sub.hooks = parent.hooks
        result = sub.chat_result(task)
        if result.finish_reason == "budget_exceeded":
            raise result.error if isinstance(result.error, BudgetExceeded) else BudgetExceeded("child budget exceeded")
        if result.status == "completed":
            text = result.text or ""
            if len(text) > 5000:
                text = text[:4500] + "\n... (sub-agent output truncated)"
            return f"[Sub-agent completed]\n{text}"
        return json.dumps({"status": result.status, "reason": result.finish_reason,
                           "summary": (result.text or "")[:4500], "usage": result.usage},
                          ensure_ascii=False)

    def _run_member(self, item):
        parent = self._parent_agent
        child_state = parent.state.fork()
        sub = self._make_child(child_state, self._child_tools(child_state))
        result = sub.chat_result(item["task"])
        member_id = item.get("_id") or uuid4().hex
        evidence = []
        artifact_error = None
        temporary = None
        try:
            directory = _prepare_artifact_dir(parent.state)
            target = directory / f"{member_id}.txt"
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
                temporary = stream.name
                stream.write(result.text or "")
            os.link(temporary, target)
            evidence.append(str(target))
        except OSError as exc:
            artifact_error = str(exc)
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return {
            "name": item["name"], "id": member_id, "status": result.status,
            "reason": result.finish_reason, "summary": (result.text or "")[:4500],
            "evidence": evidence, "usage": result.usage, "artifact_error": artifact_error,
        }

    def execute(self, task=None, tasks=None) -> str:
        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"
        if (task is None) == (tasks is None):
            return "Error: provide exactly one of task or tasks"
        if tasks is None:
            if not isinstance(task, str) or not task.strip():
                return "Error: task must be nonempty"
            return self._run_single(task)
        try:
            validate_tasks(tasks)
        except ValueError as exc:
            return f"Error: {exc}"
        try:
            _prepare_artifact_dir(self._parent_agent.state)
        except OSError:
            pass  # Each member reports its own identifiable artifact failure.
        return json.dumps({"tasks": run_team(tasks, self._run_member, self._team_status, self._team_cancel)},
                          ensure_ascii=False)

    def _team_status(self, event):
        pass

    def _team_cancel(self):
        pass
