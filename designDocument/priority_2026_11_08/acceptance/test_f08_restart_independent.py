"""Real kill/restart at in-flight provider boundary; no production code mocks."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time


def test_real_process_kill_restores_unconfirmed_budget_without_new_provider_request(tmp_path):
    entered, release = threading.Event(), threading.Event()
    requests = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            entered.set()
            release.wait(12)
            try:
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                self.wfile.write(b'data: [DONE]\n\n')
            except OSError:
                pass
    provider = ThreadingHTTPServer(('127.0.0.1',0), Provider)
    provider_thread = threading.Thread(target=provider.serve_forever,daemon=True)
    provider_thread.start()
    root = Path(__file__).resolve().parents[3]
    harness = Path(__file__).with_name('f08_service_worker.py')
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    store = tmp_path/'store'
    processes, streams = [], []

    def start(label):
        info = tmp_path/(label+'-info.json')
        stream = (tmp_path/(label+'-worker.log')).open('wb')
        streams.append(stream)
        # Direct base interpreter avoids the Windows venv launcher PID indirection.
        command = [getattr(sys,'_base_executable',sys.executable),str(harness),str(root),str(workspace),str(store),str(info),f'http://127.0.0.1:{provider.server_port}/v1']
        # Base interpreter needs this isolated venv's installed package directory.
        import os
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(sys.prefix)/'Lib/site-packages')
        process = subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,env=env)
        processes.append(process)
        deadline = time.monotonic()+8
        while not info.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        assert info.exists(), (process.poll(),(tmp_path/(label+'-worker.log')).read_text(errors='replace'))
        return process,json.loads(info.read_text(encoding='utf-8'))

    def api(info,path,data=None):
        connection = HTTPConnection('127.0.0.1',info['port'],timeout=5)
        connection.request('POST' if data is not None else 'GET','/api/'+path,
                           body=json.dumps(data) if data is not None else None,
                           headers={'X-CoreCoder-Token':info['token'],'Content-Type':'application/json'})
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status,result

    try:
        process, info = start('before-kill')
        code, run = api(info,'runs',{'id':info['sid'],'prompt':'hold accepted physical request','budget':{'requests':2}})
        assert code == 201 and entered.wait(5)
        code, blocked = api(info,'run?id='+run['id'])
        assert blocked['budget']['groups']['in_flight']['requests'] == 1
        process.kill()
        process.wait(timeout=5)
        release.set()
        restarted, info2 = start('after-restart')
        assert info2['sid'] == info['sid']
        for _ in range(3):
            code, recovered = api(info2,'run?id='+run['id'])
            assert code == 200 and recovered['status'] == 'interrupted'
            assert recovered['budget']['groups']['in_flight']['requests'] == 0
            assert recovered['budget']['groups']['unconfirmed']['requests'] == 1
            assert recovered['budget']['report']['requests_remaining'] == 1
            time.sleep(.1)
        assert len(requests) == 1
        metadata = json.loads((store/'runs'/(run['id']+'.json')).read_text(encoding='utf-8'))
        assert metadata['status'] == 'interrupted'
        assert metadata['budget']['groups']['unconfirmed']['requests'] == 1
    finally:
        release.set()
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        for stream in streams:
            stream.close()
        provider.shutdown()
        provider.server_close()
        provider_thread.join(2)
