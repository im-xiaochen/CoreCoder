"""Independent F18 CLI driver with hostile *fake* caller config, no user config."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('label')
parser.add_argument('--optimize', action='store_true')
parser.add_argument('--task')
args = parser.parse_args()
here = Path(__file__).resolve().parent
repo = here.parents[2]
base = here / 'validation' / args.label
base.mkdir(parents=True, exist_ok=False)
caller = base / 'caller'; caller.mkdir()
profile = base / 'fake-home'; profile.mkdir()
config = profile / '.corecoder'; config.mkdir()
(caller / '.env').write_text('CORECODER_MAX_TOKENS=INVALID_CALLER_VALUE\nOPENAI_API_KEY=offline-fake-key\n', encoding='utf-8')
(profile / '.gitconfig').write_text('[init]\n defaultBranch = caller-must-not-leak\n[user]\n name = Fake Caller\n email = fake@invalid.local\n', encoding='utf-8')
marker = base / 'user-extension-was-executed'
sentinel = base / 'forbidden_extension.py'
sentinel.write_text(f'from pathlib import Path;Path({str(marker)!r}).write_text("UNAUTHORIZED")', encoding='utf-8')
# Fake files detect accidental loading. They contain no real user credentials/extensions.
(config / 'mcp.json').write_text(json.dumps({'mcpServers': {'forbidden': {'command': sys.executable, 'args': [str(sentinel)]}}}), encoding='utf-8')
(config / 'hooks.json').write_text(json.dumps({'pre': [{'command': f'"{sys.executable}" "{sentinel}"'}], 'post': []}), encoding='utf-8')
env = {key: os.environ[key] for key in ['PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP'] if key in os.environ}
env.update(HOME=str(profile), USERPROFILE=str(profile), PYTHONPATH=str(repo),
           OPENAI_API_KEY='offline-fake-key', CORECODER_MAX_TOKENS='INVALID_INHERITED_VALUE',
           CORECODER_DATA_DIR=str(base / 'caller-data'), PYTHONIOENCODING='utf-8')
output = base / 'output'
cmd = [sys.executable, *(['-O'] if args.optimize else []), '-m', 'corecoder.evaluation', '--output', str(output)]
if args.task: cmd += ['--task', args.task]
source = repo / 'corecoder/evaluation.py'
before = hashlib.sha256(source.read_bytes()).hexdigest()
with (base / 'cli.log').open('w', encoding='utf-8') as log:
    result = subprocess.run(cmd, cwd=caller, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
after = hashlib.sha256(source.read_bytes()).hexdigest()
evidence = {'command': cmd, 'cwd': str(caller), 'exit_code': result.returncode,
            'evaluation_sha256_before': before, 'evaluation_sha256_after': after,
            'fake_extension_executed': marker.exists(), 'report_path': str(output / 'report.json')}
(base / 'driver.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
print(json.dumps(evidence))
raise SystemExit(result.returncode)
