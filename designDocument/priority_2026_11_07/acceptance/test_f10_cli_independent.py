"""Drive actual CLI REPL check authorization without a model request."""
import json
from corecoder import Agent, Config, SessionState
from corecoder.llm import LLM
from corecoder.permissions import Permission
from corecoder import cli
from test_f10_core_independent import checks


def drive(monkeypatch, root, inputs):
    state = SessionState(root, root)
    agent = Agent(llm=LLM(model='offline', api_key='offline-placeholder'), state=state, tools=[], permission=Permission(allow_all=True))
    monkeypatch.setattr(cli.Path, 'home', lambda: root / 'isolated-home')
    monkeypatch.setattr(cli, 'FileHistory', lambda *a, **kw: None)
    scripted = iter(inputs)
    monkeypatch.setattr(cli, 'pt_prompt', lambda *a, **kw: next(scripted))
    cli._repl(agent, Config(api_key='offline-placeholder', model='offline'))


def test_cli_checks_default_no_does_not_run_despite_yes_permission(checks, monkeypatch, capsys):
    _, root, _, _ = checks
    monkeypatch.setenv('CORECODER_DATA_DIR', str(root / 'isolated-data'))
    drive(monkeypatch, root, ['/check pass', '', 'quit'])
    assert not (root / 'pass').exists()
    assert 'Confirm' not in capsys.readouterr().out or not list((root / 'isolated-home').rglob('*.json'))


def test_cli_explicit_yes_uses_real_failure_exit_and_persists(checks, monkeypatch, capsys):
    _, root, _, _ = checks
    monkeypatch.setenv('CORECODER_DATA_DIR', str(root / 'isolated-data'))
    drive(monkeypatch, root, ['/check fail', 'y', 'quit'])
    records = list((root / 'isolated-home/.corecoder/checks').rglob('*.json'))
    assert len(records) == 1
    result = json.loads(records[0].read_text(encoding='utf-8'))
    assert result['status'] == 'failed' and result['exit_code'] == 7
    assert 'exit 7' in capsys.readouterr().out
