"""Independent real-process F10 checks; no providers or extensions."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from corecoder.project_checks import ProjectChecks, CheckError


@pytest.fixture
def checks(tmp_path):
    workspace = tmp_path / 'project'; workspace.mkdir(); (workspace / '.corecoder').mkdir()
    config = workspace / '.corecoder/checks.json'
    script = Path(__file__).with_name('check_fixture.py')
    exe = getattr(sys, '_base_executable', sys.executable).replace('\\', '/')
    def configure(modes=('pass', 'fail')):
        config.write_text(json.dumps({'version': 1, 'checks': [
            {'id': mode, 'name': mode, 'command': f'"{exe}" -u "{script.as_posix()}" {mode} --root "{(workspace / mode).as_posix()}"', 'timeout': 1 if mode == 'wait' else 10, 'files': ['*.py']}
            for mode in modes]}), encoding='utf-8')
    configure()
    return ProjectChecks(workspace, tmp_path / 'results'), workspace, config, configure


def test_actual_exit_codes_preserved_logs_and_no_text_based_success(checks):
    api, root, _, _ = checks; token = api.inspect()['token']
    for ident, code, status in [('pass', 0, 'passed'), ('fail', 7, 'failed')]:
        result = api.run(ident, token, authorized=True, files=['x.py'], run_id='offline-run', session_id='offline-session')
        assert result['exit_code'] == code and result['status'] == status and result['command_status'] == 'exited'
        assert 'independent stdout' in result['stdout'] and 'independent stderr' in result['stderr']
        assert result['repair_attempts'] == 0
        assert api.get(result['id']) == result
    assert 'all tests passed' in result['stdout'] and result['status'] == 'failed'


def test_authorization_stale_config_and_empty_selection_do_not_execute(checks):
    api, root, config, _ = checks; token = api.inspect()['token']
    assert api.select([])['checks'] == []
    assert api.select(None, [])['checks'] == []
    assert api.select(None, ['a.txt'])['checks'] == []
    for authorized in [False, None, 'true', 1]:
        with pytest.raises(CheckError): api.run('pass', token, authorized=authorized)
    config.write_text(config.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    with pytest.raises(CheckError): api.run('pass', token, authorized=True)
    assert not (root / 'pass').exists()
    assert not api.store.exists()


@pytest.mark.parametrize('stop', ['pre-cancel', 'cancel', 'timeout'])
def test_real_wait_command_has_distinct_stop_status_and_stopped_heartbeat(checks, stop):
    api, root, _, configure = checks; configure(('wait',)); cancel = threading.Event(); results = []
    if stop == 'pre-cancel': cancel.set()
    thread = threading.Thread(target=lambda: results.append(api.run('wait', api.inspect()['token'], authorized=True, cancel=cancel)), daemon=True)
    thread.start()
    heartbeat = root / 'wait/heartbeat'; deadline = time.monotonic() + 5
    if stop == 'cancel':
        while not heartbeat.exists() and time.monotonic() < deadline: time.sleep(.01)
        assert heartbeat.exists(); cancel.set()
    thread.join(5)
    assert not thread.is_alive()
    expected = 'timeout' if stop == 'timeout' else 'cancelled'
    assert results[0]['status'] == results[0]['command_status'] == expected
    assert api.get(results[0]['id'])['status'] == expected
    if stop == 'pre-cancel': assert not heartbeat.exists()
    else:
        assert heartbeat.exists(); size = heartbeat.stat().st_size
        time.sleep(.15); assert heartbeat.stat().st_size == size


def test_fresh_process_recovers_interrupted_record_without_reexecuting(checks):
    api, root, _, configure = checks; configure(('repair',))
    result = api.run('repair', api.inspect()['token'], authorized=True)
    counter = root / 'repair/checks-executed'; assert counter.read_bytes() == b'c'
    # Simulate the durable running record left by a crash after its command side effect.
    path = api.store / (result['id'] + '.json')
    record = json.loads(path.read_text()); record.update(status='running', ended_at=None)
    path.write_text(json.dumps(record), encoding='utf-8')
    code = 'from corecoder.project_checks import ProjectChecks;import json;api=ProjectChecks('+repr(str(root))+','+repr(str(api.store))+');api.recover();print(json.dumps(api.get('+repr(result['id'])+')))'
    for _ in range(2):
        child = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
        recovered = json.loads(child.stdout)
        assert recovered['status'] == recovered['command_status'] == 'interrupted'
        assert recovered['stdout'] == result['stdout'] and recovered['stderr'] == result['stderr']
        assert counter.read_bytes() == b'c'


def test_long_log_persisted_and_internal_ids_only(checks):
    api, _, _, configure = checks; configure(('long-log',))
    result = api.run('long-log', api.inspect()['token'], authorized=True)
    assert result['status'] == 'passed'
    assert 'row-0000' in result['stdout'] and 'independent-log-end' in api.get(result['id'])['stdout']
    for ident in ['../secret', '/etc/passwd', 'a' * 32 + '.json', None]:
        with pytest.raises(CheckError): api.get(ident)
    oversized = api.store / ('f' * 32 + '.json'); oversized.write_bytes(b' ' * (16 * 1024 * 1024 + 1))
    with pytest.raises(CheckError): api.get('f' * 32)


@pytest.mark.parametrize('bad', ['json', 'cwd', 'timeout', 'duplicate'])
def test_invalid_config_does_not_execute(checks, bad):
    api, root, config, _ = checks
    payload = json.loads(config.read_text())
    if bad == 'cwd': payload['checks'][0]['cwd'] = '../outside'
    elif bad == 'timeout': payload['checks'][0]['timeout'] = True
    elif bad == 'duplicate': payload['checks'][1]['id'] = payload['checks'][0]['id']
    config.write_text('{' if bad == 'json' else json.dumps(payload), encoding='utf-8')
    with pytest.raises(CheckError): api.inspect()
    assert not (root / 'pass').exists()
