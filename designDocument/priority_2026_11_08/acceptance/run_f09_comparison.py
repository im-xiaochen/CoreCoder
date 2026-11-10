"""One fixed-query old/new observation, not a universal speed benchmark."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

parser=argparse.ArgumentParser();parser.add_argument('--worker',action='store_true');parser.add_argument('--root');parser.add_argument('--label',default='f09-comparison')
args=parser.parse_args()
here=Path(__file__).resolve().parent;repo=here.parents[2]
if args.worker:
    from corecoder.state import SessionState
    from corecoder.tools.grep import GrepTool
    import corecoder.tools.grep as module
    state=SessionState(workspace_root=Path(args.root),cwd=Path(args.root))
    rows=[]
    for pattern in ('calculate','Ledger|invoice'):
        start=time.perf_counter();text=GrepTool(state).execute(pattern,path='src',include='*.py');elapsed=time.perf_counter()-start
        if 'calculation.py' not in text:raise RuntimeError('definition not located')
        if 'consumer.py' not in text:raise RuntimeError('cross-file consumer not located')
        rows.append({'pattern':pattern,'path':'src','elapsed_seconds':elapsed,'characters':len(text),'approx_tokens_chars_div4':(len(text)+3)//4,'result':text})
    print(json.dumps({'module':module.__file__,'queries':rows}));raise SystemExit(0)
from retrieval_fixture import build_repository
base=here/'validation'/args.label;base.mkdir(parents=True,exist_ok=False)
fixture=build_repository(base/'repository',count=260)
home=base/'fake-home';home.mkdir();(home/'.env').write_text('',encoding='utf-8')
env={key:os.environ[key] for key in ('PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP') if key in os.environ}
env.update(HOME=str(home),USERPROFILE=str(home),XDG_CONFIG_HOME=str(home),CORECODER_DATA_DIR=str(home/'data'),PYTHONIOENCODING='utf-8')
observations={}
for name,source in [('old',repo/'.phase3-preparation/baseline'),('new',repo)]:
    env['PYTHONPATH']=str(source)
    result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker','--root',str(fixture['root'])],cwd=home,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
    (base/(name+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError(name+' comparison worker failed')
    row=json.loads(result.stdout)
    if not Path(row['module']).resolve().is_relative_to(source.resolve()):raise RuntimeError('wrong module import')
    observations[name]=row
report={'initial_tree':fixture['initial_tree'],'file_count_pages':260,'observations':observations,'limitations':'One Windows local fixed-query run per version; tokens use chars/4 proxy, not model billing; no general speed claim.'}
(base/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
