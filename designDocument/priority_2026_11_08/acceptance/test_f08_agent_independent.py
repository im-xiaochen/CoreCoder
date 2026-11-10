"""Actual Agent/AgentTool/context + real SDK offline physical request observer."""
import json
from decimal import Decimal

import httpx2 as httpx
import pytest

from corecoder.agent import Agent
from corecoder.budget import BudgetLimits
from corecoder.context import SUMMARY_SYSTEM_PROMPT
from corecoder.hooks import Hooks
from corecoder.permissions import Permission
from corecoder.state import SessionState
from corecoder.tools.agent import AgentTool
from corecoder.tools.write import WriteFileTool
from test_f08_openai_independent import client_factory, chunk, event, success


def delegate():
    payload = chunk()
    payload['choices'][0]['delta'] = {'tool_calls': [{'index': 0, 'id': 'delegate-1', 'type': 'function',
        'function': {'name': 'agent', 'arguments': json.dumps({'task': 'independent child task'})}}]}
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=event(payload)
        + event(chunk(usage={'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15})) + b'data: [DONE]\n\n')


def agent_for(llm, tmp_path, tools=None, max_context_tokens=128000):
    return Agent(llm, tools=[] if tools is None else tools,
                 state=SessionState(workspace_root=tmp_path), permission=Permission(allow_all=True),
                 hooks=Hooks([], []), max_context_tokens=max_context_tokens)


def assert_ledger_agrees(llm, result):
    attempts = result.budget['attempts']
    assert set(attempts) == {entry.attempt_id for entry in llm.usage_ledger}
    assert all(item['state'] == 'actual' for item in attempts.values())
    assert Decimal(result.budget['groups']['actual']['known_cost']) == sum(
        (entry.estimated_cost for entry in llm.usage_ledger), Decimal(0))


def test_public_agent_tool_child_and_parent_share_two_attempts(client_factory, tmp_path):
    llm, calls = client_factory(None, lambda request, number: delegate() if number == 1 else success())
    agent = agent_for(llm, tmp_path, tools=[AgentTool()])
    result = agent.chat_result('delegate then report', budget_limits=BudgetLimits(requests=2))
    assert len(calls) == 2
    assert calls[1]['payload']['messages'][-1]['content'] == 'independent child task'
    assert result.status == 'failed' and result.finish_reason == 'budget_exceeded' and result.exit_code == 1
    assert result.as_dict()['finish_reason'] == 'budget_exceeded'
    assert result.budget['report']['requests_remaining'] == 0
    assert any(message.get('role') == 'tool' and '[Sub-agent completed]' in message['content'] for message in agent.messages)
    assert_ledger_agrees(llm, result)


def test_child_exhaustion_is_not_wrapped_as_success_and_next_run_has_fresh_limits(client_factory, tmp_path):
    llm, calls = client_factory(None, lambda request, number: delegate() if number == 1 else success())
    agent = agent_for(llm, tmp_path, tools=[AgentTool()])
    first = agent.chat_result('child has no request left', budget_limits=BudgetLimits(requests=1))
    assert len(calls) == 1 and first.finish_reason == 'budget_exceeded'
    assert first.status == 'failed' and first.budget['groups']['actual']['requests'] == 1
    assert not any('[Sub-agent completed]' in str(message.get('content', '')) for message in agent.messages)
    first_ids = set(first.budget['attempts'])
    second = agent.chat_result('new independent top-level run', budget_limits=BudgetLimits(requests=1))
    assert len(calls) == 2 and second.status == 'completed'
    assert second.budget['groups']['actual']['requests'] == 1
    assert first_ids.isdisjoint(second.budget['attempts'])
    assert len(llm.usage_ledger) == 2


def old_history(agent):
    agent.messages[:] = [{'role': 'user' if i % 2 == 0 else 'assistant',
                          'content': f'prior-{i}: ' + 'archived independent evidence ' * (180 if i < 4 else 1)}
                         for i in range(8)]


def test_real_context_summary_requests_consume_shared_run_budget(client_factory, tmp_path):
    llm, calls = client_factory(None, lambda request, number: success())
    agent = agent_for(llm, tmp_path, max_context_tokens=4000)
    old_history(agent)
    result = agent.chat_result('latest short task', budget_limits=BudgetLimits(requests=20))
    assert result.status == 'completed', (result.finish_reason, str(result.error))
    summary_calls = [call for call in calls if call['payload']['messages'][0]['content'] == SUMMARY_SYSTEM_PROMPT]
    assert summary_calls and len(calls) > len(summary_calls)
    assert result.budget['groups']['actual']['requests'] == len(calls)
    assert_ledger_agrees(llm, result)
    assert list(tmp_path.rglob('history-*.json'))  # Real lossless canonical archive path.


def test_summary_budget_exceeded_never_falls_back_then_sends_final_request(client_factory, tmp_path):
    llm, calls = client_factory(None, lambda request, number: success())
    agent = agent_for(llm, tmp_path, max_context_tokens=4000)
    old_history(agent)
    result = agent.chat_result('latest short task', budget_limits=BudgetLimits(requests=1))
    assert result.status == 'failed' and result.finish_reason == 'budget_exceeded'
    assert len(calls) == 1 and calls[0]['payload']['messages'][0]['content'] == SUMMARY_SYSTEM_PROMPT
    assert result.budget['groups']['actual']['requests'] == 1
    assert_ledger_agrees(llm, result)


def test_unsupported_model_client_rejected_before_chat_or_any_usage(tmp_path):
    class Unsupported:
        def __init__(self):
            self.calls = 0
        def chat(self, **kwargs):
            self.calls += 1
            raise AssertionError('Unsupported provider was called')
    client = Unsupported()
    agent = agent_for(client, tmp_path)
    result = agent.chat_result('must not invoke', budget_limits=BudgetLimits(requests=2))
    assert client.calls == 0
    assert result.status == 'failed' and result.finish_reason == 'budget_exceeded'
    assert result.budget['report']['requests_remaining'] == 2
    assert not result.budget['attempts']
    assert agent.last_result is result


def test_child_write_before_exhaustion_is_retained_and_parent_records_started_failure(client_factory, tmp_path):
    def tool_reply(calls):
        payload = chunk()
        payload['choices'][0]['delta'] = {'tool_calls': [
            {'index': i, 'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}
            for i, (call_id, name, arguments) in enumerate(calls)]}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=event(payload)
            + event(chunk(usage={'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15})) + b'data: [DONE]\n\n')
    def respond(request, number):
        if number == 1:
            return tool_reply([('started-child', 'agent', {'task': 'write child evidence'}),
                               ('unstarted-write', 'write_file', {'file_path': 'must-not-start.txt', 'content': 'bad'})])
        return tool_reply([('actual-child-write', 'write_file', {'file_path': 'child-written.txt', 'content': 'retained child side effect'})])
    llm, calls = client_factory(None, respond)
    agent = agent_for(llm, tmp_path, tools=[AgentTool(), WriteFileTool(SessionState(workspace_root=tmp_path))])
    result = agent.chat_result('delegate then write pending', budget_limits=BudgetLimits(requests=2))
    assert result.finish_reason == 'budget_exceeded' and len(calls) == 2
    assert (tmp_path/'child-written.txt').read_text(encoding='utf-8') == 'retained child side effect'
    assert not (tmp_path/'must-not-start.txt').exists()
    tools = {m['tool_call_id']: m['content'] for m in agent.messages if m['role'] == 'tool'}
    assert 'budget' in tools['started-child'] and 'before start' not in tools['started-child']
    assert 'started' in tools['started-child']
    assert 'cancelled before start' in tools['unstarted-write']
    errors = {e['call_id']: e for e in result.tool_errors if e['call_id']}
    assert errors['started-child']['status'] == 'failed'
    assert errors['unstarted-write']['status'] == 'cancelled'
