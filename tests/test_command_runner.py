import os
import sys
import threading
import time

from corecoder.command_runner import run_command


def command(script):
    exe = sys.executable.replace('\\', '/')
    return f'"{exe}" -u "{script.as_posix()}"'


def test_streams_before_exit_and_preserves_utf8(tmp_path):
    script = tmp_path / 'emit.py'
    script.write_text("import sys,time\nsys.stdout.buffer.write('你'.encode()[:1]); sys.stdout.flush(); time.sleep(.1); sys.stdout.buffer.write('你'.encode()[1:]); sys.stdout.flush(); sys.stderr.write('err'); sys.stderr.flush(); time.sleep(.5)", encoding='utf-8')
    events = []
    result = []
    thread = threading.Thread(target=lambda: result.append(run_command(command(script), tmp_path, on_output=lambda *v: events.append(v))))
    thread.start()
    deadline = time.monotonic() + 3
    while not events and time.monotonic() < deadline:
        time.sleep(.02)
    assert thread.is_alive()
    thread.join(5)
    assert result[0].stdout == '你'
    assert result[0].stderr == 'err'
    assert result[0].status == 'exited'
    assert [e[2] for e in events] == list(range(1, len(events) + 1))


def test_cancel_process_tree(tmp_path):
    child = tmp_path / 'child.py'
    heartbeat = tmp_path / 'heartbeat'
    child.write_text("import sys,time\np=sys.argv[1]\nwhile True:\n open(p,'a').write('x')\n time.sleep(.05)", encoding='utf-8')
    parent = tmp_path / 'parent.py'
    parent.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\ntime.sleep(30)", encoding='utf-8')
    cancel = threading.Event()
    results = []
    cmd = command(parent) + f' "{child.as_posix()}" "{heartbeat.as_posix()}"'
    thread = threading.Thread(target=lambda: results.append(run_command(cmd, tmp_path, cancel=cancel)))
    thread.start()
    deadline = time.monotonic() + 5
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert heartbeat.exists()
    cancel.set()
    thread.join(5)
    assert not thread.is_alive()
    assert results[0].status == 'cancelled'
    size = heartbeat.stat().st_size
    time.sleep(.3)
    assert heartbeat.stat().st_size == size

def test_large_dual_stream_and_timeout(tmp_path):
    script = tmp_path / 'large.py'
    script.write_text("import os\nos.write(1,b'a'*1500000)\nos.write(2,b'b'*1500000)", encoding='utf-8')
    result = run_command(command(script), tmp_path, timeout=10)
    assert result.status == 'exited'
    assert result.exit_code == 0
    assert result.truncated
    assert len((result.stdout + result.stderr).encode()) <= 2 * 1024 * 1024
    script.write_text('import time; time.sleep(20)', encoding='utf-8')
    result = run_command(command(script), tmp_path, timeout=.2)
    assert result.status == 'timeout'

def test_cancel_after_parent_exits_with_child_pipes(tmp_path):
    child = tmp_path / 'child.py'
    heartbeat = tmp_path / 'heartbeat'
    child.write_text("import sys,time\np=sys.argv[1]\nwhile True:\n open(p,'a').write('x')\n time.sleep(.05)", encoding='utf-8')
    parent = tmp_path / 'parent.py'
    parent.write_text("import subprocess,sys\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])", encoding='utf-8')
    cancel = threading.Event()
    result = []
    cmd = command(parent) + f' "{child.as_posix()}" "{heartbeat.as_posix()}"'
    thread = threading.Thread(target=lambda: result.append(run_command(cmd, tmp_path, cancel=cancel)))
    thread.start()
    deadline = time.monotonic() + 5
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert heartbeat.exists()
    time.sleep(.3)
    assert thread.is_alive()
    cancel.set()
    thread.join(5)
    assert not thread.is_alive()
    assert result[0].status == 'cancelled'
    size = heartbeat.stat().st_size
    time.sleep(.3)
    assert heartbeat.stat().st_size == size

def test_timeout_after_parent_exits_with_child_pipes(tmp_path):
    child = tmp_path / 'child.py'
    heartbeat = tmp_path / 'heartbeat'
    child.write_text("import sys,time\np=sys.argv[1]\nwhile True:\n open(p,'a').write('x')\n time.sleep(.05)", encoding='utf-8')
    parent = tmp_path / 'parent.py'
    parent.write_text("import subprocess,sys\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])", encoding='utf-8')
    cmd = command(parent) + f' "{child.as_posix()}" "{heartbeat.as_posix()}"'
    result = run_command(cmd, tmp_path, timeout=1)
    assert result.status == 'timeout'
    assert heartbeat.exists()
    size = heartbeat.stat().st_size
    time.sleep(.3)
    assert heartbeat.stat().st_size == size

def test_windows_handshake_waits_for_job_before_user_code(tmp_path, monkeypatch):
    if os.name != 'nt':
        import pytest
        pytest.skip('Windows Job handshake')
    import corecoder.windows_job as jobs
    real_attach = jobs.attach_process
    sentinel = tmp_path / 'started'
    script = tmp_path / 'immediate.py'
    script.write_text("import pathlib,sys\npathlib.Path(sys.argv[1]).write_text('started')", encoding='utf-8')
    def delayed(proc):
        time.sleep(.5)
        assert not sentinel.exists()
        return real_attach(proc)
    monkeypatch.setattr(jobs, 'attach_process', delayed)
    result = run_command(command(script) + f' "{sentinel.as_posix()}"', tmp_path)
    assert result.status == 'exited'
    assert sentinel.read_text() == 'started'

def test_windows_timeout_before_release_never_runs(tmp_path, monkeypatch):
    if os.name != 'nt':
        import pytest
        pytest.skip('Windows Job handshake')
    import corecoder.windows_job as jobs
    real = jobs.attach_process
    sentinel = tmp_path / 'should-not-run'
    script = tmp_path / 'side_effect.py'
    script.write_text(f"from pathlib import Path; Path({str(sentinel)!r}).write_text('bad')", encoding='utf-8')
    def delayed(proc):
        time.sleep(.4)
        return real(proc)
    monkeypatch.setattr(jobs, 'attach_process', delayed)
    result = run_command(command(script), tmp_path, timeout=.1)
    assert result.status == 'timeout'
    time.sleep(.4)
    assert not sentinel.exists()


def test_windows_cancel_before_release_never_runs(tmp_path, monkeypatch):
    if os.name != 'nt':
        import pytest
        pytest.skip('Windows Job handshake')
    import corecoder.windows_job as jobs
    real = jobs.attach_process
    cancel = threading.Event()
    sentinel = tmp_path / 'should-not-run'
    script = tmp_path / 'side_effect.py'
    script.write_text(f"from pathlib import Path; Path({str(sentinel)!r}).write_text('bad')", encoding='utf-8')
    def delayed(proc):
        cancel.set()
        return real(proc)
    monkeypatch.setattr(jobs, 'attach_process', delayed)
    result = run_command(command(script), tmp_path, cancel=cancel)
    assert result.status == 'cancelled'
    time.sleep(.4)
    assert not sentinel.exists()
