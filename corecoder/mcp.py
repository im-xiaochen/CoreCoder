"""MCP stdio client, distilled to the slice of the protocol an agent uses.

Servers are configured in ~/.corecoder/mcp.json:

    {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "some-fs-server", "/tmp"]}}}

Each server is a subprocess speaking JSON-RPC 2.0, one message per line over
stdin/stdout. Startup handshakes (`initialize`), pulls `tools/list`, and every
remote tool joins the agent as `mcp__<server>__<tool>`, so consent, hooks and
the main loop treat them exactly like the built-ins. A server that hangs or
dies fails that one call as an ordinary error string; it never kills the loop.
"""

import atexit
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from . import __version__
from .tools.base import Tool
from .validation import validate_mcp_config

log = logging.getLogger(__name__)

CONFIG_FILE = Path.home() / ".corecoder" / "mcp.json"
PROTOCOL_VERSION = "2025-06-18"
INIT_TIMEOUT = 15  # seconds for initialize + tools/list at startup
MAX_DISCOVERY_PAGES = 100
MAX_DISCOVERY_TOOLS = 1000
CALL_TIMEOUT = 60  # seconds for one tools/call


class MCPError(RuntimeError):
    """Transport or protocol failure talking to one server."""


class MCPClient:
    """One stdio server process: handshake, list, call, shut down.

    A daemon thread owns stdout and parks each response under its request id,
    so calls from parallel tool execution match their own replies; the write
    lock keeps two threads' requests from interleaving on stdin.
    """

    def __init__(self, name: str, command: str, args: list = (), env: dict | None = None):
        self.name = name
        self.call_timeout = CALL_TIMEOUT
        self._proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={**os.environ, **(env or {})},  # servers inherit the user env, config overrides
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line buffered: the transport is newline-delimited JSON
        )
        self._next_id = 0
        self._dead: MCPError | None = None
        self._responses: dict[int, dict] = {}
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()
        try:
            deadline = time.monotonic() + INIT_TIMEOUT
            initialized = self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "corecoder", "version": __version__},
                },
                max(0, deadline - time.monotonic()),
            )
            self._validate_initialize(initialized)
            self.protocol_version = initialized["protocolVersion"]
            self._notify("notifications/initialized")
            capabilities = initialized["capabilities"]
            candidate, seen_cursors, seen_names = [], set(), set()
            if isinstance(capabilities.get("tools"), dict):
                cursor = None
                for page in range(MAX_DISCOVERY_PAGES):
                    listed = self._request(
                        "tools/list", {} if cursor is None else {"cursor": cursor}, max(0, deadline - time.monotonic())
                    )
                    raw = listed.get("tools") if isinstance(listed, dict) else None
                    if not isinstance(raw, list):
                        raise MCPError("invalid tools/list schema: tools must be array")
                    for spec in raw:
                        if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or not spec["name"].strip():
                            raise MCPError("invalid tools/list schema: tool name")
                        if spec["name"] in seen_names:
                            raise MCPError("duplicate tool name")
                        schema = spec.get("inputSchema")
                        if not isinstance(schema, dict):
                            raise MCPError("invalid inputSchema")
                        if "type" in schema and schema["type"] != "object":
                            raise MCPError("invalid inputSchema: expected object schema")
                        try:
                            from jsonschema.validators import validator_for

                            validator_for(schema).check_schema(schema)
                        except Exception as exc:
                            raise MCPError("invalid inputSchema schema") from exc
                        if "description" in spec and not isinstance(spec["description"], str):
                            raise MCPError("invalid tool description")
                        seen_names.add(spec["name"])
                        candidate.append(spec)
                        if len(candidate) > MAX_DISCOVERY_TOOLS:
                            raise MCPError("tool limit exceeded")
                    if "nextCursor" not in listed:
                        break
                    cursor = listed["nextCursor"]
                    if not isinstance(cursor, str) or not cursor:
                        raise MCPError("invalid pagination cursor")
                    if cursor in seen_cursors:
                        raise MCPError("duplicate pagination cursor")
                    seen_cursors.add(cursor)
                else:
                    raise MCPError("pagination page limit exceeded")
            self.tools = [MCPTool(self, t) for t in candidate]
        except BaseException:
            self.close()  # a half-started server must not leak
            raise

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Run one remote tool and return its text content."""
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments}, self.call_timeout)
        text = "\n".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
        if result.get("isError"):
            raise MCPError(text or f"{tool_name} reported an error")
        return text or json.dumps(result)  # non-text content: hand the model the raw result

    def close(self):
        """Shut the server down. Safe to call twice."""
        if self._proc.poll() is not None:
            return
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)

    def _validate_initialize(self, result):
        if not isinstance(result, dict):
            raise MCPError("invalid initialize result")
        version = result.get("protocolVersion")
        if version != PROTOCOL_VERSION:
            raise MCPError(f"unsupported protocol version {version!r}")
        caps, info = result.get("capabilities"), result.get("serverInfo")
        if not isinstance(caps, dict) or not isinstance(info, dict):
            raise MCPError("invalid initialize schema")
        if (
            not isinstance(info.get("name"), str)
            or not info["name"].strip()
            or not isinstance(info.get("version"), str)
            or not info["version"].strip()
        ):
            raise MCPError("invalid serverInfo")
        for val in caps.values():
            if not isinstance(val, dict):
                raise MCPError("invalid capabilities")
            if "listChanged" in val and not isinstance(val["listChanged"], bool):
                raise MCPError("invalid capabilities listChanged")

    def _notify(self, method: str):
        try:
            self._write({"jsonrpc": "2.0", "method": method, "params": {}})
        except MCPError:
            pass  # a notification has no reply to lose; the next request meets the dead server

    def _request(self, method: str, params: dict, timeout: float) -> dict:
        if timeout <= 0:
            raise MCPError(f"MCP discovery deadline exceeded before {method}")
        with self._cond:
            if self._dead is not None:
                raise self._dead
            self._next_id += 1
            req_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        with self._cond:
            while req_id not in self._responses:
                if self._dead is not None:
                    raise self._dead
                left = deadline - time.monotonic()
                if left <= 0:
                    raise MCPError(f"MCP server {self.name!r} gave no answer to {method} in {timeout:g}s")
                self._cond.wait(left)
            msg = self._responses.pop(req_id)
        if "error" in msg:
            raise MCPError(f"MCP server {self.name!r} rejected {method}: {msg['error'].get('message', msg['error'])}")
        return msg.get("result", {})

    def _write(self, msg: dict):
        try:
            with self._write_lock:
                self._proc.stdin.write(json.dumps(msg) + "\n")
                self._proc.stdin.flush()
        except OSError as e:
            raise MCPError(f"MCP server {self.name!r} is not writable: {e}") from e

    def _read_loop(self):
        try:
            for line in self._proc.stdout:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("MCP server %r sent a non-JSON line, skipped", self.name)
                    continue
                if not isinstance(msg, dict):
                    log.warning("MCP server %r sent an invalid response, skipped", self.name)
                    continue
                if "id" not in msg:
                    continue  # server notification: nothing here needs an answer
                if not isinstance(msg.get("id"), (str, int, float, type(None))) or isinstance(msg.get("id"), bool):
                    log.warning("MCP server %r sent an invalid response id, skipped", self.name)
                    continue
                with self._cond:
                    self._responses[msg["id"]] = msg
                    self._cond.notify_all()
        except (OSError, ValueError):
            pass
        with self._cond:
            # stdout closing means the server is gone for good
            if self._dead is None:
                self._dead = MCPError(f"MCP server {self.name!r} exited")
            self._cond.notify_all()


class MCPTool(Tool):
    """A tool living in an MCP server, registered under a mcp__ name so it can
    never collide with a built-in. Side effects are unknown, so it stays out of
    Permission.READ_ONLY and the consent gate asks first, like any mutating tool.
    """

    stateless = True
    remote_schema = True

    def __init__(self, client: MCPClient, spec: dict):
        self._client = client
        self._remote_name = spec["name"]
        self.name = f"mcp__{client.name}__{spec['name']}"
        self.description = spec.get("description") or ""
        self.parameters = spec.get("inputSchema", {"type": "object", "properties": {}})

    def execute(self, **kwargs) -> str:
        return self._client.call_tool(self._remote_name, kwargs)


_live_clients: list[MCPClient] = []


def load_mcp_tools(path: Path = CONFIG_FILE) -> list[Tool]:
    """Start the configured servers and return their tools. A missing file
    means no MCP; a broken file or a server that won't start gets one clear
    warning and the agent carries on with whatever loaded."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError, UnicodeError):
        log.warning("ignoring %s: invalid JSON or unreadable file", path)
        return []
    tools: list[Tool] = []
    for name, (command, args, env) in validate_mcp_config(data, str(path.resolve())).items():
        try:
            client = MCPClient(name, command, args, env)
        except (MCPError, OSError, KeyError, TypeError) as e:
            log.warning("MCP server %r skipped: %s", name, e)
            continue
        _live_clients.append(client)
        tools.extend(client.tools)
    return tools


@atexit.register
def _shutdown():
    for client in _live_clients:
        client.close()
