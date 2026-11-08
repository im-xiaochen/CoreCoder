"""Review increment only: global identity without enabling dangerous Git config."""
import os
from pathlib import Path
import subprocess

import pytest

from corecoder.git_workspace import GitWorkspace, GitWorkspaceError
from git_fixture import Repo


@pytest.fixture
def identities(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.upper().startswith('GIT_'):
            monkeypatch.delenv(name)
    home = tmp_path / 'fake-home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    repo = Repo(tmp_path / 'repo')
    repo.write('item.txt', 'baseline\n')
    repo.git('add', '--', 'item.txt')
    repo.git('commit', '-qm', 'initial')
    repo.git('config', '--unset-all', 'user.name')
    repo.git('config', '--unset-all', 'user.email')
    global_config = home / '.gitconfig'
    global_config.write_text('[user]\n name = Global Only Identity\n email = global-only@example.invalid\n', encoding='utf-8')
    repo.write('item.txt', 'reviewed index\n')
    repo.git('add', '--', 'item.txt')
    repo.write('item.txt', 'reviewed index\nuser worktree tail\n')
    return repo, global_config, tmp_path


def commit_and_identity(repo):
    git = GitWorkspace(repo.root)
    result = git.commit(['item.txt'], git.status()['review_token'], 'independent review identity')
    assert repo.git('show', result['commit'] + ':item.txt') == b'reviewed index\n'
    assert (repo.root / 'item.txt').read_text(encoding='utf-8').endswith('user worktree tail\n')
    return repo.git('show', '-s', '--format=%an|%ae|%cn|%ce', result['commit']).decode('utf-8').strip()


def test_global_only_identity_works_without_local_configuration(identities):
    repo, _, _ = identities
    assert commit_and_identity(repo) == 'Global Only Identity|global-only@example.invalid|Global Only Identity|global-only@example.invalid'
    assert repo.git('config', '--local', '--get', 'user.name', check=False) == b''


def test_explicit_per_field_environment_overrides_local_and_global(identities, monkeypatch):
    repo, _, _ = identities
    repo.git('config', 'user.name', 'Local User')
    repo.git('config', 'user.email', 'local@example.invalid')
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'Explicit Author')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 'committer-env@example.invalid')
    assert commit_and_identity(repo) == 'Explicit Author|local@example.invalid|Local User|committer-env@example.invalid'


def test_global_identity_does_not_enable_global_hooks_filters_signers(identities):
    repo, global_config, root = identities
    sentinel = root / 'DANGEROUS_GIT_CONFIG_EXECUTED'
    shell = root / 'danger.sh'
    shell.write_text('#!/bin/sh\nprintf executed >> "' + sentinel.as_posix() + '"\nexit 0\n', encoding='utf-8')
    shell.chmod(0o755)
    hooks = root / 'hooks'
    hooks.mkdir()
    (hooks / 'pre-commit').write_bytes(shell.read_bytes())
    (hooks / 'pre-commit').chmod(0o755)
    text = f'''[core]
 hooksPath = {hooks.as_posix()}
 fsmonitor = {shell.as_posix()}
[filter "danger"]
 clean = {shell.as_posix()}
 process = {shell.as_posix()}
[gpg]
 program = {shell.as_posix()}
[commit]
 gpgsign = true
[diff]
 external = {shell.as_posix()}
'''
    with global_config.open('a', encoding='utf-8') as file:
        file.write(text)
    repo.write('.gitattributes', '*.txt filter=danger\n')
    git = GitWorkspace(repo.root)
    assert 'user worktree tail' in git.diff('item.txt')['text']
    assert commit_and_identity(repo).startswith('Global Only Identity|')
    assert not sentinel.exists(), 'identity lookup must not enable unrelated global Git commands'


def test_local_filter_rejection_survives_identity_fallback(identities):
    repo, _, root = identities
    sentinel = root / 'LOCAL_FILTER_EXECUTED'
    shell = root / 'local-danger.sh'
    shell.write_text('#!/bin/sh\nprintf executed >> "' + sentinel.as_posix() + '"\n', encoding='utf-8')
    shell.chmod(0o755)
    repo.git('config', 'filter.danger.clean', shell.as_posix())
    with pytest.raises(GitWorkspaceError, match='filter|Filter'):
        GitWorkspace(repo.root).status()
    assert not sentinel.exists()
