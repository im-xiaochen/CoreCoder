"""Deterministic independent retrieval fixture; never touches the product repo."""
from pathlib import Path
import os
import subprocess


def build_repository(root: Path, count=24):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1',
               GIT_AUTHOR_NAME='Offline Fixture',GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='Offline Fixture',GIT_COMMITTER_EMAIL='fixture@example.invalid',
               GIT_AUTHOR_DATE='2000-01-01T00:00:00Z',GIT_COMMITTER_DATE='2000-01-01T00:00:00Z')
    def git(*args):
        return subprocess.run(['git','-c','core.hooksPath='+os.devnull,'-c','commit.gpgsign=false',*args],cwd=root,env=env,check=True,capture_output=True,timeout=15).stdout
    git('init','-q','-b','fixture')
    def put(name, data):
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data.encode('utf-8') if isinstance(data,str) else data)
    put('.gitignore','ignored/\n*.tmp\n!keep.tmp\n')
    put('src/.gitignore','private.py\n*.secret\n!public.secret\n')
    put('src/calculation.py','class Ledger:\n    def total(self, values):\n        return sum(values)\n\ndef calculate(values):\n    return Ledger().total(values)\n')
    put('src/consumer.py','from calculation import calculate\n\ndef invoice(values):\n    return calculate(values)\n')
    put('web/consumer.ts','import { invoice } from "./invoice";\n')
    for i in range(count):put(f'pages/entry_{i:04d}.py',f'F09_ENTRY_{i:04d} = {i}\n')
    put('ignored/leak.py','F09_FORBIDDEN_IGNORED\n')
    put('discard.tmp','F09_FORBIDDEN_TMP\n')
    put('src/private.py','F09_FORBIDDEN_NESTED\n')
    put('src/private.secret','F09_FORBIDDEN_SECRET\n')
    put('keep.tmp','F09_ALLOWED_ROOT_EXCEPTION\n')
    put('src/public.secret','F09_ALLOWED_NESTED_EXCEPTION\n')
    put('node_modules/noise.py','F09_FORBIDDEN_DEFAULT\n')
    put('.env','F09_FORBIDDEN_CREDENTIAL\n')
    put('binary.bin',b'\0F09_FORBIDDEN_BINARY\xff')
    put('space 中文 [literal].py','def special_name():\n    return 7\n')
    git('add','--','src/calculation.py','src/consumer.py','pages','web','.gitignore','src/.gitignore')
    git('commit','-qm','fixed retrieval fixture')
    for path in root.rglob('*'):
        if path.is_file() and '.git' not in path.relative_to(root).parts:os.utime(path,(946684800,946684800))
    return {'root':root,'git':git,'put':put,'visible_entries':{f'F09_ENTRY_{i:04d}' for i in range(count)},
            'definitions':{'Ledger':('src/calculation.py',1),'calculate':('src/calculation.py',5),'invoice':('src/consumer.py',3)},
            'initial_tree':git('rev-parse','HEAD^{tree}').decode().strip()}
