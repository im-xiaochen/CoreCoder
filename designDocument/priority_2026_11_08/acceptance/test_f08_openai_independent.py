"""Actual OpenAI SDK + MockTransport; no requests can reach a network."""
import json
from decimal import Decimal

try:
    import httpx
except ImportError:
    import httpx2 as httpx
import pytest
from openai import OpenAI

import corecoder.llm as llm_module
from corecoder.budget import BudgetExceeded, BudgetLimits, RunBudget
from corecoder.llm import LLM, budget_scope


def event(data):
    return ('data: ' + json.dumps(data) + '\n\n').encode()


def chunk(content=None, usage=None):
    return {"id": "offline", "object": "chat.completion.chunk", "created": 1,
            "model": "independent", "choices": [] if usage else [{"index": 0,
            "delta": {"content": content} if content else {}, "finish_reason": None}],
            **({"usage": usage} if usage else {})}


def success(with_usage=True):
    body = event(chunk("offline answer"))
    if with_usage:
        body += event(chunk(usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}))
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=body + b'data: [DONE]\n\n')


@pytest.fixture
def client_factory(monkeypatch):
    # No real home pricing or network client is used, even at construction.
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: None)
    clients = []

    def make(budget, handler, **extra):
        calls = []

        def observe(request):
            payload = json.loads(request.content)
            observed_budget = budget if budget is not None else llm_module._BUDGET_SCOPE.get()
            snapshot = observed_budget.snapshot()
            assert snapshot['groups']['in_flight']['requests'] >= 1
            calls.append({"payload": payload, "snapshot": snapshot})
            return handler(request, len(calls))

        sdk = OpenAI(api_key="offline-not-a-key", base_url="https://offline.invalid/v1",
                     max_retries=7, http_client=httpx.Client(transport=httpx.MockTransport(observe)))
        # Replace only constructor, leaving actual SDK .with_options/create/stream intact.
        monkeypatch.setattr(llm_module, "OpenAI", lambda **kwargs: sdk)
        pricing = {("https://offline.invalid/v1", "independent"): (0.1, 0.2)}
        llm = LLM("independent", "offline-not-a-key", "https://offline.invalid/v1",
                  pricing=pricing, **({"max_tokens": 32} | extra))
        clients.append(sdk)
        return llm, calls

    yield make
    for client in clients:
        client.close()


@pytest.mark.parametrize('limits', [BudgetLimits(requests=0), BudgetLimits(total_tokens=0), BudgetLimits(cost="0")])
def test_zero_cap_makes_no_physical_sdk_request(client_factory, limits):
    budget = RunBudget(limits)
    llm, calls = client_factory(budget, lambda request, number: success())
    with budget_scope(budget), pytest.raises(BudgetExceeded):
        llm.chat([{"role": "user", "content": "must not send"}])
    assert calls == [] and llm.usage_ledger == ()


def test_sdk_hidden_retries_disabled_and_reservation_gate_precedes_retry(client_factory):
    budget = RunBudget(BudgetLimits(requests=1))
    llm, calls = client_factory(budget, lambda request, number: httpx.Response(503, json={"error": {"message": "offline unavailable"}}))
    with budget_scope(budget), pytest.raises(BudgetExceeded):
        llm.chat([{"role": "user", "content": "retry"}])
    assert len(calls) == 1  # SDK originally configured for seven retries.
    assert budget.snapshot()['groups']['unconfirmed']['requests'] == 1
    assert len(llm.usage_ledger) == 1 and not llm.usage_ledger[0].usage_known


def test_create_retries_are_individually_reserved_and_reconcile_f01(client_factory):
    budget = RunBudget(BudgetLimits(requests=3))
    def respond(request, number):
        return httpx.Response(503, json={"error": {"message": "retry"}}) if number < 3 else success()
    llm, calls = client_factory(budget, respond)
    with budget_scope(budget):
        result = llm.chat([{"role": "user", "content": "three physical attempts"}])
    assert result.content == 'offline answer' and len(calls) == 3
    ledger = llm.usage_ledger
    assert len(ledger) == 3 and len({entry.attempt_id for entry in ledger}) == 3
    snapshot = budget.snapshot()
    assert snapshot['groups']['unconfirmed']['requests'] == 2
    assert snapshot['groups']['actual']['requests'] == 1
    assert set(snapshot['attempts']) == {entry.attempt_id for entry in ledger}
    entry = next(entry for entry in ledger if entry.usage_known)
    assert entry.estimated_cost == Decimal('0.0000018')
    assert Decimal(snapshot['groups']['actual']['known_cost']) == entry.estimated_cost
    assert snapshot['attempts'][entry.attempt_id]['tokens'] == 15


def test_400_parameter_fallback_is_a_new_budgeted_physical_attempt(client_factory):
    budget = RunBudget(BudgetLimits(requests=2))
    def respond(request, number):
        return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'"}}) if number == 1 else success()
    llm, calls = client_factory(budget, respond)
    with budget_scope(budget):
        llm.chat([{"role": "user", "content": "dialect fallback"}])
    assert len(calls) == 2
    assert calls[0]['payload']['max_tokens'] == 32
    assert 'max_tokens' not in calls[1]['payload']
    assert calls[1]['payload']['max_completion_tokens'] == 32
    assert len(llm.usage_ledger) == 2
    assert budget.snapshot()['groups']['unconfirmed']['requests'] == 1


def test_actual_system_tools_input_included_before_send_and_output_is_bounded(client_factory):
    messages = [{"role": "system", "content": "independent system rules " * 200},
                {"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "offline_tool", "description": "long schema " * 200,
              "parameters": {"type": "object", "properties": {"argument": {"type": "string"}}}}}]
    budget = RunBudget(BudgetLimits(total_tokens=100))
    llm, calls = client_factory(budget, lambda request, number: success())
    with budget_scope(budget), pytest.raises(BudgetExceeded):
        llm.chat(messages, tools=tools)
    assert not calls
    generous = RunBudget(BudgetLimits(total_tokens=100000))
    llm, calls = client_factory(generous, lambda request, number: success())
    with budget_scope(generous):
        llm.chat(messages, tools=tools)
    payload = calls[0]['payload']
    assert payload['messages'] == messages and payload['tools'] == tools
    reserve = next(iter(calls[0]['snapshot']['attempts'].values()))
    assert reserve['tokens'] > 100
    assert payload['max_tokens'] == 32


def test_missing_stream_usage_remains_uncertain_instead_of_zero_cost(client_factory):
    budget = RunBudget(BudgetLimits(requests=1))
    llm, calls = client_factory(budget, lambda request, number: success(False))
    with budget_scope(budget):
        llm.chat([{"role": "user", "content": "usage missing"}])
    snapshot = budget.snapshot()
    assert len(calls) == 1 and snapshot['groups']['actual']['requests'] == 0
    assert snapshot['groups']['unconfirmed']['requests'] == 1
    assert snapshot['groups']['unconfirmed']['tokens'] >= 32
    assert snapshot['report']['requests_remaining'] == 0
    assert not llm.usage_ledger[0].usage_known


class BrokenWire(httpx.SyncByteStream):
    def __iter__(self):
        yield event(chunk('partial before physical connection drop'))
        raise httpx.ReadError('independent physical stream disconnected')


def test_real_sdk_midstream_wire_drop_retries_with_new_reservation(client_factory):
    budget = RunBudget(BudgetLimits(requests=2))
    def respond(request, number):
        if number == 1:
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=BrokenWire())
        return success()
    llm, calls = client_factory(budget, respond)
    with budget_scope(budget):
        result = llm.chat([{"role": "user", "content": "wire drop"}])
    assert result.content == 'offline answer' and len(calls) == 2
    assert budget.snapshot()['groups']['unconfirmed']['requests'] == 1
    assert budget.snapshot()['groups']['actual']['requests'] == 1
    assert len({entry.attempt_id for entry in llm.usage_ledger}) == 2


def test_actual_stream_callback_nested_chat_settles_both_physical_ids(client_factory):
    budget = RunBudget(BudgetLimits(requests=2))
    llm, calls = client_factory(budget, lambda request, number: success())
    nested = []
    def on_token(text):
        if not nested:
            nested.append('entered')
            nested.append(llm.chat([{"role": "user", "content": "nested physical request"}]).content)
    with budget_scope(budget):
        llm.chat([{"role": "user", "content": "outer physical request"}], on_token=on_token)
    assert len(calls) == 2 and nested[-1] == 'offline answer'
    snapshot = budget.snapshot()
    assert snapshot['groups']['actual']['requests'] == 2
    assert snapshot['groups']['in_flight']['requests'] == 0
    assert snapshot['groups']['unconfirmed']['requests'] == 0
    assert {entry.attempt_id for entry in llm.usage_ledger} == set(snapshot['attempts'])


def test_local_client_options_failure_is_provably_unsent(client_factory):
    budget = RunBudget(BudgetLimits(requests=1))
    llm, calls = client_factory(budget, lambda request, number: success())
    class BrokenLocalOptions:
        def with_options(self, **kwargs):
            raise ValueError('local configuration failure before create')
    llm.client = BrokenLocalOptions()
    with budget_scope(budget), pytest.raises(ValueError):
        llm.chat([{"role": "user", "content": "never transmitted"}])
    assert not calls and not llm.usage_ledger
    snapshot = budget.snapshot()
    assert snapshot['report']['requests_remaining'] == 1
    assert all(snapshot['groups'][state]['requests'] == 0 for state in ('actual', 'in_flight', 'unconfirmed'))
