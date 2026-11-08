"""Independent F06 core contracts; development probe until implementation freezes."""
import json
from pathlib import Path

import pytest

from corecoder.git_workspace import GitWorkspace, GitWorkspaceError
from git_fixture import Repo, during_task, mixed_repo


def rows(state):
    return {entry['path']: entry for entry in state['entries']}


def test_no_repository_is_explicit_and_does_not_initialize(tmp_path):
    with pytest.raises(GitWorkspaceError):
        GitWorkspace(tmp_path)
    assert not (tmp_path / '.git').exists()


def test_empty_repository_review_stage_and_first_commit(tmp_path):
    repo = Repo(tmp_path / 'repo')
    repo.write('new.txt', 'first reviewed version\n')
    api = GitWorkspace(repo.root)
    review = api.status()
    assert review['head'] is None and rows(review)['new.txt']['untracked']
    assert 'first reviewed version' in api.diff('new.txt')['text']
    staged = api.stage(['new.txt'], review['review_token'])
    repo.write('new.txt', 'later unreviewed tail\n')
    committed = api.commit(['new.txt'], api.status()['review_token'], 'initial selected index')
    assert repo.git('show', committed['commit'] + ':new.txt') == b'first reviewed version\n'
    assert (repo.root / 'new.txt').read_text() == 'later unreviewed tail\n'
    assert rows(staged)['new.txt']['index'] == 'A'


def test_read_only_review_separates_partial_index_and_worktree(tmp_path):
    repo = mixed_repo(tmp_path / 'repo'); api = GitWorkspace(repo.root)
    before = repo.proof()
    review = api.status(); entry = rows(review)['partial.txt']
    assert entry['index'] == 'M' and entry['worktree'] == 'M'
    staged = api.diff('partial.txt', staged=True)['text']
    unstaged = api.diff('partial.txt')['text']
    assert '+reviewed-index' in staged and 'user-unstaged-tail' not in staged
    assert '+user-unstaged-tail' in unstaged
    assert repo.proof() == before, 'GET/review must not mutate index, HEAD or worktree'


def test_baseline_detects_same_status_content_drift_and_unknown_origin(tmp_path):
    repo = mixed_repo(tmp_path / 'repo'); api = GitWorkspace(repo.root)
    baseline = json.loads(json.dumps(api.baseline()))
    during_task(repo)
    current = rows(GitWorkspace(repo.root).status(baseline))
    assert current['user-only.txt']['preexisting'] is True
    assert current['user-only.txt']['changed_during_run'] is True
    assert current['partial.txt']['preexisting'] is True
    assert current['partial.txt']['changed_during_run'] is False
    assert current['clean.txt']['preexisting'] is False
    assert current['clean.txt']['changed_during_run'] is True
    assert current['before-untracked.txt']['changed_during_run'] is False
    for entry in api.status()['entries']:
        assert entry['preexisting'] is None and entry['changed_during_run'] is None


def test_unselected_user_staged_is_never_included(tmp_path):
    repo = mixed_repo(tmp_path / 'repo'); api = GitWorkspace(repo.root)
    before = repo.proof()
    try:
        result = api.commit(['partial.txt'], api.status()['review_token'], 'only reviewed partial')
    except GitWorkspaceError:
        assert repo.proof() == before
    else:
        assert repo.git('diff-tree', '--no-commit-id', '--name-only', '-r', result['commit']).splitlines() == [b'partial.txt']
        assert repo.git('show', ':user-staged.txt') == b'unselected-user-index\n'
        assert repo.git('diff', '--cached', '--name-only').splitlines() == [b'user-staged.txt']


def test_commit_uses_reviewed_index_and_preserves_unstaged_tail(tmp_path):
    repo = mixed_repo(tmp_path / 'repo')
    repo.git('reset', '-q', 'HEAD', '--', 'user-staged.txt')  # fixture setup, never product behavior
    api = GitWorkspace(repo.root); before = repo.proof()
    result = api.commit(['partial.txt'], api.status()['review_token'], 'selected index')
    assert repo.git('show', result['commit'] + ':partial.txt') == b'reviewed-index\n'
    assert repo.git('show', result['commit'] + ':user-only.txt') == b'base\n'
    after = repo.proof()
    assert after['files'] == before['files']
    assert after['index_entries'] == before['index_entries']
    assert after['index_bytes'] == before['index_bytes']


@pytest.mark.parametrize('drift', ['worktree', 'index', 'head'])
def test_stale_review_rejected_without_overwriting_external_drift(tmp_path, drift):
    repo = mixed_repo(tmp_path / 'repo'); api = GitWorkspace(repo.root)
    token = api.status()['review_token']
    if drift == 'worktree': repo.write('partial.txt', 'outside editor\n')
    elif drift == 'index':
        repo.write('partial.txt', 'external index\n'); repo.git('add', '--', 'partial.txt')
    else: repo.git('commit', '--allow-empty', '-qm', 'external commit')
    before = repo.proof()
    with pytest.raises(GitWorkspaceError): api.stage(['partial.txt'], token)
    with pytest.raises(GitWorkspaceError): api.commit(['partial.txt'], token, 'must not execute')
    assert repo.proof() == before


def test_binary_delete_rename_and_untracked_are_visible(tmp_path):
    repo = mixed_repo(tmp_path / 'repo'); during_task(repo); api = GitWorkspace(repo.root)
    entries = rows(api.status())
    assert entries['delete.txt']['worktree'] == 'D'
    assert entries['renamed.txt']['old_path'] == 'old-name.txt'
    assert entries['new-untracked.txt']['untracked']
    assert 'Binary' in api.diff('binary.bin')['text']
    assert '-base' in api.diff('delete.txt')['text']
    with pytest.raises(GitWorkspaceError):
        api.commit(['renamed.txt'], api.status()['review_token'], 'one rename side only')


def test_literal_names_stage_only_exact_paths_and_reject_escape(tmp_path):
    repo = mixed_repo(tmp_path / 'repo'); during_task(repo); api = GitWorkspace(repo.root)
    names = ['--help.txt', '[literal].txt', 'space 中文.txt']
    before = repo.git('ls-files', '--stage', '-z')
    api.stage(names, api.status()['review_token'])
    assert repo.git('show', ':--help.txt') == b'task-created\n'
    assert repo.git('show', ':[literal].txt') == b'task-created\n'
    assert repo.git('ls-files', '--stage', '-z').count(b'new-untracked.txt') == before.count(b'new-untracked.txt')
    snapshot = repo.proof()
    for path in ['../escape', str(tmp_path / 'outside'), '.git/config', ':(glob)**']:
        with pytest.raises(GitWorkspaceError): api.stage([path], api.status()['review_token'])
        assert repo.proof() == snapshot
