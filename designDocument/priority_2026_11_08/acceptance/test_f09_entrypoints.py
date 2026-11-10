"""Actual CLI/Agent/Web entrypoints with offline scripted provider responses."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import pytest
from corecoder import Agent, Config, cli
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse, ToolCall
from corecoder.permissions import Permission
from corecoder.state import SessionState
from retrieval_fixture import build_repository


@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=build_repository(tmp_path/'repo')['root']
    monkeypatch.setenv('CORECODER_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setattr(Config,'from_env',classmethod(lambda cls:Config(api_key='offline',model='scripted')))
    return root


def mapping_turn():
    return LLMResponse(tool_calls=[ToolCall('map','repo_map',{'query':'invoice'})])


def test_real_cli_plan_can_use_repo_map_without_write_approval(setup,monkeypatch):
    agent=Agent(llm=ScriptedLLM([mapping_turn(),LLMResponse(content='located')]),state=SessionState(workspace_root=setup,cwd=setup),permission=Permission(allow_all=False),hooks=None)
    agent.plan_mode=True
    inputs=iter(['locate invoice','quit'])
    monkeypatch.setattr(cli,'pt_prompt',lambda *a,**k:next(inputs))
    cli._repl(agent,Config(api_key='offline',model='scripted'))
    assert agent.last_result.status=='completed'
    assert any(m.get('role')=='tool' and 'src/consumer.py:3' in str(m.get('content')) for m in agent.messages)


def test_child_uses_same_map_without_parent_state_or_permission_expansion(setup):
    script=[LLMResponse(tool_calls=[ToolCall('child','agent',{'task':'locate invoice'})]),mapping_turn(),LLMResponse(content='child located invoice'),LLMResponse(content='parent completed')]
    agent=Agent(llm=ScriptedLLM(script),state=SessionState(workspace_root=setup,cwd=setup),permission=Permission(allow_all=True),hooks=None)
    result=agent.chat_result('delegate read-only location')
    assert result.status=='completed' and not result.tool_errors
    assert any(m.get('role')=='tool' and 'child located invoice' in str(m.get('content')) for m in agent.messages)
    assert agent.state.cwd==setup


def test_chat_tool_request_cannot_read_project_files(setup,monkeypatch):
    agent=Agent(llm=ScriptedLLM([mapping_turn(),LLMResponse(content='chat response')]),state=SessionState(workspace_root=setup,cwd=setup),hooks=None)
    agent.chat_mode=True
    opened=[];real=Path.open
    def guarded(path,*args,**kwargs):
        if path.is_relative_to(setup):
            opened.append(str(path));raise AssertionError('chat accessed project')
        return real(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    agent.chat_result('do not access project')
    assert not opened
    assert not any('src/consumer.py:3' in str(m.get('content')) for m in agent.messages)
    assert agent._tool_schemas()==[]


def test_actual_http_run_uses_workspace_map_in_plan(setup,tmp_path):
    from web_backend.service import Service
    from web_backend.server import make_handler
    service=Service(setup,tmp_path/'store',llm_factory=lambda:ScriptedLLM([mapping_turn(),LLMResponse(content='located')]),extensions=False)
    sid=service.create()['id'];service.get(sid).set_mode('plan')
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(service,setup));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,data=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        conn.request('POST' if data is not None else 'GET','/api/'+path,body=json.dumps(data) if data is not None else None,headers={'X-CoreCoder-Token':service.token,'Content-Type':'application/json'})
        response=conn.getresponse();result=json.loads(response.read());conn.close();assert response.status<400,result;return result
    try:
        run=request('runs',{'id':sid,'prompt':'locate invoice'});deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            view=request('run?id='+run['id'])
            if view['status']!='running':break
            time.sleep(.03)
        assert view['status']=='completed'
        snapshot=request('session?id='+sid)
        assert any(m.get('role')=='tool' and 'src/consumer.py:3' in str(m.get('content')) for m in snapshot['messages'])
    finally:server.shutdown();server.server_close();thread.join(2)
