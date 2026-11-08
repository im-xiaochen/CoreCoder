"""Independent F06 HTTP/run-baseline contracts; isolated repos and providers."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
from corecoder import Config
from corecoder.llm import LLM
from corecoder.results import RunResult
import web_backend.service as module
from web_backend.server import make_handler
from git_fixture import mixed_repo, during_task


@pytest.fixture
def project(tmp_path, monkeypatch):
    repo = mixed_repo(tmp_path / 'repo')
    monkeypatch.setenv('CORECODER_DATA_DIR', str(tmp_path / 'isolated-data'))
    monkeypatch.setattr(Config, 'from_env', lambda: Config(api_key='offline-placeholder', model='offline'))
    def factory():
        return module.Service(repo.root, tmp_path / 'store', llm_factory=lambda: LLM(model='offline', api_key='offline-placeholder'), extensions=False)
    return repo, factory


@contextmanager
def http(service):
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(service, Path(__file__).resolve().parents[3] / 'frontend/dist'))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    def request(path, body=None):
        req = Request(f'http://127.0.0.1:{server.server_port}/api/{path}',
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'X-CoreCoder-Token': service.token, 'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=8) as response: return response.status, json.load(response)
        except HTTPError as error: return error.code, json.load(error)
    try: yield request
    finally: server.shutdown(); server.server_close(); worker.join(2)


def status(request, sid):
    code, result = request('git/status?' + urlencode({'id': sid}))
    assert code == 200, result
    return result


def await_done(service, events):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        event = events.get(timeout=8)
        if event['type'] == 'done': break
    else: raise AssertionError('run did not terminate')
    while service.execution.locked() and time.monotonic() < deadline: time.sleep(.01)
    assert not service.execution.locked()


def test_http_confirm_token_and_selected_commit(project):
    repo, factory = project; svc = factory(); sid = svc.create()['id']
    repo.write('new-choice.txt', 'reviewed new data\n')
    with http(svc) as request:
        review = status(request, sid); before = repo.proof()
        for confirm in [None, False, 'true', 1]:
            assert request('git/stage', {'id': sid, 'paths': ['new-choice.txt'], 'review_token': review['review_token'], 'confirm': confirm})[0] == 403
            assert repo.proof() == before
        assert request('git/stage', {'id': sid, 'paths': ['new-choice.txt'], 'review_token': 'stale', 'confirm': True})[0] == 409
        assert request('git/stage', {'id': sid, 'paths': ['new-choice.txt'], 'review_token': review['review_token'], 'confirm': True})[0] == 200
        assert request('git/commit', {'id': sid, 'paths': ['new-choice.txt'], 'review_token': status(request, sid)['review_token'], 'confirm': True, 'message': 'no user staged files'})[0] == 409
        # Only the disposable fixture removes its unrelated staged entries.
        repo.git('reset', '-q', 'HEAD', '--', 'partial.txt', 'user-staged.txt')
        code, result = request('git/commit', {'id': sid, 'paths': ['new-choice.txt'], 'review_token': status(request, sid)['review_token'], 'confirm': True, 'message': 'selected HTTP delivery'})
        assert code == 200, result
        assert repo.git('show', result['commit'] + ':new-choice.txt') == b'reviewed new data\n'
        assert repo.git('diff-tree', '--no-commit-id', '--name-only', '-r', result['commit']).splitlines() == [b'new-choice.txt']


@pytest.mark.parametrize('mode', ['chat', 'plan'])
def test_http_noncode_mutations_denied_and_busy_protected(project, mode):
    repo, factory = project; svc = factory(); sid = svc.create()['id']; session = svc.get(sid)
    with http(svc) as request:
        token = status(request, sid)['review_token']; before = repo.proof()
        session.set_mode(mode)
        assert request('git/stage', {'id': sid, 'paths': ['partial.txt'], 'review_token': token, 'confirm': True})[0] == 403
        session.set_mode('code'); svc.execution.acquire()
        try: assert request('git/stage', {'id': sid, 'paths': ['partial.txt'], 'review_token': token, 'confirm': True})[0] == 409
        finally: svc.execution.release()
        assert repo.proof() == before


def test_chat_run_never_opens_git_or_captures_baseline(project, monkeypatch):
    _, factory = project; svc = factory(); sid = svc.create()['id']; session = svc.get(sid)
    session.set_mode('chat')
    def forbidden(*args, **kwargs): raise AssertionError('chat must not inspect Git workspace')
    monkeypatch.setattr(module, 'GitWorkspace', forbidden)
    monkeypatch.setattr(session.agent, 'chat_result', lambda *a, **kw: RunResult('completed', 'offline', 0, [], {}, 'completed'))
    events = svc.start(sid, 'offline chat'); await_done(svc, events)
    assert session.git_baseline is None


def test_run_baseline_precedes_effects_persists_and_old_binding_is_unknown(project, monkeypatch):
    repo, factory = project; svc = factory(); sid = svc.create()['id']; session = svc.get(sid)
    def run(*args, **kwargs):
        assert session.git_baseline and session.git_baseline_run_id == session.run_id
        assert session.git_baseline['files']['clean.txt']['worktree']
        during_task(repo)
        return RunResult('completed', 'offline', 0, [], {}, 'completed')
    monkeypatch.setattr(session.agent, 'chat_result', run)
    events = svc.start(sid, 'mutate disposable repo'); await_done(svc, events)
    restored = factory(); target = restored.get(sid)
    assert target.git_baseline == session.git_baseline
    assert target.git_baseline_run_id == target.run_id == session.run_id
    with http(restored) as request:
        entries = {e['path']: e for e in status(request, sid)['entries']}
        assert entries['clean.txt']['preexisting'] is False and entries['clean.txt']['changed_during_run'] is True
        assert entries['user-only.txt']['preexisting'] is True and entries['user-only.txt']['changed_during_run'] is True
        target.run_id = 'different-run-without-this-baseline'
        assert all(e['preexisting'] is None and e['changed_during_run'] is None for e in status(request, sid)['entries'])


@pytest.mark.parametrize('race', ['mode', 'identity', 'archive'])
def test_http_rechecks_session_after_execution_lock_acquired(project, race):
    repo, factory = project; svc = factory(); sid = svc.create()['id']; session = svc.get(sid)
    with http(svc) as request:
        token = status(request, sid)['review_token']; before = repo.proof(); actual = svc.execution
        class RacingLock:
            released = False
            def acquire(self, blocking=False):
                acquired = actual.acquire(blocking=blocking)
                if acquired:
                    if race == 'mode': session.set_mode('plan')
                    elif race == 'identity': svc.sessions[sid] = object()
                    else: session.archived = True
                return acquired
            def release(self): self.released = True; actual.release()
        racing = RacingLock(); svc.execution = racing
        try:
            code, _ = request('git/stage', {'id': sid, 'paths': ['partial.txt'], 'review_token': token, 'confirm': True})
            assert code == (403 if race == 'mode' else 409)
            assert racing.released and not actual.locked()
            assert repo.proof() == before
        finally: svc.sessions[sid] = session; svc.execution = actual
