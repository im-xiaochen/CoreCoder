"""Real commands; assert logs before releasing a quiet running check."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from corecoder.project_checks import ProjectChecks


def wait_for(read, predicate, seconds=5):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            last = read()
            if predicate(last):
                return last
        except (OSError, ValueError):
            pass
        time.sleep(.02)
    summary = {k: (f'{len(v)} chars' if k in {'stdout','stderr'} else v) for k,v in last.items()} if isinstance(last,dict) else last
    raise AssertionError(f'incremental evidence not visible within {seconds}s: {summary!r}')


@pytest.fixture
def live_check(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    root.mkdir()
    config = root / '.corecoder/checks.json'
    config.parent.mkdir()
    store = tmp_path / 'records'
    signal = tmp_path / 'signal'
    signal.mkdir()
    monkeypatch.setenv('CORECODER_DATA_DIR', str(tmp_path / 'private-data'))
    exe = getattr(sys, '_base_executable', sys.executable).replace('\\', '/')
    writer = Path(__file__).with_name('pr13_check_writer.py')
    def start(large=False):
        config.write_text(json.dumps({'version':1,'checks':[{'id':'incremental','name':'incremental',
            'command':f'"{exe}" -u "{writer.as_posix()}" "{signal.as_posix()}"' + (' --large' if large else ''),
            'timeout':20}]}), encoding='utf-8')
        checks = ProjectChecks(root, store)
        result, errors = [], []
        def work():
            try:
                result.append(checks.run('incremental', checks.inspect()['token'], authorized=True, run_id='r'*32, session_id='s'*32))
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return checks, thread, result, errors
    yield root, store, signal, start
    (signal / 'release').touch()


def running_record(store):
    paths = list(store.glob('*.json'))
    if len(paths) != 1:
        return None
    return json.loads(paths[0].read_text(encoding='utf-8'))


def test_quiet_running_command_persists_both_streams_and_recovery_prefix(live_check, tmp_path):
    root, store, signal, start = live_check
    checks, thread, result, errors = start()
    try:
        wait_for(lambda: signal / 'started', lambda path: path.exists())
        snapshot = wait_for(lambda: running_record(store), lambda row: row is not None
            and 'PR13 stdout before release' in row['stdout'] and 'PR13 stderr before release' in row['stderr'], seconds=3)
        assert thread.is_alive() and not (signal / 'release').exists()
        assert snapshot['status'] == 'running' and snapshot['ended_at'] is None
        assert checks.get(snapshot['id'])['stdout'] == snapshot['stdout']
        recovered_store = tmp_path / 'crash-prefix'
        recovered_store.mkdir()
        (recovered_store / (snapshot['id'] + '.json')).write_text(json.dumps(snapshot), encoding='utf-8')
    finally:
        (signal / 'release').touch()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert result[0]['status'] == 'failed' and result[0]['exit_code'] == 7
    assert 'PR13 final stdout' in checks.get(snapshot['id'])['stdout']
    # This intentionally simulates a persisted running prefix, not OS strong-kill.
    home = tmp_path / 'fake-home'
    home.mkdir()
    (home / '.env').write_text('', encoding='utf-8')
    env = {name: os.environ[name] for name in ['PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'] if name in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(Path(__file__).resolve().parents[3]),
               CORECODER_DATA_DIR=str(home / 'data'))
    code = 'from corecoder.project_checks import ProjectChecks; import sys,json; c=ProjectChecks(sys.argv[1],sys.argv[2]);c.recover();c.recover();print(json.dumps(c.get(sys.argv[3])))'
    recovered = subprocess.run([sys.executable,'-c',code,str(root),str(recovered_store),snapshot['id']],
                               cwd=home,env=env,capture_output=True,text=True,encoding='utf-8',timeout=15,check=True)
    value = json.loads(recovered.stdout)
    assert value['status'] == 'interrupted'
    assert value['stdout'] == snapshot['stdout'] and value['stderr'] == snapshot['stderr']
    assert (signal / 'execution-count').read_text(encoding='utf-8').splitlines() == ['started']


def test_running_large_log_is_bounded_and_final_status_is_real(live_check):
    _, store, signal, start = live_check
    checks, thread, result, errors = start(large=True)
    try:
        wait_for(lambda: signal / 'started', lambda path: path.exists())
        snapshot = wait_for(lambda: running_record(store), lambda row: row is not None and row['truncated'], seconds=3)
        assert thread.is_alive() and snapshot['status'] == 'running'
        assert 0 < len(snapshot['stdout'].encode('utf-8')) + len(snapshot['stderr'].encode('utf-8')) <= 2*1024*1024
        assert 'PR13 stdout before release' in snapshot['stdout']
    finally:
        (signal / 'release').touch()
        thread.join(5)
    assert not thread.is_alive() and not errors
    final = checks.get(snapshot['id'])
    assert final['status'] == 'failed' and final['exit_code'] == 7 and final['truncated']
    assert len(final['stdout'].encode('utf-8')) + len(final['stderr'].encode('utf-8')) <= 2*1024*1024


def test_exact_runner_limit_is_complete_and_never_signals_truncation(tmp_path):
    from corecoder.command_runner import run_command
    script = tmp_path / 'exact_output.py'
    script.write_text('import sys,time\nfor i in range(256):\n sys.stdout.buffer.write(b"Q"*8192);sys.stdout.buffer.flush();time.sleep(.01)\n', encoding='utf-8')
    exe = getattr(sys, '_base_executable', sys.executable).replace('\\', '/')
    notices, output = [], []
    result = run_command(f'"{exe}" "{script.as_posix()}"', str(tmp_path), timeout=10,
                         on_output=lambda stream,text,seq: output.append(text), on_truncated=lambda:notices.append(True))
    assert result.status == 'exited' and result.exit_code == 0
    assert len(result.stdout.encode('utf-8')) == 2*1024*1024 and result.stderr == ''
    assert not result.truncated and not notices
    assert ''.join(output) == result.stdout
