"""Direct risk samples for the provider's colon-separated diagnostic."""
import pytest
from openai import BadRequestError

from corecoder.llm import _adapt_rejected_param
from sdk_fixture import httpx


@pytest.mark.parametrize(("message", "removed", "translated"), [
    ("UNSUPPORTED parameter:  Temperature", True, False),
    ("Unknown parameter : temperature.", True, False),
    ("Unsupported parameter: temperature_scale", False, False),
    ("Unsupported parameter: top_temperature", False, False),
    ("Unsupported parameter: 'max_completion_tokens'", False, False),
    ("Unsupported parameter: 'max_tokens'", False, True),
])
def test_adapter_distinguishes_rejected_parameter_names(message, removed, translated):
    params = {"temperature": 0.7, "max_tokens": 32}
    response = httpx.Response(400, request=httpx.Request("POST", "https://fixture.invalid"))
    exc = BadRequestError(message, response=response, body={"message": message})
    assert _adapt_rejected_param(params, exc) is (removed or translated)
    expected = {"max_completion_tokens" if translated else "max_tokens": 32}
    if not removed:
        expected["temperature"] = 0.7
    assert params == expected
