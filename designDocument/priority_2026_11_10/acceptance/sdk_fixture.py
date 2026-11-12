"""Independent real SDK transport harness: all requests remain in memory."""
import copy
import json

try:
    import httpx
except ImportError:
    import httpx2 as httpx
from openai import OpenAI

from corecoder.llm import LLM


def delta(value, usage=None):
    return {'id':'independent','object':'chat.completion.chunk','created':1,'model':'fixture-reasoner',
            'choices':[] if usage is not None else [{'index':0,'delta':value,'finish_reason':None}],
            **({'usage':usage} if usage is not None else {})}


def stream(*deltas):
    parts=[delta(value) for value in deltas]
    parts.append(delta({}, {'prompt_tokens':12,'completion_tokens':3,'total_tokens':15}))
    raw=b''.join(('data: '+json.dumps(part)+'\n\n').encode() for part in parts)+b'data: [DONE]\n\n'
    return httpx.Response(200,headers={'content-type':'text/event-stream'},content=raw)


def llm_with_transport(monkeypatch, respond, model='fixture-reasoner', endpoint='https://fixture.invalid/v1', **params):
    """Patch constructor only; actual SDK request, streaming and Agent remain real."""
    calls=[]
    def record(request):
        calls.append(copy.deepcopy(json.loads(request.content)))
        return respond(request,len(calls))
    sdk=OpenAI(api_key='offline-only',base_url=endpoint,max_retries=7,
               http_client=httpx.Client(transport=httpx.MockTransport(record)))
    import corecoder.llm as module
    monkeypatch.setattr(module,'OpenAI',lambda **kwargs:sdk)
    monkeypatch.setattr(module.time,'sleep',lambda seconds:None)
    llm=LLM(model,'offline-only',endpoint,max_tokens=32,pricing={(endpoint,model):(0.1,0.2)},**params)
    return llm,calls,sdk
