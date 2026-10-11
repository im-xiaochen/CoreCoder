"""Bridge the existing agent to a local browser, without importing the CLI."""

from __future__ import annotations

import base64
import difflib
import hashlib
import inspect
import json
import queue
import threading
import time
import uuid
from pathlib import Path

from corecoder import Agent, Config, SessionState
from corecoder.context import estimate_tokens, output_reserve_for
from corecoder.hooks import load_hooks
from corecoder.llm import LLM, LiteLLM
from corecoder.mcp import load_mcp_tools
from corecoder.permissions import Permission
from corecoder.session import list_sessions, load_session, save_session
from corecoder.tools.agent import AgentTool
from corecoder.tools.base import Tool, ToolOutcome
from corecoder.tools.bash import BashTool
from corecoder.tools.edit import EditFileTool
from corecoder.tools.glob_tool import GlobTool
from corecoder.tools.grep import GrepTool
from corecoder.tools.read import ReadFileTool
from corecoder.tools.todo import TodoWriteTool
from corecoder.tools.write import WriteFileTool


class RequestError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class TurnCancelled(KeyboardInterrupt):
    """Stop at an agent boundary; running shell processes finish first."""


def digest(value: bytes | None) -> str | None:
    return hashlib.sha256(value).hexdigest() if value is not None else None


def exposed_file(path: Path, workspace: Path) -> bool:
    path = path.resolve()
    if not path.is_relative_to(workspace):
        return False
    protected_env = path.name == ".env" or (
        path.name.startswith(".env.") and path.name not in {".env.example", ".env.sample", ".env.template"}
    )
    return not protected_env and ".git" not in path.relative_to(workspace).parts


class WorkspaceGrepTool(GrepTool):
    def __init__(self, workspace: Path, state: SessionState | None = None):
        super().__init__(state=state)
        self.workspace = workspace

    def _walk(self, root: Path, include: str | None):
        files, truncated = super()._walk(root, include)
        return [p for p in files if exposed_file(p, self.workspace)], truncated

    def clone_for_state(self, state):
        return type(self)(self.workspace, state=state)


class ScopedTool(Tool):
    def __init__(self, tool: Tool, session: WebSession):
        self.tool, self.session = tool, session
        self.state = getattr(tool, "state", None)
        self.name, self.description, self.parameters = tool.name, tool.description, tool.parameters

    def execute(self, **kwargs):
        inspect.signature(self.tool.execute).bind(**kwargs)
        key = "file_path" if "file_path" in kwargs else "path"
        raw = kwargs.get(key, ".")
        if not isinstance(raw, str):
            return "Error: path must be a string"
        path = self.state.resolve_path(raw)
        if not path.is_relative_to(self.session.service.workspace):
            return "Error: file tools are limited to the selected workspace"
        if not exposed_file(path, self.session.service.workspace):
            return "Error: credential and Git internal files are not exposed by the web file tools"
        kwargs[key] = str(path)
        mutating = self.name in {"write_file", "edit_file"}
        if mutating and str(path) not in self.session.baselines:
            prior = path.read_bytes() if path.is_file() else None
            if prior is not None and len(prior) > 2_000_000:
                return "Error: web file editing is limited to files smaller than 2 MB"
            self.session.baselines[str(path)] = prior
        result = self.tool.execute(**kwargs)
        if mutating and not str(result).startswith("Error:"):
            self.session.undo_hashes.append(digest(path.read_bytes() if path.exists() else None))
        return result

    def clone_for_state(self, state):
        clone = getattr(self.tool, "clone_for_state", None)
        if clone is None:
            if getattr(self.tool, "state", None) is not None:
                raise ValueError(f"stateful tool {self.name!r} cannot be cloned")
            child = self.tool
        else:
            child = clone(state)
        return type(self)(child, self.session)


class BrowserAgent(Agent):
    def __init__(self, session: WebSession, **kwargs):
        self.session = session
        super().__init__(**kwargs)

    def _full_messages(self):
        self.session.check_cancelled()
        return super()._full_messages()

    def _pre_hooks(self, tc):
        self.session.check_cancelled()
        if not isinstance(tc.arguments, dict):
            return "Error: tool arguments must be a JSON object"
        return super()._pre_hooks(tc)

    def _run_tool_call(self, tc, on_tool=None):
        self.session.check_cancelled()
        def notify_tool(name, arguments):
            # Agent invokes on_tool only after argument/schema validation. Keep
            # the browser's visible start event behind the same gate so an
            # invalid call cannot look like an attempted side effect.
            self.session.emit("tool_start", {"id": tc.id, "name": name, "arguments": arguments})
            if on_tool is not None:
                return on_tool(name, arguments)

        outcome = super()._run_tool_call(tc, notify_tool)
        if not isinstance(outcome, ToolOutcome):
            outcome = ToolOutcome(tc.id, tc.name, "failed", str(outcome), "execute")
        self.session.emit(
            "tool_result",
            {
                "id": tc.id,
                "name": tc.name,
                "result": outcome.content[:24000],
                "status": outcome.status,
                "stage": outcome.stage,
                "warning": outcome.warning,
                # Preserve the old field for consumers that only understand
                # the pre-R07 refusal event shape.
                "refused": outcome.status == "refused",
            },
        )
        return outcome

    def _permit(self, tc):
        return super()._permit(tc)

    def _exec_tools_parallel(self, tool_calls, on_tool=None, return_outcomes=False):
        # Browser sessions remain serial, while Agent owns validation and the
        # exactly-once outcome lifecycle shared by single and batch calls.
        return super()._exec_tools_parallel(tool_calls, on_tool, return_outcomes=return_outcomes)


class BrowserAgentTool(AgentTool):
    def execute(self, task: str) -> str:
        parent = self._parent_agent
        if parent is None:
            return "Error: agent tool not initialized"
        # Keep the original sub-agent feature, with browser cancellation,
        # permission events and its own task list at every agent boundary.
        child_state = parent.state.fork()
        child_tools = []
        for tool in parent.tools:
            if tool.name == "agent":
                continue
            if isinstance(tool, TodoWriteTool):
                child_tools.append(TodoWriteTool(state=child_state))
            elif isinstance(tool, AgentTool):
                continue
            else:
                clone = getattr(tool, "clone_for_state", None)
                if clone is not None:
                    child_tools.append(clone(child_state))
                elif getattr(tool, "stateless", False):
                    child_tools.append(tool)
                else:
                    return f"Sub-agent error: custom tool {tool.name!r} must declare stateless=True or implement clone_for_state(state)"
        sub = BrowserAgent(
            parent.session,
            llm=parent.llm,
            state=child_state,
            tools=child_tools,
            max_context_tokens=parent.context.max_tokens,
            max_rounds=20,
            permission=parent.permission,
            hooks=parent.hooks,
        )
        try:
            result = sub.chat(task)
            if len(result) > 5000:
                result = result[:4500] + "\n... (sub-agent output truncated)"
            return f"[Sub-agent completed]\n{result}"
        except Exception as exc:  # noqa: BLE001 - matches the existing agent tool boundary
            return f"Sub-agent error: {exc}"
        finally:
            pass


class WebSession:
    def __init__(self, service: Service, data: dict | None = None):
        data = data or {}
        self.service = service
        self.id = data.get("id", uuid.uuid4().hex)
        self.title = data.get("title", "新对话")
        self.updated_at = data.get("updated_at", time.time())
        self.cwd = data.get("cwd", str(service.workspace))
        if not Path(self.cwd).is_dir() or not Path(self.cwd).resolve().is_relative_to(service.workspace):
            self.cwd = str(service.workspace)
        self.state = SessionState(service.workspace, self.cwd, session_id=self.id)
        self.timeline = data.get("timeline", [])
        # Derived UI metadata is kept outside canonical provider messages so
        # repeated call IDs can still be matched by chronological occurrence.
        self.saved_tool_outcomes = data.get("tool_outcomes", [])
        self.busy = False
        self.cancel = threading.Event()
        self.approval_ready = threading.Event()
        self.pending_permission: dict | None = None
        self.approval_answer = "deny"
        self.events: queue.Queue | None = None
        self.baselines = {p: base64.b64decode(b) if b is not None else None for p, b in data.get("baselines", {}).items()}
        self.undo_hashes = data.get("undo_hashes", [])
        permission = Permission(ask=self.ask)
        permission._always = set(data.get("allowed_tools", []))
        tools = [
            ScopedTool(t, self)
            for t in [
                ReadFileTool(state=self.state),
                WriteFileTool(state=self.state),
                EditFileTool(state=self.state),
                GlobTool(state=self.state),
                WorkspaceGrepTool(service.workspace, state=self.state),
            ]
        ]
        tools += [BashTool(state=self.state), TodoWriteTool(state=self.state), BrowserAgentTool(), *service.mcp_tools]
        self.agent = BrowserAgent(
            self,
            llm=service.make_llm(),
            state=self.state,
            tools=tools,
            max_context_tokens=service.config.max_context_tokens,
            permission=permission,
            hooks=service.hooks,
        )
        self.agent.messages = data.get("messages", [])
        self.agent.llm.model = data.get("model", service.config.model)
        old_prompt = data.get("prompt_tokens", 0)
        old_completion = data.get("completion_tokens", 0)
        if old_prompt or old_completion:
            importer = getattr(self.agent.llm, "import_legacy_usage", None)
            if importer is not None:
                importer(old_prompt, old_completion)
            else:
                self.agent.llm.total_prompt_tokens = old_prompt
                self.agent.llm.total_completion_tokens = old_completion
        self.agent.plan_mode = bool(data.get("plan_mode", False))
        self.agent._todo.execute(data.get("tasks", []))

    def check_cancelled(self):
        if self.cancel.is_set():
            raise TurnCancelled()

    def emit(self, event: str, data: dict):
        entry = {"type": event, **data, "at": time.time()}
        if event not in {"token", "reasoning", "snapshot", "done"}:
            self.timeline.append(entry)
        if self.events is not None:
            self.events.put(entry)

    def ask(self, name: str, arguments: dict) -> str:
        self.check_cancelled()
        self.approval_ready.clear()
        self.pending_permission = {"id": uuid.uuid4().hex, "name": name, "arguments": arguments}
        self.emit("permission", self.pending_permission)
        deadline = time.monotonic() + 300
        while not self.approval_ready.wait(0.25):
            self.check_cancelled()
            if time.monotonic() > deadline:
                self.pending_permission = None
                self.emit("notice", {"message": "授权等待已超时，本次调用已拒绝。"})
                return "deny"
        self.pending_permission = None
        self.check_cancelled()
        return self.approval_answer

    def summary(self):
        return {
            "id": self.id,
            "title": self.title,
            "updated_at": self.updated_at,
            "model": self.agent.llm.model,
            "busy": self.busy,
            "message_count": sum(m.get("role") == "user" for m in self.agent.messages),
        }

    def snapshot(self):
        llm = self.agent.llm
        cost_summary = getattr(llm, "cost_summary", None)
        if cost_summary is None:
            cost_payload = {
                "known_cost": None,
                "unknown_prompt_tokens": 0,
                "unknown_completion_tokens": 0,
                "unknown_requests": 0,
                "missing_usage_requests": 0,
            }
        else:
            cost_payload = cost_summary.as_dict()
            # Decimal is deliberately kept in the ledger, but the Web API is
            # JSON and must preserve precision without relying on float.
            cost_payload["known_cost"] = str(cost_payload["known_cost"])
        return {
            **self.summary(),
            "messages": self.agent.messages,
            "timeline": self.timeline[-200:],
            "tool_outcomes": [
                *self.saved_tool_outcomes,
                *[
                    {"call_id": entry.get("call_id"), "status": entry.get("status")}
                    for entry in self.state.execution_records
                ],
            ],
            "cwd": self.cwd,
            "plan_mode": self.agent.plan_mode,
            "tasks": self.agent._todo._tasks,
            "pending_permission": self.pending_permission,
            "allowed_tools": sorted(self.agent.permission._always),
            "usage": {
                "prompt": llm.total_prompt_tokens,
                "completion": llm.total_completion_tokens,
                "cost": llm.estimated_cost,
                "cost_summary": cost_payload,
                "context": estimate_tokens(self.agent.messages),
                "max_context": self.agent.context.max_tokens,
            },
            "changes": self.changes(include_diff=False),
            "undo_count": len(self.undo_hashes),
        }

    def changes(self, include_diff=True):
        changes = []
        for name, prior in self.baselines.items():
            path = Path(name)
            if not path.is_relative_to(self.service.workspace):
                continue
            try:
                current = path.read_bytes() if path.is_file() else None
            except OSError:
                continue
            if current == prior:
                continue
            old = (prior or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
            new = (current or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
            relative = path.relative_to(self.service.workspace).as_posix()
            lines = list(difflib.unified_diff(old, new, fromfile=f"a/{relative}", tofile=f"b/{relative}"))
            change = {
                "path": relative,
                "status": "added" if prior is None else "deleted" if current is None else "modified",
                "additions": sum(s.startswith("+") and not s.startswith("+++") for s in lines),
                "deletions": sum(s.startswith("-") and not s.startswith("---") for s in lines),
            }
            if include_diff:
                change["diff"] = "".join(lines)[:50000]
            changes.append(change)
        return changes

    def persist(self):
        self.service.store.mkdir(parents=True, exist_ok=True)
        data = {
            **self.snapshot(),
            "workspace": str(self.service.workspace),
            "prompt_tokens": self.agent.llm.total_prompt_tokens,
            "completion_tokens": self.agent.llm.total_completion_tokens,
            "baselines": {p: base64.b64encode(b).decode("ascii") if b is not None else None for p, b in self.baselines.items()},
            "undo_hashes": self.undo_hashes,
        }
        target = self.service.store / f"{self.id}.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)


class Service:
    def __init__(self, workspace: Path, store: Path | None = None, llm_factory=None, extensions=True):
        self.workspace = workspace.resolve()
        self.config = Config.from_env()
        self.store = store or Path.home() / ".corecoder" / "web" / hashlib.sha256(str(self.workspace).encode()).hexdigest()[:16]
        self._llm_factory = llm_factory
        self.mcp_tools = load_mcp_tools() if extensions else []
        self.hooks = load_hooks() if extensions else None
        self.sessions: dict[str, WebSession] = {}
        self.execution = threading.Lock()
        self.lock = threading.RLock()
        self.token = uuid.uuid4().hex + uuid.uuid4().hex
        if self.store.exists():
            for path in self.store.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("workspace") != str(self.workspace) or not isinstance(data.get("messages"), list):
                        continue
                    if not isinstance(data.get("id"), str) or not data["id"].isalnum():
                        continue
                    session = WebSession(self, data)
                    self.sessions[session.id] = session
                except (ValueError, OSError, TypeError, KeyError, AttributeError):
                    continue

    def make_llm(self):
        if self._llm_factory:
            return self._llm_factory()
        cls = LiteLLM if self.config.provider == "litellm" else LLM
        # A missing key can still render the workbench and explain how to set it.
        return cls(
            model=self.config.model,
            api_key=self.config.api_key or "not-configured",
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

    def bootstrap(self):
        from corecoder import __version__

        return {
            "token": self.token,
            "version": __version__,
            "workspace": str(self.workspace),
            "project": self.workspace.name,
            "model": self.config.model,
            "provider": self.config.provider,
            "base_url": self.config.base_url,
            "key_configured": bool(self.config.api_key),
            "tools": ["read_file", "write_file", "edit_file", "bash", "glob", "grep", "todo_write", "agent"],
            "mcp_tools": [t.name for t in self.mcp_tools],
            "hooks": {"pre": len(self.hooks.pre) if self.hooks else 0, "post": len(self.hooks.post) if self.hooks else 0},
            "sessions": self.summaries(),
        }

    def summaries(self):
        with self.lock:
            return sorted((s.summary() for s in self.sessions.values()), key=lambda s: s["updated_at"], reverse=True)

    def get(self, sid):
        with self.lock:
            if sid not in self.sessions:
                raise RequestError("对话不存在，请新建对话。", 404)
            return self.sessions[sid]

    def create(self):
        with self.lock:
            if len(self.sessions) >= 100:
                raise RequestError("已达到 100 个本地对话，请先删除不需要的对话。")
            session = WebSession(self)
            self.sessions[session.id] = session
            session.persist()
            return session.snapshot()

    def bind_checkpoints(self, session):
        # CoreCoder tools use the session-owned store directly.  Keep this
        # method as a compatibility no-op for embedders that still call it.
        return session.state.checkpoint_store

    def start(self, sid: str, prompt: str):
        session = self.get(sid)
        if not prompt.strip() or len(prompt) > 100_000:
            raise RequestError("请输入 1–100000 个字符的任务。")
        if not self.config.api_key and not self._llm_factory:
            raise RequestError("请在项目 .env 中填写 API Key，随后重启网页服务。")
        if not self.execution.acquire(blocking=False):
            raise RequestError("另一个任务仍在运行，请完成或停止后重试。", 409)
        session.busy = True
        session.cancel.clear()
        session.events = queue.Queue()
        events = session.events
        if session.title == "新对话":
            session.title = prompt.strip().replace("\n", " ")[:28]

        def run():
            try:
                session.agent.state.cwd = Path(session.cwd).resolve()
                session.emit("started", {"session_id": sid})
                result = session.agent.chat_result(
                    prompt,
                    on_token=lambda token: (session.check_cancelled(), session.emit("token", {"text": token})),
                    on_reasoning=lambda token: (session.check_cancelled(), session.emit("reasoning", {"text": token})),
                )
                result_payload = {
                    "status": result.status,
                    "finish_reason": result.finish_reason,
                    "rounds": result.rounds,
                    "tool_errors": result.tool_errors,
                    "usage": result.usage,
                    "text": result.text,
                }
                session.emit("run_result", result_payload)
                if result.status == "completed":
                    session.emit("complete", {"text": result.text, **result_payload})
                elif result.status == "max_rounds":
                    session.emit(
                        "error",
                        {"message": "已达到工具轮次上限。你可以继续发送任务。", **result_payload},
                    )
                elif result.status == "cancelled":
                    session.emit("notice", {"message": "已停止后续步骤。", **result_payload})
                else:
                    message = result.tool_errors[-1]["message"] if result.tool_errors else "Agent 运行失败。"
                    session.emit("error", {"message": str(message)[:1500], **result_payload})
            except (TurnCancelled, KeyboardInterrupt):
                # Guard the protocol even if cancellation came from a callback.
                self.repair_pending(session.agent)
                session.emit("notice", {"message": "已停止后续步骤。已完成的文件修改仍然保留。"})
            except Exception as exc:  # noqa: BLE001 - network/tool boundary
                self.repair_pending(session.agent)
                message = str(exc)
                if self.config.api_key:
                    message = message.replace(self.config.api_key, "[redacted]")
                session.emit("error", {"message": message[:1500]})
            finally:
                session.cwd = str(session.agent.state.cwd)
                session.pending_permission = None
                session.busy = False
                session.updated_at = time.time()
                try:
                    session.persist()
                except OSError:
                    session.emit("error", {"message": "对话已结束，但本地存档写入失败。"})
                try:
                    events.put({"type": "snapshot", "session": session.snapshot()})
                finally:
                    events.put({"type": "done"})
                    session.events = None
                    self.execution.release()

        threading.Thread(target=run, daemon=True).start()
        return events

    @staticmethod
    def repair_pending(agent):
        answered = {m.get("tool_call_id") for m in agent.messages if m.get("role") == "tool"}
        missing = [tc["id"] for m in agent.messages for tc in m.get("tool_calls", []) if tc["id"] not in answered]
        for call_id in missing:
            agent.messages.append({"role": "tool", "tool_call_id": call_id, "content": "[interrupted]"})

    def approve(self, sid, permission_id, answer):
        session = self.get(sid)
        if answer not in {"once", "always", "deny"}:
            raise RequestError("无效授权选项。")
        if not session.pending_permission or session.pending_permission["id"] != permission_id:
            raise RequestError("此授权请求已结束。", 409)
        session.approval_answer = answer
        session.approval_ready.set()

    def stop(self, sid):
        session = self.get(sid)
        session.cancel.set()
        session.approval_ready.set()

    def action(self, sid, data):
        session = self.get(sid)
        if not self.execution.acquire(blocking=False):
            raise RequestError("任务执行期间不能修改会话设置。", 409)
        try:
            action = data.get("action")
            notice = ""
            if action == "plan":
                session.agent.plan_mode = bool(data.get("enabled"))
            elif action == "model":
                model = str(data.get("model", "")).strip()
                if not model or len(model) > 200:
                    raise RequestError("请输入有效模型名称。")
                session.agent.llm.model = model
            elif action == "reset":
                session.agent.reset()
                session.timeline.clear()
                session.saved_tool_outcomes = []
                notice = "对话上下文已清空，文件修改保留。"
            elif action == "save":
                sid_saved = save_session(session.agent.messages, session.agent.llm.model, f"web_{session.id}")
                notice = f"已保存，可在 CLI 中使用 corecoder -r {sid_saved} 恢复。"
            elif action == "compact":
                before = estimate_tokens(session.agent.messages)
                full = session.agent._full_messages()
                prepared = session.agent.context.prepare_request(
                    full[1:],
                    system=full[0]["content"],
                    tools=session.agent._tool_schemas(),
                    output_reserve=output_reserve_for(session.agent.llm, 4096),
                    artifact_dir=session.state.artifact_dir,
                    llm=session.agent.llm,
                )
                if not prepared.budget_ok:
                    raise RequestError("上下文压缩后仍超过模型窗口，请先缩短任务。", 409)
                session.agent.messages[:] = prepared.messages
                after = estimate_tokens(session.agent.messages)
                notice = f"上下文：{before:,} → {after:,} 估算 tokens。"
            elif action == "undo":
                if not session.state.checkpoint_store.pending():
                    raise RequestError("此对话没有可撤销的文件修改。")
                preview = session.state.checkpoint_store.preview()
                if not preview:
                    raise RequestError("此对话没有可撤销的文件修改。")
                path = Path(preview["path"]).resolve()
                if not path.is_relative_to(self.workspace):
                    raise RequestError("撤销目标不在当前工作区。")
                current = path.read_bytes() if path.is_file() else None
                expected = preview.get("expected_sha256")
                # The durable checkpoint is authoritative; retain the session
                # execution hash as a compatibility witness for stores written
                # by an earlier web process.
                witnesses = {expected, *(session.undo_hashes or [])}
                if digest(current) not in witnesses:
                    raise RequestError("文件在修改后发生变化，已保留当前内容，无法直接撤销。", 409)
                notice = session.state.checkpoint_store.undo()
                if str(notice).lower().startswith(("conflict", "error")):
                    raise RequestError(str(notice), 409)
                if session.undo_hashes:
                    session.undo_hashes.pop()
            elif action == "revoke":
                session.agent.permission._always.clear()
                notice = "已清除此对话中持续允许的工具。"
            elif action == "import":
                loaded = load_session(str(data.get("saved_id", "")))
                if not loaded or not isinstance(loaded[0], list):
                    raise RequestError("该 CLI 会话无法读取。")
                if session.agent.messages:
                    raise RequestError("请在空白新对话中恢复存档，避免覆盖当前上下文。", 409)
                session.agent.messages, session.agent.llm.model = loaded
                self.repair_pending(session.agent)
                session.title = next((str(m.get("content", ""))[:28] for m in loaded[0] if m.get("role") == "user"), "恢复的对话")
                notice = "已恢复 CLI 对话。"
            else:
                raise RequestError("不支持此操作。")
            session.updated_at = time.time()
            session.persist()
            return {"session": session.snapshot(), "notice": notice, "sessions": self.summaries()}
        finally:
            self.execution.release()

    @staticmethod
    def saved_sessions():
        return list_sessions()
