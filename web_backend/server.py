"""Loopback-only JSON/SSE API and built frontend server (stdlib only)."""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import queue
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .service import RequestError, Service

ROOT = Path(__file__).resolve().parents[1]


def make_handler(service: Service, frontend: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            # Do not log prompts, request bodies, credentials or tool arguments.
            pass

        def reply(self, value, status=200):
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def validate_host(self):
            hostname = urlsplit("http://" + self.headers.get("Host", "")).hostname
            if hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise RequestError("此服务仅供本机访问。", 403)

        def body(self):
            self.validate_host()
            origin = self.headers.get("Origin")
            actual = f"http://{self.headers.get('Host')}"
            if origin and origin not in {actual, "http://127.0.0.1:5173", "http://localhost:5173"}:
                raise RequestError("不允许此网页来源。", 403)
            token = self.headers.get("X-CoreCoder-Token", "")
            if not hmac.compare_digest(token, service.token):
                raise RequestError("网页连接已过期，请刷新后重试。", 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise RequestError("请求必须使用 JSON。", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_000_000:
                    raise RequestError("请求大小无效。", 413)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise RequestError("请求必须是 JSON 对象。")
                return data
            except (ValueError, UnicodeDecodeError) as exc:
                raise RequestError("请求 JSON 无效。") from exc

        def do_GET(self):
            try:
                self.validate_host()
                url = urlsplit(self.path)
                if url.path == "/api/bootstrap":
                    return self.reply(service.bootstrap())
                if url.path == "/api/sessions":
                    return self.reply(service.summaries())
                if url.path == "/api/saved-sessions":
                    return self.reply(service.saved_sessions())
                if url.path == "/api/session":
                    return self.reply(service.get(parse_qs(url.query).get("id", [""])[0]).snapshot())
                if url.path == "/api/changes":
                    return self.reply(service.get(parse_qs(url.query).get("id", [""])[0]).changes())
                if url.path.startswith("/api/"):
                    raise RequestError("接口不存在。", 404)
                requested = (frontend / unquote(url.path).lstrip("/")).resolve()
                if not requested.is_relative_to(frontend.resolve()):
                    raise RequestError("无效路径。", 403)
                if not requested.is_file():
                    requested = frontend / "index.html"
                if not requested.exists():
                    raise RequestError("前端尚未构建。请在 frontend 目录执行 npm install 和 npm run build。", 503)
                payload = requested.read_bytes()
                self.send_response(200)
                mime = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
                self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
                self.end_headers()
                self.wfile.write(payload)
            except RequestError as exc:
                self.reply({"error": str(exc)}, exc.status)
            except (OSError, ValueError, TypeError, AttributeError):
                self.reply({"error": "无法读取本地数据。"}, 500)

        def do_POST(self):
            try:
                data = self.body()
                path = urlsplit(self.path).path
                if path == "/api/sessions":
                    return self.reply(service.create(), 201)
                if path == "/api/action":
                    return self.reply(service.action(data.get("id", ""), data))
                if path == "/api/approve":
                    service.approve(data.get("id", ""), data.get("permission_id", ""), data.get("answer", "deny"))
                    return self.reply({"ok": True})
                if path == "/api/stop":
                    service.stop(data.get("id", ""))
                    return self.reply({"ok": True})
                if path != "/api/chat":
                    raise RequestError("接口不存在。", 404)
                sid = data.get("id", "")
                prompt = data.get("prompt", "")
                if not isinstance(prompt, str):
                    raise RequestError("任务必须是文本。")
                events = service.start(sid, prompt)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.close_connection = True
                try:
                    while True:
                        try:
                            event = events.get(timeout=10)
                            payload = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                        except queue.Empty:
                            payload = ": heartbeat\n\n"
                            event = {}
                        self.wfile.write(payload.encode("utf-8"))
                        self.wfile.flush()
                        if event.get("type") == "done":
                            break
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    service.stop(sid)
            except RequestError as exc:
                self.reply({"error": str(exc)}, exc.status)
            except (OSError, ValueError, TypeError, AttributeError):
                self.reply({"error": "操作失败，请查看服务终端或重试。"}, 500)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="CoreCoder local web workbench")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--open", action="store_true", help="Open the workbench in your browser")
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error("workspace must be an existing directory")
    service = Service(args.workspace)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(service, ROOT / "frontend" / "dist"))
    url = f"http://127.0.0.1:{args.port}"
    print(f"CoreCoder Workbench: {url}", flush=True)
    print(f"Workspace: {service.workspace}", flush=True)
    print("Press Ctrl+C to stop. API key stays on the backend.", flush=True)
    if args.open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        for session in service.sessions.values():
            service.stop(session.id)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
