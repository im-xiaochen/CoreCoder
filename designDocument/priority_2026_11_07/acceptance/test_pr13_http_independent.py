"""Actual loopback SSE and GET must expose diagnostics before exit."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

from test_f06_http_independent import project
from test_pr13_checks_incremental_independent import wait_for
from web_backend.server import make_handler


def test_http_live_persisted_output_cursor_and_closed_run_guard(project, monkeypatch):
    repo, factory = project
    service = factory()
    sid = service.create()['id']
    signals = repo.root / 'signals'
    writer = Path(__file__).with_name('pr13_check_writer.py')
    exe = getattr(sys, '_base_executable', sys.executable).replace('\\', '/')
    config = repo.root / '.corecoder/checks.json'
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({'version':1,'checks':[{'id':'live','name':'live','command':f'"{exe}" -u "{writer.as_posix()}" "{signals.as_posix()}"','timeout':20}]}),encoding='utf-8')
    callbacks=[]
    real=service.project_checks.run
    def capture(*args, **kwargs):
        callbacks.append(kwargs['on_progress'])
        return real(*args, **kwargs)
    monkeypatch.setattr(service.project_checks,'run',capture)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(service,repo.root))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path, body=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=6)
        conn.request('POST' if body is not None else 'GET','/api/'+path,
                     body=json.dumps(body) if body is not None else None,
                     headers={'X-CoreCoder-Token':service.token,'Content-Type':'application/json'})
        response=conn.getresponse();data=json.loads(response.read());conn.close()
        assert response.status<400,data
        return data
    def stream(rid,after,until):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=6)
        conn.request('GET',f'/api/run/events?id={rid}&after={after}',headers={'X-CoreCoder-Token':service.token})
        response=conn.getresponse();assert response.status==200
        events=[]
        try:
            while line:=response.readline():
                if line.startswith(b'data: '):
                    event=json.loads(line[6:]);events.append(event)
                    if until(event):return events
            raise AssertionError('SSE ended without required event')
        finally:response.close();conn.close()
    try:
        token=request('checks/config?id='+sid)['token']
        payload={'id':sid,'check_ids':['live'],'token':token,'confirm':True}
        run=request('checks/start',payload);rid=run['id']
        events=stream(rid,0,lambda e:e['type']=='check_output')
        progress=events[-1]
        assert progress['run_id']==rid and progress['session_id']==sid and progress['id']
        record=wait_for(lambda:request('checks/result?result_id='+progress['id']),lambda x:
            'PR13 stdout before release' in x['stdout'] and 'PR13 stderr before release' in x['stderr'],seconds=3)
        assert record['status']=='running' and record['ended_at'] is None
        assert request('run?id='+rid)['status']=='running'
        assert not (signals/'release').exists()
        (signals/'release').touch()
        tail=stream(rid,progress['seq'],lambda e:e['type']=='done')
        assert all(e['seq']>progress['seq'] and e['run_id']==rid for e in tail)
        assert len({e['seq'] for e in tail})==len(tail)
        assert any(e['type']=='check_result' and e['exit_code']==7 for e in tail)
        wait_for(lambda:service.execution.locked(),lambda locked:not locked)
        final=request('checks/result?result_id='+record['id'])
        assert final['status']=='failed' and 'PR13 final stdout' in final['stdout']
        next_run=request('checks/start',payload)
        before=service.runs.view(next_run['id'])['last_seq']
        callbacks[0]({'id':record['id'],'run_id':rid,'session_id':sid,'text':'STALE PRIOR RUN'})
        assert all(e.get('text')!='STALE PRIOR RUN' for e in service.runs.runs[next_run['id']]['events'])
        stream(next_run['id'],0,lambda e:e['type']=='done')
        assert before>=0
    finally:
        signals.mkdir(exist_ok=True);(signals/'release').touch()
        server.shutdown();server.server_close();thread.join(2)
