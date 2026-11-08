"""F10 HTTP lifecycle and persisted diagnostics; real local commands."""
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode

import pytest
from test_f06_http_independent import project, http


@pytest.fixture
def webchecks(project):
    repo, factory = project
    path = repo.root / '.corecoder/checks.json'; path.parent.mkdir(exist_ok=True)
    script = Path(__file__).with_name('check_fixture.py'); exe = getattr(sys, '_base_executable', sys.executable).replace('\\', '/')
    path.write_text(json.dumps({'version': 1, 'checks': [
        {'id': mode, 'name': mode, 'command': f'"{exe}" -u "{script.as_posix()}" {mode} --root "{(repo.root / mode).as_posix()}"', 'files': ['*.py'], 'timeout': 8}
        for mode in ['pass', 'fail', 'wait']]}), encoding='utf-8')
    svc = factory(); sid = svc.create()['id']
    return repo, factory, path, svc, sid


def terminal(request, rid):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        code, view = request('run?' + urlencode({'id': rid}))
        assert code == 200, view
        if view['status'] in ['completed', 'failed', 'cancelled', 'interrupted']: return view
        time.sleep(.025)
    raise AssertionError('check run did not terminate')


def test_http_real_batch_results_history_and_restart_read_never_reexecutes(webchecks):
    _, factory, _, svc, sid = webchecks
    with http(svc) as request:
        code, config = request('checks/config?id=' + sid); assert code == 200
        code, run = request('checks/start', {'id': sid, 'check_ids': ['pass', 'fail'], 'files': ['x.py'], 'token': config['token'], 'confirm': True})
        assert code == 201, run
        assert terminal(request, run['id'])['status'] == 'failed'
        code, history = request('checks/history?id=' + sid); assert code == 200 and len(history) == 2
        bycheck = {item['check_id']: item for item in history}
        assert bycheck['pass']['exit_code'] == 0 and bycheck['pass']['status'] == 'passed'
        assert bycheck['fail']['exit_code'] == 7 and bycheck['fail']['status'] == 'failed'
        result_ids = {item['id'] for item in history}
        for item in history:
            assert item['run_id'] == run['id'] and item['session_id'] == sid
            assert 'stdout' not in item
            code, full = request('checks/result?result_id=' + item['id'])
            assert code == 200 and 'independent stderr' in full['stderr']
    restored = factory()
    with http(restored) as request:
        for _ in range(2):
            code, history = request('checks/history?id=' + sid)
            assert code == 200 and {item['id'] for item in history} == result_ids
            for item in history: assert request('checks/result?result_id=' + item['id'])[0] == 200
        assert request('checks/result?result_id=../outside')[0] >= 400


def test_http_notrun_authorization_empty_matching_and_config_drift(webchecks):
    repo, _, path, svc, sid = webchecks
    with http(svc) as request:
        token = request('checks/config?id=' + sid)[1]['token']
        payload = {'id': sid, 'check_ids': ['pass'], 'files': None, 'token': token, 'confirm': True}
        for patch, expected in [({'confirm': False}, 403), ({'check_ids': []}, 400), ({'files': []}, 400), ({'files': ['x.txt']}, 400)]:
            assert request('checks/start', {**payload, **patch})[0] == expected
            assert not svc.execution.locked() and not svc.get(sid).busy
        assert not svc.runs.runs, 'not-run requests must not become completed runs'
        svc.get(sid).set_mode('chat'); assert request('checks/config?id=' + sid)[0] == 403
        svc.get(sid).set_mode('plan'); assert request('checks/start', payload)[0] == 403
        svc.get(sid).set_mode('code'); path.write_text(path.read_text() + '\n')
        assert request('checks/start', payload)[0] == 409
        assert not (repo.root / 'pass').exists()
        assert not svc.project_checks.history(sid)


def test_http_stop_check_once_and_keep_cancelled_log(webchecks):
    repo, _, _, svc, sid = webchecks
    with http(svc) as request:
        token = request('checks/config?id=' + sid)[1]['token']
        code, run = request('checks/start', {'id': sid, 'check_ids': ['wait'], 'token': token, 'confirm': True})
        assert code == 201
        marker = repo.root / 'wait/heartbeat'; deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline: time.sleep(.01)
        assert marker.exists(); assert request('stop', {'id': sid})[0] == 200
        assert terminal(request, run['id'])['status'] == 'cancelled'
        history = request('checks/history?id=' + sid)[1]
        assert len(history) == 1 and history[0]['status'] == history[0]['command_status'] == 'cancelled'
        full = request('checks/result?result_id=' + history[0]['id'])[1]
        assert 'independent stdout' in full['stdout']
        size = marker.stat().st_size; time.sleep(.15); assert marker.stat().st_size == size


def test_check_start_persistence_failure_releases_lock_and_never_runs(webchecks, monkeypatch):
    repo, _, _, svc, sid = webchecks; session = svc.get(sid); before = session.run_id
    token = svc.project_checks.inspect()['token']
    def fail(): raise OSError('independent injected disk failure')
    monkeypatch.setattr(session, 'persist', fail)
    with pytest.raises(OSError): svc.checks_start(sid, {'check_ids': ['pass'], 'token': token, 'confirm': True})
    assert not svc.execution.locked() and not session.busy and session.run_id == before
    assert not (repo.root / 'pass').exists()
    assert all(run['status'] == 'failed' for run in svc.runs.runs.values())
