"""Unsupported budget adapter must fail closed; no provider packages loaded."""
import sys
from types import SimpleNamespace

import pytest

from corecoder.budget import BudgetExceeded, BudgetLimits, RunBudget
from corecoder.llm import LiteLLM, budget_scope


def fake_module(monkeypatch):
    calls = []
    def completion(**params):
        calls.append(params)
        return iter([
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='offline default', tool_calls=None), finish_reason=None)], usage=None),
            SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2)),
        ])
    monkeypatch.setitem(sys.modules, 'litellm', SimpleNamespace(completion=completion))
    return calls


def test_enabled_budget_litellm_rejects_before_send_or_ledger(monkeypatch):
    calls = fake_module(monkeypatch)
    budget = RunBudget(BudgetLimits(requests=2))
    llm = LiteLLM('offline/provider', api_key='not-a-key', pricing={})
    before = budget.snapshot()
    with budget_scope(budget), pytest.raises(BudgetExceeded, match='unsupported|not verified'):
        llm.chat([{'role': 'user', 'content': 'must reject before provider'}])
    assert calls == [] and not llm.usage_ledger
    assert budget.snapshot() == before


def test_unbudgeted_litellm_remains_compatible(monkeypatch):
    calls = fake_module(monkeypatch)
    llm = LiteLLM('offline/provider', api_key='not-a-key', pricing={})
    response = llm.chat([{'role': 'user', 'content': 'default compatibility'}])
    assert response.content == 'offline default' and len(calls) == 1
    assert response.prompt_tokens == 4 and response.completion_tokens == 2
    assert len(llm.usage_ledger) == 1 and llm.usage_ledger[0].usage_known
