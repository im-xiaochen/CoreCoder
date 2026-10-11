"""R11: real stdio transport against a deterministic, stdlib-only MCP server."""

import sys

import pytest

from corecoder import mcp

SERVER = r'''
import json
import sys
import time

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
mode = sys.argv[1]
page = 0

def tool(name):
    return {"name": name, "inputSchema": {"type": "object", "properties": {}}}

for line in sys.stdin:
    req = json.loads(line)
    if "id" not in req:
        continue
    if req["method"] == "initialize":
        result = {"protocolVersion": "unsupported" if mode == "version" else "2025-06-18",
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif req["method"] == "tools/list":
        cursor = req.get("params", {}).get("cursor")
        if mode in ("forever", "slow") and page > 10:
            sys.exit(1)  # Bound a broken client too; the expected limit/deadline must fire first.
        if page and mode == "crash":
            sys.exit(1)
        if mode == "slow":
            time.sleep(0.2)
        if mode == "malformed":
            result = {"tools": [42]}
        elif mode == "empty":
            result = {"tools": []}
        else:
            if page and cursor != "opaque:page-" + str(page):
                result = {"tools": []}  # wrong cursor: the multipage assertion will fail
            else:
                result = {"tools": [tool("first" if mode == "duplicate" else "tool" + str(page))]}
                if mode not in ("single", "version") and (page < 2 or mode in ("repeat", "forever", "slow")):
                    result["nextCursor"] = "opaque:page-" + str(page if mode == "repeat" and page else page + 1)
        page += 1
    else:
        result = {"content": [{"type": "text", "text": "OK"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
'''


@pytest.fixture
def server(tmp_path):
    script = tmp_path / "fake_paginated_server.py"
    script.write_text(SERVER, encoding="utf-8")
    return script


def connect(server, mode):
    return mcp.MCPClient("paged", sys.executable, [str(server), mode])


def test_all_pages_and_opaque_cursor_are_followed(server):
    client = connect(server, "pages")
    try:
        assert [tool.name for tool in client.tools] == [f"mcp__paged__tool{i}" for i in range(3)]
        assert client.tools[-1].execute() == "OK"
        assert client.protocol_version == "2025-06-18"
    finally:
        client.close()


@pytest.mark.parametrize("mode,expected", [("single", ["mcp__paged__tool0"]), ("empty", [])])
def test_single_and_empty_list_remain_supported(server, mode, expected):
    client = connect(server, mode)
    try:
        assert [tool.name for tool in client.tools] == expected
    finally:
        client.close()


@pytest.mark.parametrize("mode,pattern", [
    ("repeat", "cursor|pagination"), ("duplicate", "duplicate|collision"),
    ("crash", "exited"), ("malformed", "schema|tools|invalid"),
    ("version", "version|protocol"),
])
def test_bad_discovery_fails_atomically_and_reaps_process(server, monkeypatch, mode, pattern):
    processes = []
    original_popen = mcp.subprocess.Popen

    def track(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(mcp.subprocess, "Popen", track)
    client = None
    try:
        with pytest.raises(mcp.MCPError, match=pattern):
            client = connect(server, mode)
    finally:
        if client is not None:
            client.close()
    assert processes and all(process.poll() is not None for process in processes)


def test_distinct_endless_cursors_hit_page_limit(server, monkeypatch):
    assert hasattr(mcp, "MAX_DISCOVERY_PAGES"), "R11 must bound discovery independently of cursor equality"
    monkeypatch.setattr(mcp, "MAX_DISCOVERY_PAGES", 3)
    client = None
    try:
        with pytest.raises(mcp.MCPError, match="page|pagination"):
            client = connect(server, "forever")
    finally:
        if client is not None:
            client.close()


def test_whole_discovery_uses_one_deadline(server, monkeypatch):
    monkeypatch.setattr(mcp, "INIT_TIMEOUT", 0.5)
    client = None
    try:
        with pytest.raises(mcp.MCPError, match="timeout|deadline|no answer"):
            client = connect(server, "slow")
    finally:
        if client is not None:
            client.close()
