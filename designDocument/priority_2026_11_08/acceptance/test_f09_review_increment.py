"""Only the two F09 static-review corrections; genuine worker/process probes."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from corecoder.state import SessionState
from corecoder.tools.grep import GrepTool
import corecoder.tools.grep as grep_module


def test_real_regex_worker_pid_is_base_interpreter_and_dead_after_timeout(tmp_path,monkeypatch):
    root=tmp_path/'workspace';root.mkdir()
    (root/'expensive.txt').write_text('a'*4000+'!\n',encoding='utf-8')
    marker=tmp_path/'actual-worker.json';bootstrap=tmp_path/'worker_probe.py'
    worker=Path(grep_module.__file__).resolve().parents[1]/'search_worker.py'
    bootstrap.write_text('import os,sys,json,runpy\nfrom pathlib import Path\nPath('+repr(str(marker))+').write_text(json.dumps({"pid":os.getpid(),"executable":sys.executable}),encoding="utf-8")\nrunpy.run_path('+repr(str(worker))+',run_name="__main__")\n',encoding='utf-8')
    real=grep_module.subprocess.Popen;started=[]
    def observe(command,*args,**kwargs):
        if str(command[-1]).endswith('search_worker.py'):
            original=list(command);command=list(command);command[-1]=str(bootstrap)
            proc=real(command,*args,**kwargs);started.append((original,proc));return proc
        return real(command,*args,**kwargs)
    monkeypatch.setattr(grep_module.subprocess,'Popen',observe)
    tool=GrepTool(SessionState(workspace_root=root,cwd=root))
    try:
        start=time.monotonic();result=tool.execute('(a+)+$',path='expensive.txt');elapsed=time.monotonic()-start
        assert 'timed out' in result and 'incomplete' in result and elapsed<9
        assert len(started)==1 and marker.is_file()
        command,proc=started[0];actual=json.loads(marker.read_text(encoding='utf-8'))
        assert actual['pid']==proc.pid and proc.poll() is not None
        if os.name=='nt':assert Path(command[0]).resolve()==Path(sys._base_executable).resolve()
    finally:
        for _,proc in started:
            if proc.poll() is None:proc.kill();proc.wait(5)


def test_no_rg_git_installed_plain_nonrepo_searches_but_ignore_stays_closed(monkeypatch):
    real=shutil.which
    assert real('git')
    monkeypatch.setattr(shutil,'which',lambda name,*a,**k:None if name=='rg' else real(name,*a,**k))
    # Actual system temp deliberately lies outside the product Git checkout.
    with tempfile.TemporaryDirectory(prefix='corecoder-f09-independent-') as directory:
        root=Path(directory).resolve();(root/'plain.py').write_text('PLAIN_NONREPO_CANARY\n',encoding='utf-8')
        tool=GrepTool(SessionState(workspace_root=root,cwd=root))
        result=tool.execute('PLAIN_NONREPO_CANARY')
        assert 'PLAIN_NONREPO_CANARY' in result and '[backend: walk' in result
        (root/'.gitignore').write_text('plain.py\n',encoding='utf-8')
        result=tool.execute('PLAIN_NONREPO_CANARY')
        assert 'Error:' in result and 'ignore' in result.lower() and 'PLAIN_NONREPO_CANARY' not in result
