"""Wire-level protocol acceptance; actual SDK, Agent, archive and context."""
import copy
import json

import pytest

from corecoder.agent import Agent
from corecoder.budget import BudgetExceeded, BudgetLimits
from corecoder.hooks import Hooks
from corecoder.permissions import Permission
from corecoder.state import SessionState
from corecoder.tools.read import ReadFileTool
from corecoder import session as archive
from sdk_fixture import httpx, llm_with_transport, stream


def tool_stream():
    return stream({'reasoning':'DISPLAY_GENERIC'}, {'reasoning_content':'PRIVATE_TOOL_'},
        {'reasoning_content':'REASON'}, {'tool_calls':[{'index':0,'id':'read-proof','type':'function',
        'function':{'name':'read_file','arguments':'{"file_path":"note.txt"}'}}]})


def create_agent(llm,root):
    state=SessionState(workspace_root=root)
    return Agent(llm,tools=[ReadFileTool(state=state)],state=state,
                 permission=Permission(),hooks=Hooks([],[]))


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    (tmp_path/'note.txt').write_text('public tool proof',encoding='utf-8')
    monkeypatch.setattr(archive,'SESSIONS_DIR',tmp_path/'sessions')
    return tmp_path


def test_real_agent_tool_round_only_replays_protocol_reasoning_with_no_internal_fields(workspace,monkeypatch):
    llm,calls,sdk=llm_with_transport(monkeypatch,lambda request,n: tool_stream() if n==1 else stream({'content':'final answer','reasoning_content':'FINAL_PRIVATE'}))
    try:
        agent=create_agent(llm,workspace)
        shown=[]
        result=agent.chat_result('read proof',on_reasoning=shown.append,budget_limits=BudgetLimits(requests=2))
        assert result.status=='completed' and len(calls)==2
        assistant=next(m for m in calls[1]['messages'] if m.get('tool_calls'))
        assert assistant['reasoning_content']=='PRIVATE_TOOL_REASON'
        assert set(assistant)<={'role','content','tool_calls','reasoning_content'}
        assert 'DISPLAY_GENERIC' in ''.join(shown)
        assert 'DISPLAY_GENERIC' not in json.dumps(agent.messages)
        assert 'FINAL_PRIVATE' not in json.dumps(agent.messages)
        assert result.budget['groups']['actual']['requests']==2
        assert len(llm.usage_ledger)==2
    finally:
        sdk.close()


@pytest.mark.parametrize('change',[None,'model','endpoint'])
def test_archive_roundtrip_preserves_only_confirmed_same_source_replay(workspace,monkeypatch,change):
    llm,calls,sdk=llm_with_transport(monkeypatch,lambda request,n: tool_stream() if n==1 else stream({'content':'done'}))
    agent=create_agent(llm,workspace)
    try:
        assert agent.chat_result('read').status=='completed'
        sid=archive.save_session(agent.messages,llm.model,usage_ledger=llm.usage_ledger)
        saved=archive.load_session_full(sid)
        assert 'PRIVATE_TOOL_REASON' in json.dumps(saved['messages'])
        new_llm,new_calls,new_sdk=llm_with_transport(monkeypatch,lambda request,n: stream({'content':'restored'}),
             model='other-model' if change=='model' else llm.model,
             endpoint='https://other.invalid/v1' if change=='endpoint' else llm.endpoint)
        try:
            restored=create_agent(new_llm,workspace)
            restored.messages=copy.deepcopy(saved['messages'])
            new_llm.restore_usage(saved['usage_ledger'])
            assert restored.chat_result('follow up').status=='completed'
            wire=json.dumps(new_calls[0]['messages'])
            assert ('PRIVATE_TOOL_REASON' in wire)==(change is None)
            for message in new_calls[0]['messages']:
                if message['role']=='assistant':
                    assert set(message)<={'role','content','tool_calls','reasoning_content'}
        finally:
            new_sdk.close()
    finally:
        sdk.close()


def test_legacy_unknown_reasoning_not_sent_or_exposed_to_summary(workspace,monkeypatch):
    llm,calls,sdk=llm_with_transport(monkeypatch,lambda request,n: stream({'content':'summary or final'}))
    try:
        agent=create_agent(llm,workspace)
        agent.messages=[{'role':'user','content':'old task'},
            {'role':'assistant','content':None,'reasoning_content':'UNKNOWN_LEGACY_PRIVATE',
             'tool_calls':[{'id':'old','type':'function','function':{'name':'read_file','arguments':'{"file_path":"note.txt"}'}}]},
            {'role':'tool','tool_call_id':'old','content':'public evidence'},
            {'role':'assistant','content':'old answer'}]
        assert agent.chat_result('normal followup').status=='completed'
        assert 'UNKNOWN_LEGACY_PRIVATE' not in json.dumps(calls)
        # Actual context compression, leaving small recent turns so archive can fit.
        agent.messages += [{'role':'user' if i%2==0 else 'assistant',
                            'content':('large historical evidence '*220 if i<4 else 'recent short')} for i in range(8)]
        agent.context.max_tokens=4000
        before=len(calls)
        assert agent.chat_result('force context prepare').status=='completed'
        assert len(calls)>before+1
        assert 'UNKNOWN_LEGACY_PRIVATE' not in json.dumps(calls[before:])
    finally:
        sdk.close()


@pytest.mark.parametrize('message',["Unsupported parameter: temperature", "Unsupported value for 'temperature'"])
def test_temperature_fallback_real_requests_each_have_distinct_budget_attempt(workspace,monkeypatch,message):
    llm,calls,sdk=llm_with_transport(monkeypatch,lambda request,n: httpx.Response(400,json={'error':{'message':message}}) if n==1 else stream({'content':'adapted'}),temperature=.7)
    try:
        result=create_agent(llm,workspace).chat_result('temperature dialect',budget_limits=BudgetLimits(requests=2))
        assert result.status=='completed' and len(calls)==2
        assert calls[0]['temperature']==.7 and 'temperature' not in calls[1]
        assert len(llm.usage_ledger)==2
        assert len(result.budget['attempts'])==2
    finally:
        sdk.close()


def test_temperature_retry_denied_by_shared_request_cap_before_second_transport(workspace,monkeypatch):
    llm,calls,sdk=llm_with_transport(monkeypatch,lambda request,n: httpx.Response(400,json={'error':{'message':'Unsupported parameter: temperature'}}),temperature=.7)
    try:
        result=create_agent(llm,workspace).chat_result('one attempt',budget_limits=BudgetLimits(requests=1))
        assert result.status=='failed' and result.finish_reason=='budget_exceeded'
        assert len(calls)==1 and len(llm.usage_ledger)==1
    finally:
        sdk.close()


@pytest.mark.parametrize('rejected',['temperature_scale','top_temperature','max_completion_tokens'])
def test_composite_parameter_names_do_not_remove_temperature_or_translate_max_tokens(workspace,monkeypatch,rejected):
    llm,calls,sdk=llm_with_transport(monkeypatch,lambda request,n: httpx.Response(400,json={'error':{'message':'Unsupported parameter: '+rejected}}) if n==1 else stream({'content':'fallback'}),temperature=.7)
    try:
        result=create_agent(llm,workspace).chat_result('negative dialect match',budget_limits=BudgetLimits(requests=2))
        assert result.status=='completed' and len(calls)==2
        assert all(call['temperature']==.7 and call['max_tokens']==32 for call in calls)
        assert all('max_completion_tokens' not in call for call in calls)
    finally:
        sdk.close()


def test_effective_sdk_environment_endpoint_separates_same_model_history(workspace,monkeypatch):
    monkeypatch.setenv('OPENAI_BASE_URL','https://first.invalid/v1')
    llm,calls,sdk=llm_with_transport(monkeypatch,lambda request,n: tool_stream() if n==1 else stream({'content':'done'}),endpoint=None)
    try:
        agent=create_agent(llm,workspace)
        assert agent.chat_result('first endpoint').status=='completed'
        monkeypatch.setenv('OPENAI_BASE_URL','https://second.invalid/v1')
        other,new_calls,other_sdk=llm_with_transport(monkeypatch,lambda request,n: stream({'content':'second'}),endpoint=None)
        try:
            restored=create_agent(other,workspace)
            restored.messages=copy.deepcopy(agent.messages)
            assert restored.chat_result('new effective endpoint').status=='completed'
            assert 'PRIVATE_TOOL_REASON' not in json.dumps(new_calls)
        finally:
            other_sdk.close()
    finally:
        sdk.close()


def test_confirmed_reasoning_does_not_leak_to_summary_or_nonassistant_role(workspace,monkeypatch):
    llm,calls,sdk=llm_with_transport(monkeypatch,lambda request,n: tool_stream() if n==1 else stream({'content':'short summary final'}))
    try:
        agent=create_agent(llm,workspace)
        assert agent.chat_result('initial').status=='completed'
        generated=next(copy.deepcopy(m) for m in agent.messages if m.get('tool_calls'))
        invalid_role=copy.deepcopy(generated)
        invalid_role['role']='user'
        invalid_role['content']='role changed input'
        llm.chat([invalid_role])
        assert 'PRIVATE_TOOL_REASON' not in json.dumps(calls[-1])
        agent.messages += [{'role':'user' if i%2==0 else 'assistant',
                            'content':('large historical evidence '*220 if i<4 else 'recent short')} for i in range(8)]
        agent.context.max_tokens=4000
        before=len(calls)
        assert agent.chat_result('summarize existing tool history').status=='completed'
        assert len(calls)>before+1
        assert 'PRIVATE_TOOL_REASON' not in json.dumps(calls[before:])
        metadata=set(generated)-{'role','content','tool_calls','reasoning_content'}
        assert all(key not in json.dumps(calls[before:]) for key in metadata)
    finally:
        sdk.close()
