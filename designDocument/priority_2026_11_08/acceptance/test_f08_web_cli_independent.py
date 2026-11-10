"""F08 incremental persistence/HTTP/CLI failure boundaries, offline SDK."""
import copy
import json
import threading
import time

import pytest
from prompt_toolkit.history import InMemoryHistory

from corecoder import Config, cli
from corecoder.budget import BudgetLimits, RunBudget
from corecoder.llm import budget_scope
from test_f08_openai_independent import client_factory, success
from test_f08_agent_independent import agent_for, old_history
from test_f11_entrypoints import http
from web_backend.service import Service


@pytest.fixture
def web(tmp_path, monkeypatch, client_factory):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    monkeypatch.setattr(Config, 'from_env', classmethod(lambda cls: Config(api_key='offline-key', model='independent', max_tokens=32)))
    def make(handler=lambda request, number: success()):
        llm, calls = client_factory(None, handler)
        service = Service(workspace, tmp_path/'store', llm_factory=lambda: llm, extensions=False)
        sid = service.create()['id']
        return service, sid, llm, calls
    return make


def wait_done(service, rid):
    until = time.monotonic()+6
    while service.runs.view(rid)['status'] == 'running' and time.monotonic() < until:
        time.sleep(.01)
    assert service.runs.view(rid)['status'] != 'running'
    assert not service.get(service.runs.view(rid)['session_id']).busy


def test_provider_blocked_http_and_disk_show_inflight_then_final_actual(web):
    entered, release = threading.Event(), threading.Event()
    def handler(request, number):
        entered.set()
        assert release.wait(5)
        return success()
    service, sid, llm, calls = web(handler)
    try:
        with http(service) as request:
            code, run = request('runs', {'id':sid, 'prompt':'blocked physical request', 'budget':{'requests':1}})
            assert code == 201 and entered.wait(3)
            code, view = request('run?id='+run['id'])
            assert code == 200 and view['budget']['groups']['in_flight']['requests'] == 1
            disk = json.loads((service.runs.root/(run['id']+'.json')).read_text(encoding='utf-8'))
            assert disk['budget'] == view['budget']
            release.set()
            wait_done(service, run['id'])
    finally:
        release.set()
    final = service.runs.view(run['id'])
    assert len(calls) == 1 and final['budget']['groups']['actual']['requests'] == 1
    assert final['budget']['groups']['in_flight']['requests'] == 0
    assert len(llm.usage_ledger) == 1


def test_initial_checkpoint_failure_preserves_run_reference_and_releases_lock(web, monkeypatch):
    service, sid, llm, calls = web()
    old = service.runs.create(sid)
    service.runs.status(old, 'completed')
    service.get(sid).run_id = old
    original = service.runs.budget
    monkeypatch.setattr(service.runs, 'budget', lambda *args: (_ for _ in ()).throw(OSError('initial snapshot fault')))
    with pytest.raises(OSError):
        service.start(sid, 'initial fault', legacy_events=False, budget={'requests':1})
    assert not calls and not llm.usage_ledger
    assert service.get(sid).run_id == old and not service.get(sid).busy
    assert all(run['status'] != 'running' for run in service.runs.runs.values())
    assert service.execution.acquire(False)
    service.execution.release()
    monkeypatch.setattr(service.runs, 'budget', original)


def test_reserve_checkpoint_failure_has_zero_transport_and_releases_unsent(client_factory):
    attempts = []
    def fail(snapshot):
        attempts.append(copy.deepcopy(snapshot))
        raise OSError('reserve disk unavailable')
    budget = RunBudget(BudgetLimits(requests=1), on_change=fail)
    llm, calls = client_factory(budget, lambda request, number: success())
    with budget_scope(budget), pytest.raises(OSError):
        llm.chat([{'role':'user','content':'must not transmit'}])
    assert not calls and not llm.usage_ledger
    assert attempts[0]['groups']['in_flight']['requests'] == 1
    assert budget.snapshot()['report']['requests_remaining'] == 1
    assert all(item['state'] == 'released' for item in budget.snapshot()['attempts'].values())


def test_settle_single_disk_fault_final_snapshot_preserves_actual_and_f01_on_restart(web, monkeypatch):
    service, sid, llm, calls = web()
    original = service.runs.budget
    failed = []
    def fail_once(rid, snapshot):
        if snapshot['groups']['actual']['requests'] and not failed:
            failed.append(rid)
            raise OSError('one settle checkpoint write failure')
        return original(rid, snapshot)
    monkeypatch.setattr(service.runs, 'budget', fail_once)
    rid = service.start(sid, 'settle once', legacy_events=False, budget={'requests':1})
    wait_done(service, rid)
    assert failed and len(calls) == 1
    reopened = Service(service.workspace, service.store, llm_factory=lambda: llm, extensions=False)
    view = reopened.runs.view(rid)
    assert view['budget']['groups']['actual']['requests'] == 1
    assert view['budget']['groups']['in_flight']['requests'] == 0
    assert len(reopened.get(sid).agent.llm.usage_ledger) == 1
    assert reopened.get(sid).agent.llm.usage_ledger[0].usage_known


def test_thread_start_fault_preserves_previous_run_and_no_zombie(web, monkeypatch):
    service, sid, llm, calls = web()
    old = service.runs.create(sid)
    service.runs.status(old, 'completed')
    service.get(sid).run_id = old
    real = threading.Thread.start
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, 'start', lambda self: (_ for _ in ()).throw(RuntimeError('thread start fault')))
        with pytest.raises(RuntimeError):
            service.start(sid, 'thread fault', legacy_events=False, budget={'requests':1})
    assert threading.Thread.start is real
    assert not calls and not llm.usage_ledger
    assert service.get(sid).run_id == old and not service.get(sid).busy
    assert all(run['status'] != 'running' for run in service.runs.runs.values())
    assert service.execution.acquire(False)
    service.execution.release()


@pytest.mark.parametrize('budget', [{}, {'requests':None,'total_tokens':None,'cost':None}])
def test_empty_budget_means_default_without_controller(web, budget):
    service, sid, llm, calls = web()
    # Default adapter explicitly records unbudgeted invocation, avoiding a fake budget.
    from corecoder.demo import ScriptedLLM
    from corecoder.llm import LLMResponse
    service.get(sid).agent.llm = ScriptedLLM([LLMResponse(content='default compatible')])
    with http(service) as request:
        code, run = request('runs', {'id':sid,'prompt':'default','budget':budget})
        assert code == 201
        wait_done(service, run['id'])
    assert service.runs.view(run['id'])['status'] == 'completed'
    assert service.runs.view(run['id']).get('budget') is None


def test_http_invalid_budget_creates_no_run_and_zero_cap_no_transport(web):
    service, sid, llm, calls = web()
    before = set(service.runs.runs)
    with http(service) as request:
        for invalid in ({'requests':True},{'cost':'NaN'},{'unknown_policy':'invalid'}):
            code, payload = request('runs', {'id':sid,'prompt':'invalid','budget':invalid})
            assert code == 400
            assert set(service.runs.runs) == before
        code, run = request('runs', {'id':sid,'prompt':'zero','budget':{'requests':0}})
        assert code == 201
        wait_done(service, run['id'])
    assert not calls and not llm.usage_ledger
    assert service.runs.view(run['id'])['budget']['report']['requests_remaining'] == 0


@pytest.mark.parametrize('cap', [0, 20])
def test_actual_repl_compact_uses_explicit_budget_scope(client_factory, tmp_path, monkeypatch, capsys, cap):
    llm, calls = client_factory(None, lambda request, number: success())
    agent = agent_for(llm, tmp_path, max_context_tokens=4000)
    old_history(agent)
    monkeypatch.setattr(cli, 'FileHistory', lambda *a,**kw: InMemoryHistory())
    inputs = iter(['/compact','quit'])
    monkeypatch.setattr(cli, 'pt_prompt', lambda *a,**kw: next(inputs))
    cli._repl(agent, Config(api_key='offline', model='independent'), budget_limits=BudgetLimits(requests=cap))
    output = capsys.readouterr().out
    assert 'Budget:' in output
    if cap == 0:
        assert not calls and not llm.usage_ledger
        assert 'request limit' in output
    else:
        assert calls and 'Compressed:' in output


def test_actual_cli_flags_zero_budget_structured_exit_before_send(client_factory, tmp_path, monkeypatch, capsys):
    llm, calls = client_factory(None, lambda request, number: success())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Config, 'from_env', classmethod(lambda cls: Config(api_key='offline-key', model='independent', max_tokens=32)))
    monkeypatch.setattr(cli, 'load_hooks', lambda: None)
    monkeypatch.setattr(cli, 'load_mcp_tools', lambda: [])
    monkeypatch.setattr(cli, 'create_tools', lambda state: [])
    monkeypatch.setattr('sys.argv', ['corecoder','--budget-requests','0','--budget-tokens','100','--budget-cost','0.1','--budget-unknown-policy','allow_unknown','--json','-p','zero'])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 1 and not calls
    result = json.loads(capsys.readouterr().out)
    assert result['finish_reason'] == 'budget_exceeded'
    assert result['budget']['limits'] == {'requests':0,'total_tokens':100,'cost':'0.1','unknown_policy':'allow_unknown'}
