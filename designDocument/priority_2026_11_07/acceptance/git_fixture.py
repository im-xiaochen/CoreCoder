"""Independent disposable Git fixtures; no CoreCoder implementation imports."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess


class Repo:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.env = dict(os.environ)
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Offline Acceptance', GIT_AUTHOR_EMAIL='acceptance@invalid.local',
                        GIT_COMMITTER_NAME='Offline Acceptance', GIT_COMMITTER_EMAIL='acceptance@invalid.local',
                        GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat', GIT_OPTIONAL_LOCKS='0',
                        GIT_AUTHOR_DATE='2026-11-07T00:00:00Z', GIT_COMMITTER_DATE='2026-11-07T00:00:00Z')
        for key in list(self.env):
            if key in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'} or key.startswith('GIT_CONFIG_KEY_') or key.startswith('GIT_CONFIG_VALUE_'):
                self.env.pop(key)
        self.env.pop('GIT_CONFIG_COUNT', None)
        self.git('init', '-q', '-b', 'fixture-main')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'user.name', 'Offline Acceptance')
        self.git('config', 'user.email', 'acceptance@invalid.local')

    def git(self, *args: str, check=True) -> bytes:
        return subprocess.run(['git', '-c', 'core.hooksPath=' + str(self.root / '.disabled-hooks'),
                               '-c', 'commit.gpgsign=false', *args], cwd=self.root,
                              env=self.env, capture_output=True, check=check).stdout

    def write(self, name: str, data: str | bytes):
        path = self.root / name
        assert path.resolve().is_relative_to(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)

    def proof(self):
        files = {}
        for path in self.root.rglob('*'):
            if path.is_file() and '.git' not in path.relative_to(self.root).parts:
                files[path.relative_to(self.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'head': self.git('rev-parse', 'HEAD').decode().strip(),
                'index_entries': self.git('ls-files', '--stage', '-z').hex(),
                'index_bytes': hashlib.sha256((self.root / '.git/index').read_bytes()).hexdigest(),
                'status': self.git('status', '--porcelain=v1', '-z', '--untracked-files=all').hex(),
                'files': files}


def mixed_repo(root: Path) -> Repo:
    repo = Repo(root)
    for name in ['partial.txt', 'user-only.txt', 'clean.txt', 'delete.txt', 'old-name.txt']:
        repo.write(name, 'base\n')
    repo.write('binary.bin', b'\x00base\xff')
    repo.git('add', '--', '.')
    repo.git('commit', '-qm', 'fixed fixture baseline')
    repo.write('partial.txt', 'reviewed-index\n')
    repo.git('add', '--', 'partial.txt')
    repo.write('partial.txt', 'reviewed-index\nuser-unstaged-tail\n')
    repo.write('user-only.txt', 'preexisting-user-dirty\n')
    repo.write('user-staged.txt', 'unselected-user-index\n')
    repo.git('add', '--', 'user-staged.txt')
    repo.write('before-untracked.txt', 'preexisting-untracked\n')
    return repo


def during_task(repo: Repo):
    repo.write('clean.txt', 'changed-during-task\n')
    repo.write('user-only.txt', 'preexisting-user-dirty\nexternal-during-task\n')
    repo.write('binary.bin', b'\x00modified\xff\x80')
    (repo.root / 'delete.txt').unlink()
    repo.git('mv', '--', 'old-name.txt', 'renamed.txt')
    for name in ['new-untracked.txt', '--help.txt', '[literal].txt', 'space 中文.txt']:
        repo.write(name, 'task-created\n')
