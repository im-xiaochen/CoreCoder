"""Test doubles and assertions; this module implements no remediation logic."""

from types import SimpleNamespace

from corecoder import cli
from corecoder.config import Config
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLMResponse


def required_attr(target, name):
    assert hasattr(target, name), f"Proposed contract is not implemented: {target!r}.{name}"
    return getattr(target, name)


def assert_complete_tool_chain(messages):
    """Check ordering, unique ids, exactly one reply, and no orphan replies."""
    pending = set()
    for message in messages:
        if message.get("role") == "tool":
            call_id = message["tool_call_id"]
            assert call_id in pending, f"Orphan or duplicate tool reply: {call_id}"
            pending.remove(call_id)
        else:
            assert not pending, f"New message before tool replies: {pending}"
            seen = set()  # IDs are unique within a batch; completed rounds may reuse them.
            for call in message.get("tool_calls", []):
                assert call["id"] not in seen, "Duplicate tool call id"
                seen.add(call["id"])
                pending.add(call["id"])
    assert not pending, f"Unanswered tool calls: {pending}"


def tool_reply(agent, call_id):
    replies = [m["content"] for m in agent.messages if m.get("tool_call_id") == call_id]
    assert len(replies) == 1
    return replies[0]


def drive_repl(monkeypatch, agent, inputs):
    remaining = iter(inputs)
    consumed = []

    def prompt(*args, **kwargs):
        value = next(remaining)
        consumed.append(value)
        return value

    monkeypatch.setattr(cli, "pt_prompt", prompt)
    cli._repl(agent, Config(model="offline"))
    return consumed


class RecordingLLM(ScriptedLLM):
    def __init__(self, script=None):
        super().__init__(script if script is not None else [LLMResponse(content="done")])
        self.requests = []

    def chat(self, messages, **kwargs):
        self.requests.append(messages)
        return super().chat(messages, **kwargs)


def stream_chunk(arguments=None, usage=None, name="write_file"):
    tool_calls = None if arguments is None else [SimpleNamespace(
        index=0, id="c1", function=SimpleNamespace(name=name, arguments=arguments),
    )]
    delta = SimpleNamespace(content=None, tool_calls=tool_calls)
    return SimpleNamespace(usage=usage, choices=[SimpleNamespace(delta=delta)])
