from pathlib import Path

from corecoder.agent import Agent
from corecoder.project_rules import discover_rules
from corecoder.state import SessionState


def test_ancestry_bounds_and_limits(tmp_path):
    root = tmp_path / 'root'
    child = root / 'child'
    other = root / 'other'
    child.mkdir(parents=True)
    other.mkdir()
    (root / 'AGENTS.md').write_text('root rule', encoding='utf-8')
    (child / 'AGENTS.md').write_text('child rule', encoding='utf-8')
    (other / 'AGENTS.md').write_text('other rule', encoding='utf-8')
    rules = discover_rules(child, root)
    assert [r['content'] for r in rules] == ['root rule', 'child rule']
    (child / 'AGENTS.md').write_bytes(b'x' * (32 * 1024 + 1))
    assert discover_rules(child, root)[-1]['status'] == 'skipped'
    (child / 'AGENTS.md').write_bytes(b'\xff')
    assert discover_rules(child, root)[-1]['reason'] == 'UnicodeDecodeError'


def test_chat_does_no_filesystem_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('filesystem access in chat')
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(Path, 'exists', forbidden)
    assert discover_rules('nowhere', 'nowhere', 'chat') == []


def test_agent_mode_switch_and_current_directory(tmp_path):
    child = tmp_path / 'child'
    child.mkdir()
    (tmp_path / 'AGENTS.md').write_text('root special', encoding='utf-8')
    (child / 'AGENTS.md').write_text('child special', encoding='utf-8')
    state = SessionState(tmp_path, child)
    agent = Agent(object(), tools=[], state=state)
    text = agent._full_messages()[0]['content']
    assert text.index('root special') < text.index('child special')
    state.cwd = tmp_path
    assert 'child special' not in agent._full_messages()[0]['content']
    agent.chat_mode = True
    assert 'root special' not in agent._full_messages()[0]['content']
    assert agent.rule_sources == []


def test_cli_rule_root_policy_inherited_by_child(tmp_path):
    (tmp_path / '.git').mkdir()
    (tmp_path / 'AGENTS.md').write_text('repo root rule', encoding='utf-8')
    sub = tmp_path / 'sub'
    sub.mkdir()
    state = SessionState(sub, sub)
    state.rules_workspace_root = None
    child = state.fork()
    parent_agent = Agent(object(), tools=[], state=state)
    child_agent = Agent(object(), tools=[], state=child)
    assert 'repo root rule' in parent_agent._full_messages()[0]['content']
    assert 'repo root rule' in child_agent._full_messages()[0]['content']
