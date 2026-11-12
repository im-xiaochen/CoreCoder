from types import SimpleNamespace

import httpx2 as httpx
from openai import BadRequestError

from corecoder.llm import LLM, _adapt_rejected_param


def test_reasoning_tool_history_scoped_to_origin():
    model = LLM("model-a", "fake")
    function = SimpleNamespace(name="read_file", arguments='{"file_path":"a"}')
    call = SimpleNamespace(index=0, id="call", function=function)
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content="think", reasoning=None, content=None, tool_calls=[call]))], usage=None)]
    response = model._drain(iter(chunks), None, None)
    message = response.message
    assert message["reasoning_content"] == "think" and message["_reasoning_source"]["model"] == "model-a"
    requests = []
    model._call_with_retry = lambda params: requests.append(params.copy()) or iter([])
    model.chat([message])
    assert requests[-1]["messages"][0]["reasoning_content"] == "think"
    assert "_reasoning_source" not in requests[-1]["messages"][0]
    model.model = "model-b"
    model.chat([message])
    assert "reasoning_content" not in requests[-1]["messages"][0]


def test_temperature_unquoted_and_exact_max_token():
    request = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(400, request=request)
    params = {"temperature": 0.5, "max_tokens": 10}
    error = BadRequestError("unsupported parameter temperature", response=response, body=None)
    assert _adapt_rejected_param(params, error) and "temperature" not in params
    error = BadRequestError("unsupported 'max_completion_tokens'", response=response, body=None)
    assert not _adapt_rejected_param(params, error) and params["max_tokens"] == 10
