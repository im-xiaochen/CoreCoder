"""Offline HTTP budget boundary tests."""
import json
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from corecoder.llm import LLM
from web_backend.server import make_handler
from web_backend.service import Service


def test_http_budget_validation_and_zero_cap(tmp_path):
    calls = []
    model = LLM("fake", "fake", max_tokens=4)
    original = model.client.with_options
    def options(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    model.client.with_options = options
    service = Service(tmp_path, store=tmp_path / "store", llm_factory=lambda: model, extensions=False)
    sid = service.create()["id"]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def post(budget):
        data = json.dumps({"id": sid, "prompt": "hello", "budget": budget}).encode()
        return urlopen(Request(base + "/api/runs", data=data, headers={"Content-Type": "application/json", "X-CoreCoder-Token": service.token}))
    try:
        for bad in ({"requests": True}, {"requests": -1}, {"cost": "NaN"}, {"bad": 1}):
            with pytest.raises(HTTPError) as err:
                post(bad)
            assert err.value.code == 400 and not service.runs.runs
        with post({"requests": 0}) as response:
            rid = json.load(response)["id"]
        for _ in range(100):
            run = service.runs.view(rid)
            if run["status"] == "failed":
                break
            time.sleep(0.02)
        assert run["status"] == "failed" and run["budget"]["attempts"] == {}
        assert not model.usage_ledger
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
