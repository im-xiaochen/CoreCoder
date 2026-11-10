"""Independent skills files and physical-call observer, no model/network client."""
from pathlib import Path
import threading
from types import SimpleNamespace


def write_skill(root: Path, name='fixture-review', body='Review the selected changes.\n', extra=None):
    folder=root/'.corecoder/skills'/name
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'SKILL.md').write_text(f'---\nversion: 1\n---\n{body}',encoding='utf-8')
    for path,content in (extra or {}).items():
        target=folder/path
        if not target.resolve().is_relative_to(folder.resolve()):raise ValueError('fixture auxiliary path escapes')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content,encoding='utf-8')
    return folder


class PhysicalCallRecorder:
    """Patch the actual provider create boundary with this callable after API freeze.

    Every physical attempt is recorded, including exceptions; optional gates hold
    in-flight attempts without sleeps so shared-budget races can be observed.
    """
    def __init__(self, outcomes=(), gate=None):
        self.calls=[]
        self.outcomes=list(outcomes)
        self.lock=threading.Lock()
        self.entered=threading.Event()
        self.gate=gate

    def __call__(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            outcome=self.outcomes.pop(0) if self.outcomes else {'prompt_tokens':10,'completion_tokens':5}
        self.entered.set()
        if self.gate is not None and not self.gate.wait(5):raise TimeoutError('independent provider gate not released')
        if isinstance(outcome,BaseException):raise outcome
        usage=None if outcome is None else SimpleNamespace(**outcome)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='offline',tool_calls=None),finish_reason='stop')],usage=usage)
