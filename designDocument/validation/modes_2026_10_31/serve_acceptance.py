"""Offline mode-selector acceptance server using the real Agent, HTTP and SSE.

Run from the repository root after building frontend/dist:
    python -m designDocument.validation.modes_2026_10_31.serve_acceptance

All state is isolated below .tmp/modes-acceptance-20261031. No provider,
MCP, hooks, real .env, credentials or user session directories are used.
"""

from __future__ import annotations

import argparse
import html
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from corecoder import Config
from corecoder import session as cli_sessions
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse, ToolCall
from web_backend.server import make_handler
from web_backend.service import RequestError, Service

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / ".tmp" / "modes-acceptance-20261031"


class Audit:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.mode_requests: list[dict] = []

    def append(self, collection, value):
        with self.lock:
            collection.append(value)

    def snapshot(self):
        with self.lock:
            return {"calls": list(self.calls), "mode_requests": list(self.mode_requests)}


class AcceptanceLLM(ScriptedLLM):
    def __init__(self, audit):
        super().__init__([], model="offline-mode-acceptance")
        self.audit = audit
        self.session_id = ""
        self.mode = "code"

    def chat(self, messages, tools=None, on_token=None, on_reasoning=None):
        names = [tool["function"]["name"] for tool in tools or []]
        self.audit.append(self.audit.calls, {"session_id": self.session_id, "mode": self.mode, "tools": names})
        if self.mode == "chat":
            assert not names, "Chat mode exposed tool schemas"
        last_user = max(i for i, message in enumerate(messages) if message["role"] == "user")
        prompt = messages[last_user]["content"].strip().lower()
        results = [message for message in messages[last_user + 1:] if message["role"] == "tool"]
        if not results and prompt == "write" and self.mode != "chat":
            return LLMResponse(tool_calls=[ToolCall("accept-write", "write_file", {
                "file_path": "acceptance-note.txt", "content": "Offline mode acceptance.\n",
            })])
        if not results and prompt == "read" and self.mode != "chat":
            return LLMResponse(tool_calls=[ToolCall("accept-read", "read_file", {"file_path": "welcome.txt"})])
        if not results and prompt == "probe" and self.mode == "chat":
            # Deliberately malformed provider behavior: the Agent must refuse
            # every call before permissions, hooks, or actual execution.
            return LLMResponse(tool_calls=[
                ToolCall("probe-read", "read_file", {"file_path": "welcome.txt"}),
                ToolCall("probe-write", "write_file", {"file_path": "probe.txt", "content": "must not exist"}),
                ToolCall("probe-bash", "bash", {"command": "echo acceptance-probe"}),
                ToolCall("probe-agent", "agent", {"task": "Explain a loop without tools."}),
                ToolCall("probe-mcp", "mcp_acceptance_probe", {}),
            ])
        if self.mode == "chat":
            text = "聊天验收完成：可以讨论想法或解释代码。本轮没有调用项目工具。"
            if results:
                text = "聊天模式已拒绝本轮所有意外工具调用，继续基于对话回答。"
        elif self.mode == "plan":
            text = "1. 只读了解项目。\n2. 整理实施步骤。\n3. 等待你批准后再修改文件。"
        elif results:
            text = "编程验收完成。工具结果已返回，可在变更面板检查文件。"
        else:
            text = "编程模式已就绪，可以读取项目或在授权后修改文件。"
        if prompt == "slow":
            for _ in range(60):
                if on_token:
                    on_token("等待停止验证。")
                time.sleep(0.2)
        elif on_token:
            for index in range(0, len(text), 4):
                on_token(text[index:index + 4])
                time.sleep(0.04)
        self.total_completion_tokens += len(text)
        return LLMResponse(content=text)


class AcceptanceService(Service):
    def __init__(self, workspace, store, audit):
        self.audit = audit
        super().__init__(workspace, store=store, llm_factory=lambda: AcceptanceLLM(audit), extensions=False)

    def start(self, sid, prompt):
        session = self.get(sid)
        session.agent.llm.session_id = sid
        session.agent.llm.mode = session.mode
        return super().start(sid, prompt)

    def action(self, sid, data):
        if data.get("action") not in {"mode", "plan"}:
            return super().action(sid, data)
        entry = {"session_id": sid, "action": data.get("action"), "requested_mode": data.get("mode"),
                 "before": self.get(sid).mode, "accepted": False}
        try:
            response = super().action(sid, data)
            entry.update({"accepted": True, "after": self.get(sid).mode})
            return response
        finally:
            self.audit.append(self.audit.mode_requests, entry)

    def acceptance_state(self):
        return {
            **self.audit.snapshot(),
            "sessions": [{"id": session.id, "mode": session.mode, "busy": session.busy,
                          "changes": session.changes(include_diff=False)} for session in self.sessions.values()],
            "files": sorted(path.name for path in self.workspace.iterdir() if path.is_file()),
        }


def make_acceptance_handler(service, frontend):
    base = make_handler(service, frontend)

    class Handler(base):
        def send_header(self, keyword, value):
            if keyword.lower() == "content-security-policy":
                # Test-only same-origin iframe for a narrow viewport. The
                # product server retains frame-ancestors 'none'.
                value = value.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
            super().send_header(keyword, value)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path not in {"/acceptance-state", "/acceptance-frame"}:
                return super().do_GET()
            try:
                self.validate_host()
                if url.path == "/acceptance-state":
                    return self.reply(service.acceptance_state())
                width = parse_qs(url.query).get("width", ["390"])[0]
                if width not in {"390", "1280"}:
                    raise RequestError("width must be 390 or 1280")
                title = html.escape(f"{width}px 工作台")
                payload = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
                           "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                           "<title>模式选择器离线验收</title><style>"
                           "body{margin:16px;font:14px 'Segoe UI',sans-serif;background:#e0e0e1;color:#242428}"
                           "nav{display:flex;gap:16px;margin-bottom:12px}"
                           "iframe{display:block;border:1px solid #c4c4c5;background:white;height:780px}"
                           "</style><nav><a href='/acceptance-frame?width=390'>390px 窄屏</a>"
                           "<a href='/acceptance-frame?width=1280'>1280px 桌面</a>"
                           "<a href='/'>直接打开工作台</a></nav>"
                           f"<iframe title='{title}' src='/' width='{width}'></iframe></html>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)
            except RequestError as exc:
                self.reply({"error": str(exc)}, exc.status)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Offline acceptance of the three workbench modes")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    workspace = STATE / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    welcome = workspace / "welcome.txt"
    if not welcome.exists():
        welcome.write_text("Offline acceptance fixture.\n", encoding="utf-8")
    os.environ["CORECODER_DATA_DIR"] = str(STATE / "data")
    cli_sessions.SESSIONS_DIR = STATE / "cli-sessions"
    Config.from_env = classmethod(lambda cls: Config(api_key="offline-test-only", model="offline-mode-acceptance"))
    service = AcceptanceService(workspace, STATE / "sessions", Audit())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_acceptance_handler(service, ROOT / "frontend" / "dist"))
    print(f"Offline mode acceptance: http://127.0.0.1:{args.port}", flush=True)
    print(f"Narrow viewport: http://127.0.0.1:{args.port}/acceptance-frame?width=390", flush=True)
    print(f"Read-only audit: http://127.0.0.1:{args.port}/acceptance-state", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for session in service.sessions.values():
            service.stop(session.id)
        server.server_close()


if __name__ == "__main__":
    main()
