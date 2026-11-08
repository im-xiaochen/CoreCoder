"""Check only our independent fixture assumptions; not product acceptance."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from git_fixture import during_task, mixed_repo

out = Path(__file__).resolve().parent / 'validation'
out.mkdir(exist_ok=True)
root = Path(tempfile.mkdtemp(prefix='fixture-prep-', dir=out))
one = mixed_repo(root / 'one')
two = mixed_repo(root / 'two')
before = one.proof()
assert before['head'] == two.proof()['head'], 'fixture initial commit must be stable'
during_task(one)
after = one.proof()
assert before['head'] == after['head']
assert one.git('show', ':partial.txt') == b'reviewed-index\n'
assert one.git('show', ':user-staged.txt') == b'unselected-user-index\n'
assert 'user-unstaged-tail' in (one.root / 'partial.txt').read_text()
status = one.git('status', '--porcelain=v1', '-z', '--untracked-files=all')
for literal in [b'partial.txt', b'binary.bin', b'delete.txt', b'renamed.txt', b'--help.txt', b'[literal].txt']:
    assert literal in status, literal
checks = {}
for mode, expected in [('pass', 0), ('fail', 7)]:
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('check_fixture.py')),
                             mode, '--root', str(root / 'checks')], capture_output=True, text=True)
    assert result.returncode == expected
    checks[mode] = {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
report = {'scope': 'independent fixture preparation only, no feature passed', 'root': str(root),
          'baseline': before, 'during_task': after, 'check_commands': checks}
(out / 'fixture-preparation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('Fixture preparation PASS: two deterministic Git baselines; mixed status/index/worktree; exit 0/7.')
print('No F06/F10/F18 implementation executed. Evidence: ' + str(out / 'fixture-preparation.json'))
