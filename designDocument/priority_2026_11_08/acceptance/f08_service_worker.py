"""Isolated acceptance server; only explicit localhost provider and no extensions."""
import json
from pathlib import Path
import sys
from http.server import ThreadingHTTPServer

repo, workspace, store, info, provider = sys.argv[1:]
sys.path.insert(0, repo)
from corecoder.config import Config
from corecoder.llm import LLM
import corecoder.llm as llm_module
from openai import OpenAI
import httpx2
from web_backend.service import Service
from web_backend.server import make_handler

Config.from_env = classmethod(lambda cls: Config(api_key='offline-key',model='independent',max_tokens=32))
llm_module.OpenAI = lambda **kwargs: OpenAI(**kwargs,http_client=httpx2.Client(trust_env=False,timeout=8))
service = Service(Path(workspace), Path(store), extensions=False,
                  llm_factory=lambda: LLM('independent','offline-key',provider,max_tokens=32,pricing={}))
sid = next(iter(service.sessions)) if service.sessions else service.create()['id']
server = ThreadingHTTPServer(('127.0.0.1',0), make_handler(service,service.workspace))
Path(info).write_text(json.dumps({'port':server.server_port,'token':service.token,'sid':sid}),encoding='utf-8')
server.serve_forever()
