"""Only F06 filter/transport limit deltas; isolated real commands."""
import os
from pathlib import Path
import sys
import time

import pytest
import corecoder.git_workspace as module
from corecoder.git_workspace import GitWorkspace, GitWorkspaceError
from git_fixture import Repo


@pytest.mark.parametrize('kind', ['clean', 'process'])
def test_readonly_filter_preflight_never_executes_configured_program(tmp_path, kind):
    repo = Repo(tmp_path / 'repo')
    repo.write('.gitattributes', '*.txt filter=sentinel\n')
    repo.write('a.txt', 'base\n'); repo.git('add', '--', '.'); repo.git('commit', '-qm', 'fixture')
    repo.write('a.txt', 'changed\n'); api = GitWorkspace(repo.root)
    token = api.status()['review_token']
    index = (repo.root / '.git/index').read_bytes()
    marker = tmp_path / 'must-not-execute'
    script = tmp_path / 'filter.py'
    script.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("EXECUTED")\n', encoding='utf-8')
    exe = getattr(sys, '_base_executable', sys.executable).replace('\\', '/')
    repo.git('config', 'filter.sentinel.' + kind, f'"{exe}" "{script.as_posix()}"')
    for action in [api.status, api.baseline, lambda: api.diff('a.txt'), lambda: api.stage(['a.txt'], token)]:
        with pytest.raises(GitWorkspaceError, match='filter|Filter'): action()
    assert not marker.exists()
    assert (repo.root / '.git/index').read_bytes() == index


def test_real_large_git_diff_is_explicitly_rejected(tmp_path):
    repo = Repo(tmp_path / 'repo')
    repo.write('large.txt', 'old\n' * 1_000_000); repo.git('add', '--', '.'); repo.git('commit', '-qm', 'large fixture')
    repo.write('large.txt', 'new\n' * 1_000_000)
    api = GitWorkspace(repo.root)
    started = time.monotonic()
    with pytest.raises(GitWorkspaceError, match='limit'): api.diff('large.txt')
    assert time.monotonic() - started < 12


@pytest.mark.parametrize('mode', ['stdout-cap', 'stderr-cap', 'timeout'])
def test_live_transport_limits_terminate_before_natural_exit(tmp_path, monkeypatch, mode):
    repo = Repo(tmp_path / 'repo'); api = GitWorkspace(repo.root)
    script = tmp_path / 'transport.py'
    payload = 'import os,time\n'
    if mode == 'stdout-cap': payload += 'for _ in range(150): os.write(1,b"x"*65536)\n'
    elif mode == 'stderr-cap': payload += 'os.write(2,b"e"*65536)\n'
    else: payload += 'os.write(1,b"started")\n'
    payload += 'time.sleep(60)\n'
    script.write_text(payload, encoding='utf-8')
    real_popen = module.subprocess.Popen; launched = []
    def launch(command, **kwargs):
        if command[0] == 'git' and command[-1] == 'diff':
            child = real_popen([getattr(sys, '_base_executable', sys.executable), '-u', str(script)], **kwargs)
            launched.append(child)
            if mode == 'timeout':
                real_wait = child.wait
                child.wait = lambda timeout=None: real_wait(timeout=.2 if timeout == 30 else timeout)
            return child
        return real_popen(command, **kwargs)
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    started = time.monotonic()
    try:
        with pytest.raises(GitWorkspaceError, match='timed out' if mode == 'timeout' else 'limit'):
            api._git('diff')
        assert time.monotonic() - started < 3, 'limit must stop a live writer, not wait for its 60-second natural exit'
        assert launched and launched[0].poll() is not None
        assert launched[0].stdout.closed and launched[0].stderr.closed
    finally:
        for child in launched:
            if child.poll() is None: child.kill(); child.wait(timeout=3)
