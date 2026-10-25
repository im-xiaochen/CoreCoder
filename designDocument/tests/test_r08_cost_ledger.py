"""R08: synthetic fixed rates, no claim about current provider pricing."""

from types import SimpleNamespace

import pytest

from corecoder import llm as llm_module
from corecoder.llm import LLM, LiteLLM
from designDocument.tests.support import required_attr, stream_chunk


@pytest.fixture
def priced_llm(monkeypatch):
    monkeypatch.setattr(llm_module, "_PRICING", {"model-a": (2.0, 8.0), "model-b": (0.2, 0.8)})
    return LLM(model="model-a", api_key="offline-test")


def consume(llm, prompt=1_000_000, completion=0):
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    llm._drain(iter([stream_chunk(usage=usage)]), None, None)


def test_model_switch_does_not_reprice_history(priced_llm):
    consume(priced_llm)
    before = priced_llm.estimated_cost
    assert before == pytest.approx(2.0)
    priced_llm.model = "model-b"
    assert priced_llm.estimated_cost == before
    consume(priced_llm, prompt=1_000_000, completion=1_000_000)
    assert priced_llm.estimated_cost == pytest.approx(3.0)


def test_rate_changes_do_not_reprice_previous_entries(priced_llm, monkeypatch):
    consume(priced_llm)
    monkeypatch.setitem(llm_module._PRICING, "model-a", (100.0, 100.0))
    assert priced_llm.estimated_cost == pytest.approx(2.0)
    consume(priced_llm, prompt=1_000_000)
    assert priced_llm.estimated_cost == pytest.approx(102.0)


def test_unknown_rate_exposes_known_subtotal_and_unknown_usage(priced_llm):
    consume(priced_llm)
    priced_llm.model = "unknown-model"
    consume(priced_llm, prompt=123, completion=45)
    summary = required_attr(priced_llm, "cost_summary")
    assert summary.known_cost == pytest.approx(2.0)
    assert summary.unknown_prompt_tokens == 123
    assert summary.unknown_completion_tokens == 45
    assert summary.unknown_requests == 1
    assert priced_llm.estimated_cost is None, "Partial cost must not be presented as a complete total"


def test_missing_usage_is_distinct_from_zero_usage(priced_llm):
    priced_llm._drain(iter([stream_chunk()]), None, None)
    summary = required_attr(priced_llm, "cost_summary")
    assert summary.missing_usage_requests == 1
    assert priced_llm.estimated_cost is None


def test_request_model_snapshot_survives_midstream_model_change(priced_llm, monkeypatch):
    def stream(params):
        assert params["model"] == "model-a"
        priced_llm.model = "model-b"
        return iter([stream_chunk(usage=SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=0))])

    monkeypatch.setattr(priced_llm, "_call_with_retry", stream)
    priced_llm.chat([{"role": "user", "content": "go"}])
    assert priced_llm.estimated_cost == pytest.approx(2.0)
    ledger = required_attr(priced_llm, "usage_ledger")
    assert ledger[-1].model == "model-a"


def test_litellm_uses_same_ledger(monkeypatch):
    monkeypatch.setattr(llm_module, "_PRICING", {"local/model-a": (2.0, 8.0), "local/model-b": (0.2, 0.8)})
    llm = LiteLLM(model="local/model-a")
    consume(llm)
    llm.model = "local/model-b"
    assert llm.estimated_cost == pytest.approx(2.0)
