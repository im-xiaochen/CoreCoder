from types import SimpleNamespace

import httpx2 as httpx
import pytest
from openai import APIConnectionError, BadRequestError

from corecoder.budget import BudgetExceeded, BudgetLimits, RunBudget
from corecoder.llm import LLM, budget_scope


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.options = []
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return iter(result)


def make_llm(outcomes):
    model = LLM("test", "fake", max_tokens=4)
    model.client = FakeClient(outcomes)
    return model


def test_zero_cap_never_calls_provider():
    model = make_llm([])
    with budget_scope(RunBudget(BudgetLimits(requests=0))), pytest.raises(BudgetExceeded):
        model.chat([{"role": "user", "content": "hello"}])
    assert model.client.calls == []


def test_retry_is_gated_and_sdk_retry_disabled(monkeypatch):
    monkeypatch.setattr("corecoder.llm.time.sleep", lambda _: None)
    error = APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    model = make_llm([error, []])
    budget = RunBudget(BudgetLimits(requests=1))
    with budget_scope(budget), pytest.raises(BudgetExceeded):
        model.chat([{"role": "user", "content": "hello"}])
    assert len(model.client.calls) == 1 and all(option == {"max_retries": 0} for option in model.client.options)
    assert budget.snapshot()["groups"]["unconfirmed"]["requests"] == 1


def test_400_fallback_is_a_new_attempt():
    req = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(400, request=req)
    error = BadRequestError("unsupported stream_options", response=response, body=None)
    model = make_llm([error, []])
    with budget_scope(RunBudget(BudgetLimits(requests=1))), pytest.raises(BudgetExceeded):
        model.chat([{"role": "user", "content": "hello"}])
    assert len(model.client.calls) == 1


def test_stream_usage_and_missing_usage_settle():
    usage = SimpleNamespace(prompt_tokens=7, completion_tokens=2, prompt_tokens_details=None, completion_tokens_details=None)
    model = make_llm([[SimpleNamespace(usage=usage, choices=[])], []])
    budget = RunBudget()
    with budget_scope(budget):
        model.chat([{"role": "user", "content": "hello"}])
        model.chat([{"role": "user", "content": "again"}])
    groups = budget.snapshot()["groups"]
    assert groups["actual"]["tokens"] == 9 and groups["unconfirmed"]["requests"] == 1
    assert model.usage_ledger[0].attempt_id in budget.snapshot()["attempts"]



def test_local_client_failure_does_not_reserve():
    model = make_llm([])
    def fail(**kwargs):
        raise RuntimeError("local setup failure")
    model.client.with_options = fail
    budget = RunBudget()
    with budget_scope(budget), pytest.raises(RuntimeError):
        model.chat([{"role": "user", "content": "hello"}])
    assert not budget.snapshot()["attempts"] and not model.usage_ledger and not model.client.calls


def test_nested_chat_preserves_outer_attempt():
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None, completion_tokens_details=None)
    delta = SimpleNamespace(content="outer", reasoning_content=None, reasoning=None, tool_calls=None)
    outer = [SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta)]), SimpleNamespace(usage=usage, choices=[])]
    model = make_llm([outer, [SimpleNamespace(usage=usage, choices=[])]])
    budget = RunBudget()
    with budget_scope(budget):
        model.chat([{"role": "user", "content": "outer"}], on_token=lambda _: model.chat([{"role": "user", "content": "inner"}]))
    assert budget.snapshot()["groups"]["actual"]["requests"] == 2
    assert budget.snapshot()["groups"]["in_flight"]["requests"] == 0
