"""Offline browser acceptance fixture; real HTTP/SSE, isolated disposable stores.

Run from repository root: python -m designDocument.validation.ui_2026_10_29.serve_acceptance
Prompts: 'fail' produces an error, 'slow' streams until stopped, otherwise succeeds.
No credentials, provider traffic, or user session stores are used.
"""

import os
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from corecoder import Config
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse
from web_backend.server import make_handler
from web_backend.service import RequestError, Service

ROOT = Path(__file__).resolve().parents[3]


class AcceptanceLLM(ScriptedLLM):
    def __init__(self):
        super().__init__([], model="offline-ui-acceptance")

    def chat(self, messages, tools=None, on_token=None, on_reasoning=None):
        prompt = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if prompt == "fail":
            raise RuntimeError("Offline acceptance: simulated provider failure")
        text = "离线验收完成。"
        if prompt == "slow":
            for _ in range(60):
                if on_token:
                    on_token("等待停止验证。")
                time.sleep(0.5)
        elif on_token:
            on_token(text)
        return LLMResponse(content=text)


class AcceptanceService(Service):
    def start(self, sid, prompt):
        if prompt == "reject":
            raise RequestError("Offline acceptance: simulated HTTP 409 before acceptance", 409)
        return super().start(sid, prompt)


def main():
    state = ROOT / ".tmp" / "ui-acceptance-20261029"
    workspace = state / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["CORECODER_DATA_DIR"] = str(state / "data")
    Config.from_env = classmethod(lambda cls: Config(api_key="offline-test-only", model="offline-ui-acceptance"))
    service = AcceptanceService(workspace, store=state / "sessions", llm_factory=AcceptanceLLM, extensions=False)
    server = ThreadingHTTPServer(("127.0.0.1", 8766), make_handler(service, ROOT / "frontend" / "dist"))
    print("Offline acceptance: http://127.0.0.1:8766", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
