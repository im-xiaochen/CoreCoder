"""Loopback-only JSON/SSE API and built frontend server (stdlib only)."""

from __future__ import annotations

import argparse
import errno
import hmac
import json
import mimetypes
import queue
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from corecoder.project_checks import CheckError

from .service import RequestError, Service

ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_FRONTEND = ROOT / "frontend" / "dist"
PACKAGED_FRONTEND = Path(__file__).resolve().parent / "static"


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
                if url.path == "/api/run":
                    rid = parse_qs(url.query).get("id", [""])[0]
                    try:
                        return self.reply(service.runs.view(rid))
                    except KeyError:
                        raise RequestError("任务不存在。", 404)
                if url.path == "/api/run/events":
                    query = parse_qs(url.query)
                    rid = query.get("id", [""])[0]
                    try:
                        run = service.runs.view(rid)
                        after = int(query.get("after", [self.headers.get("Last-Event-ID", "0")])[0])
                        if after < 0 or after > run['last_seq']:
                            raise ValueError()
                    except KeyError:
                        raise RequestError("任务不存在。", 404)
                    except ValueError:
                        raise RequestError("事件游标无效。", 400)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    try:
                        while True:
                            with service.runs.changed:
                                gap, events, status = service.runs.events_after(rid, after)
                                view = service.runs.view(rid) if gap else None
                                if not events and not gap and (status not in {'completed', 'cancelled', 'failed', 'interrupted'} or not service.runs.runs[rid]['events'] or service.runs.runs[rid]['events'][-1]['type'] != 'done'):
                                    service.runs.changed.wait(timeout=10)
                                    continue
                            if view is not None:
                                session = service.get(view['session_id'])
                                events = [{'type': 'reset', 'run_id': rid, 'seq': view['last_seq'],
                                           'session': session.snapshot(), 'text': view['text'],
                                           'reasoning': view['reasoning'], 'status': view['status'],
                                           'team_tasks': view.get('team_tasks', {})}]
                            for event in events:
                                payload = f"id: {event['seq']}\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                                self.wfile.write(payload.encode('utf-8'))
                                self.wfile.flush()
                                after = event['seq']
                            if any(e['type'] == 'done' for e in events) or (not events and status in {'completed', 'cancelled', 'failed', 'interrupted'}):
                                break
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass
                    return
                if url.path == "/api/sessions":
                    return self.reply(service.summaries())
                if url.path == "/api/saved-sessions":
                    return self.reply(service.saved_sessions())
                if url.path == "/api/skills":
                    return self.reply(service.skills(parse_qs(url.query).get("id", [""])[0]))
                if url.path == "/api/session":
                    return self.reply(service.get(parse_qs(url.query).get("id", [""])[0]).snapshot())
                if url.path == "/api/changes":
                    return self.reply(service.get(parse_qs(url.query).get("id", [""])[0]).changes())
                if url.path in ("/api/git/status", "/api/git/diff"):
                    query = parse_qs(url.query)
                    return self.reply(service.git_action(query.get('id', [''])[0], url.path.rsplit('/', 1)[-1], {'path': query.get('path', [''])[0], 'staged': query.get('staged', [''])[0] == 'true'}))
                if url.path == '/api/checks/config':
                    session = service.get(parse_qs(url.query).get('id', [''])[0])
                    if session.mode == 'chat': raise RequestError('聊天模式不读取项目检查。', 403)
                    return self.reply(service.project_checks.inspect())
                if url.path == '/api/checks/result':
                    return self.reply(service.project_checks.get(parse_qs(url.query).get('result_id', [''])[0]))
                if url.path == '/api/checks/history':
                    sid = parse_qs(url.query).get('id', [''])[0]
                    service.get(sid)
                    return self.reply(service.project_checks.history(sid))
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
                # Windows MIME registrations vary. ES modules require a JavaScript MIME type.
                mime = {".js": "text/javascript", ".css": "text/css"}.get(requested.suffix)
                if mime is None:
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
            except CheckError as exc:
                self.reply({'error': str(exc)}, 400)
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
                if path in ("/api/git/stage", "/api/git/commit"):
                    return self.reply(service.git_action(data.get('id', ''), path.rsplit('/', 1)[-1], data))
                if path == '/api/checks/start':
                    return self.reply(service.checks_start(data.get('id', ''), data), 201)
                if path == "/api/runs":
                    sid = data.get("id", "")
                    prompt = data.get("prompt", "")
                    if not isinstance(prompt, str):
                        raise RequestError("任务必须是文本。")
                    rid = service.start(sid, prompt, legacy_events=False, budget=data.get("budget"))
                    return self.reply(service.runs.view(rid), 201)
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
                    session = service.get(sid)
                    if session.events is events:
                        session.events = None
            except RequestError as exc:
                self.reply({"error": str(exc)}, exc.status)
            except CheckError as exc:
                self.reply({'error': str(exc)}, 400)
            except (OSError, ValueError, TypeError, AttributeError):
                self.reply({"error": "操作失败，请查看服务终端或重试。"}, 500)

    return Handler


def _serve(argv, *, prog, workspace, frontend, require_assets):
    parser = argparse.ArgumentParser(prog=prog, description="CoreCoder local web workbench")
    parser.add_argument("--port", type=int, default=8765, help="Loopback port (0 selects an available port)")
    parser.add_argument("--workspace", type=Path, default=workspace, help="Directory to open in the workbench")
    parser.add_argument("--open", action="store_true", help="Open the workbench in your browser")
    args = parser.parse_args(argv)
    if not args.workspace.is_dir():
        parser.error("workspace must be an existing directory")
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if require_assets and not (frontend / "index.html").is_file():
        parser.error("workbench assets are missing. Install a built CoreCoder distribution or run npm ci and npm run build in frontend/")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), BaseHTTPRequestHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
            parser.error(f"port {args.port} is already in use on 127.0.0.1. Choose another with --port or use --port 0")
        parser.error(f"could not bind to 127.0.0.1:{args.port}: {exc}. Try another port or use --port 0")

    # Bind before loading extensions and recovering runs, so a port conflict leaves state alone.
    with server:
        service = Service(args.workspace)
        server.RequestHandlerClass = make_handler(service, frontend)
        url = f"http://127.0.0.1:{server.server_port}"
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


def workbench_main(argv=None):
    frontend = PACKAGED_FRONTEND if (PACKAGED_FRONTEND / "index.html").is_file() else DEVELOPMENT_FRONTEND
    return _serve(argv, prog="corecoder web", workspace=Path.cwd(), frontend=frontend, require_assets=True)


def main():
    return _serve(None, prog=None, workspace=ROOT, frontend=DEVELOPMENT_FRONTEND, require_assets=False)


if __name__ == "__main__":
    main()
