"""An expendable local HTTP server whose process can be killed during an SDK send."""
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from openai import OpenAI

from corecoder.llm import LLM
from sdk_fixture import httpx, stream
from web_backend.server import make_handler
from web_backend.service import Service


def main():
    workspace, store, sends = [Path(argument) for argument in sys.argv[1:]]
    endpoint = "https://fixture.invalid/v1"

    def respond(request):
        body = json.loads(request.content)
        prompt = next(m["content"] for m in body["messages"] if m["role"] == "user")
        with sends.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"prompt": prompt}) + "\n")
        if prompt == "plain run":
            return stream({"content": "older conversation without a team"})
        if prompt == "restart run":
            task_list = [
                {"name": "completed-first", "task": "first member"},
                {"name": "interrupted-next", "task": "blocked member", "depends_on": ["completed-first"]},
                {"name": "never-started", "task": "unreachable member", "depends_on": ["interrupted-next"]},
            ]
            return stream({"tool_calls": [{
                "index": 0, "id": "restart-team", "type": "function",
                "function": {"name": "agent", "arguments": json.dumps({"tasks": task_list})},
            }]})
        if prompt.startswith("first member"):
            return stream({"content": "finished before restart"})
        # The parent test terminates only this process after observing this
        # request in sends. A deadline prevents an orphan from hanging.
        threading.Event().wait(45)
        return stream({"content": "unexpected late answer"})

    def make_model():
        llm = LLM("fixture-restart", "offline-only", endpoint, max_tokens=32)
        llm.client = OpenAI(api_key="offline-only", base_url=endpoint, max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(respond)))
        return llm

    service = Service(workspace, store=store, llm_factory=make_model, extensions=False)
    with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, workspace)) as server:
        print(server.server_port, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
