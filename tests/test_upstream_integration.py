"""Regression checks for v0.8.0 integrated with the fork's request ledger."""

import io
import json
import sys
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

from corecoder import llm as llm_module
from corecoder.llm import LLM, _pricing_for
from corecoder.mcp import MCPError, load_mcp_tools
from tests.test_mcp import FAKE_SERVER


def drain(llm):
    llm._drain(iter([SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000000,
                        completion_tokens=500000), choices=[])]), None, None)


def test_file_rates_are_snapshotted_per_request(tmp_path, monkeypatch):
    path = tmp_path / "pricing.json"
    monkeypatch.setattr(llm_module, "PRICING_FILE", path)
    endpoint = "https://example.test/v1"
    path.write_text(json.dumps({"endpoints": {endpoint: {"custom": [1, 2]}}}), encoding="utf-8")
    llm = LLM("custom", "offline", base_url=endpoint)
    context = llm._request_context()
    path.write_text(json.dumps({"endpoints": {endpoint: {"custom": [10, 20]}}}), encoding="utf-8")
    llm._record_usage(context, "attempt", 1000000, 500000, True)
    original = llm.usage_ledger[0]
    assert original.estimated_cost == Decimal(2)
    drain(llm)
    assert llm.usage_ledger[0] == original
    assert llm.usage_ledger[1].estimated_cost == Decimal(20)
    assert llm.estimated_cost == 22
    assert _pricing_for("openai", "https://other.test/v1", "custom") is None


def test_model_only_file_rate_does_not_price_another_endpoint(tmp_path, monkeypatch):
    path = tmp_path / "pricing.json"
    path.write_text('{"gpt-5.4": [1, 2]}', encoding="utf-8")
    monkeypatch.setattr(llm_module, "PRICING_FILE", path)
    assert _pricing_for("openai", "", "gpt-5.4") == (1, 2)
    assert _pricing_for("openai", "https://third-party.test", "gpt-5.4") is None
    explicit = {("litellm", "https://third-party.test", "gpt-5.4"): (3, 4)}
    assert _pricing_for("litellm", "https://third-party.test", "gpt-5.4", explicit) == (3, 4)
    assert _pricing_for("openai", "https://third-party.test", "gpt-5.4", explicit) is None


@pytest.mark.parametrize("invalid", [[-1, 2], [float("nan"), 2], [1, float("inf")],
                                      [True, 2], [1, 2, 3], "cheap", ["1", "2"]])
def test_invalid_rate_does_not_poison_other_entries(tmp_path, monkeypatch, invalid):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"broken": invalid, "valid": [0, 2]}), encoding="utf-8")
    monkeypatch.setattr(llm_module, "PRICING_FILE", path)
    rates = llm_module._load_pricing()
    assert "broken" not in rates
    assert rates["valid"] == (0, 2)


@pytest.fixture
def server_script(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


@pytest.fixture
def mcp_config(tmp_path, server_script):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"fake": {"command": sys.executable,
                    "args": [str(server_script)]}}}), encoding="utf-8")
    return path


@pytest.fixture
def client(mcp_config):
    tools = load_mcp_tools(mcp_config)
    instance = tools[0]._client
    yield instance
    instance.close()
    from corecoder import mcp
    mcp._live_clients.remove(instance)


def test_reconnect_cancels_old_inflight_call_without_replay(client, monkeypatch):
    sent = threading.Event()
    write = client._write

    def observe(msg, proc=None):
        write(msg, proc)
        if msg.get("method") == "tools/call":
            sent.set()

    monkeypatch.setattr(client, "_write", observe)
    outcomes = []

    def call():
        try:
            client.call_tool("stall", {})
        except MCPError as exc:
            outcomes.append(str(exc))

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    assert sent.wait(5)
    client.reconnect()
    thread.join(5)
    assert not thread.is_alive()
    assert len(outcomes) == 1
    assert client.call_tool("echo", {"text": "new connection"}) == "echo: new connection"


def test_stale_reader_cannot_publish_responses_or_kill_new_process(client):
    stale = SimpleNamespace(stdout=io.StringIO('{"id": 123456, "result": "stale"}\n'))
    client._read_loop(stale)
    assert 123456 not in client._responses
    assert client.error is None
    assert client.call_tool("echo", {"text": "healthy"}) == "echo: healthy"


@pytest.mark.parametrize("replacement, message", [
    (FAKE_SERVER.replace('"protocolVersion": "2025-06-18"', '"protocolVersion": "invalid"'), "protocol"),
    (FAKE_SERVER.replace('"required": ["text"]', '"required": 123'), "inputSchema"),
    (FAKE_SERVER.replace('result = {"tools": TOOLS}', 'result = {"tools": TOOLS, "nextCursor": "loop"}'), "duplicate"),
    (FAKE_SERVER.replace('"Echo text back"', '"Changed contract"'), "restart the session"),
])
def test_reconnect_failure_preserves_published_tools(client, server_script, replacement, message):
    original = client.tools
    server_script.write_text(replacement, encoding="utf-8")
    with pytest.raises(MCPError, match=message):
        client.reconnect()
    assert client.tools is original
    assert client.error is not None
    with pytest.raises(MCPError):
        original[0].execute(text="must not run")
