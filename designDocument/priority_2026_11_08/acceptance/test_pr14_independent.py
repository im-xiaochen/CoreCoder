"""PR14: artifact-free wheel build and browser child-budget propagation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import httpx2 as httpx
import pytest

from corecoder.permissions import Permission
from test_f08_openai_independent import client_factory, chunk, event
from test_f08_web_cli_independent import web, wait_done


def reply(tool_calls):
    data=chunk()
    data['choices'][0]['delta']={'tool_calls':[
        {'index':i,'id':identifier,'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}
        for i,(identifier,name,arguments) in enumerate(tool_calls)]}
    return httpx.Response(200,headers={'content-type':'text/event-stream'},content=event(data)
        +event(chunk(usage={'prompt_tokens':12,'completion_tokens':3,'total_tokens':15}))+b'data: [DONE]\n\n')


@pytest.mark.parametrize('cap',[1,2])
def test_browser_child_budget_aborts_pending_parent_write(web,cap):
    def respond(request,number):
        if number==1:
            return reply([('child','agent',{'task':'write child proof if budget permits'}),
                          ('pending-parent','write_file',{'file_path':'parent-must-not-write.txt','content':'unwanted write'})])
        return reply([('child-write','write_file',{'file_path':'child-proof.txt','content':'child did start'})])
    service,sid,llm,calls=web(respond)
    session=service.get(sid)
    session.agent.permission=Permission(allow_all=True)
    assert any(type(tool).__name__=='BrowserAgentTool' for tool in session.agent.tools)
    rid=service.start(sid,'delegate and then write',legacy_events=False,budget={'requests':cap})
    wait_done(service,rid)
    assert len(calls)==cap
    assert service.runs.view(rid)['status']=='failed'
    results=[e for e in service.runs.runs[rid]['events'] if e['type']=='run_result']
    assert results[-1]['finish_reason']=='budget_exceeded'
    assert not (service.workspace/'parent-must-not-write.txt').exists()
    assert (service.workspace/'child-proof.txt').exists()==(cap==2)
    outputs={m['tool_call_id']:m['content'] for m in session.agent.messages if m['role']=='tool'}
    assert 'budget' in outputs['child'] and '[Sub-agent completed]' not in outputs['child']
    assert 'before start' not in outputs['child']
    assert 'cancelled before start' in outputs['pending-parent']
    attempts=service.runs.view(rid)['budget']['attempts']
    assert set(attempts)=={entry.attempt_id for entry in llm.usage_ledger}
    assert len(llm.usage_ledger)==cap


def test_clean_source_build_and_installed_templates_without_old_artifacts(tmp_path):
    root=Path(__file__).resolve().parents[3]
    clean=tmp_path/'clean-source'
    clean.mkdir()
    for name in ('pyproject.toml','README.md','LICENSE'):
        shutil.copy2(root/name,clean/name)
    shutil.copytree(root/'corecoder',clean/'corecoder',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    assert not (clean/'.phase3-preparation').exists()
    assert not list(clean.rglob('*.whl'))
    wheel_dir=tmp_path/'built-wheel'
    wheel_dir.mkdir()
    env={key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME')}
    # Actual offline backend build from this minimal source, no repository .git/old wheel.
    build=subprocess.run([sys.executable,'-m','pip','wheel',str(clean),'--no-deps','--no-build-isolation','--no-index','--no-cache-dir','-w',str(wheel_dir)],
                         cwd=tmp_path,env=env,capture_output=True,text=True,timeout=60)
    (tmp_path/'wheel-build.log').write_text(build.stdout+build.stderr,encoding='utf-8')
    assert build.returncode==0,build.stdout+build.stderr
    wheels=list(wheel_dir.glob('corecoder-*.whl'))
    assert len(wheels)==1
    with zipfile.ZipFile(wheels[0]) as archive:
        for name in ('review','test','pr-summary'):
            assert f'corecoder/builtin_skills/{name}/SKILL.md' in archive.namelist()
    installed=tmp_path/'installed'
    install=subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-compile','--target',str(installed),str(wheels[0])],
                           cwd=tmp_path,env=env,capture_output=True,text=True,timeout=30)
    (tmp_path/'wheel-install.log').write_text(install.stdout+install.stderr,encoding='utf-8')
    assert install.returncode==0,install.stdout+install.stderr
    isolated=tmp_path/'outside-source'
    isolated.mkdir()
    code='''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import corecoder.skills as skills
assert Path(skills.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
snapshot=skills.list_skills(Path.cwd())
loaded=[skills.resolve_skill(snapshot,name,'builtin') for name in ('review','test','pr-summary')]
assert all(entry.text.strip() for entry in loaded)
print(json.dumps({'import':skills.__file__,'sources':[entry.source for entry in loaded]}))
'''
    probe=subprocess.run([sys.executable,'-I','-c',code,str(installed)],cwd=isolated,env=env,capture_output=True,text=True,timeout=20)
    (tmp_path/'installed-probe.log').write_text(probe.stdout+probe.stderr,encoding='utf-8')
    assert probe.returncode==0,probe.stdout+probe.stderr
    assert json.loads(probe.stdout)['sources']==['builtin']*3
