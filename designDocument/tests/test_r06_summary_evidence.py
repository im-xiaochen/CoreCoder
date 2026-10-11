"""R06: facts present only in arguments or late history reach summary memory."""

from corecoder.context import SUMMARY_SYSTEM_PROMPT, ContextManager
from corecoder.llm import LLMResponse, ToolCall


def edit_messages():
    return [
        {"role": "user", "content": "USER_CONSTRAINT: preserve compatibility"},
        LLMResponse(tool_calls=[ToolCall("edit1", "edit_file", {
            "file_path": "only-in-args.py", "old_string": "OLD_RULE", "new_string": "NEW_DECISION",
        })]).message,
        {"role": "tool", "tool_call_id": "edit1", "content": "Edited successfully"},
        {"role": "assistant", "content": "REMAINING_TASK: run integration tests"},
    ]


def test_flatten_keeps_tool_identity_arguments_and_outcome():
    flat = ContextManager._flatten(edit_messages())
    for fact in ("edit1", "edit_file", "only-in-args.py", "OLD_RULE", "NEW_DECISION", "Edited successfully"):
        assert fact in flat


def test_fallback_summary_preserves_decisions_constraints_and_remaining_work():
    summary = ContextManager()._get_summary(edit_messages(), None)
    for fact in ("only-in-args.py", "NEW_DECISION", "preserve compatibility", "run integration tests"):
        assert fact in summary


def test_failure_is_not_reported_as_success():
    messages = edit_messages()
    messages[2]["content"] = "Error: old_string not found; NO_WRITE_OCCURRED"
    summary = ContextManager()._get_summary(messages, None)
    assert "NO_WRITE_OCCURRED" in summary
    assert "only-in-args.py" in summary


def test_summary_model_sees_late_facts_in_long_history():
    history = [{"role": "user", "content": f"Earlier observation {i}: " + "x" * 380} for i in range(80)]
    history += edit_messages()

    class Summarizer:
        def __init__(self):
            self.inputs = []

        def chat(self, messages, **kwargs):
            self.inputs.append(str(messages))
            return LLMResponse(content="only-in-args.py NEW_DECISION preserve compatibility run integration tests")

    llm = Summarizer()
    ContextManager()._get_summary(history, llm)
    joined = "\n".join(llm.inputs)
    assert "Earlier observation 0" in joined
    assert "Earlier observation 79" in joined
    assert "NEW_DECISION" in joined
    assert len(llm.inputs) >= 2, "Long history must be summarized in bounded chunks"


def test_summary_request_requires_evidence_bound_unverified_state():
    class Summarizer:
        def __init__(self):
            self.system = ""

        def chat(self, messages, **kwargs):
            self.system = messages[0]["content"]
            return type("Response", (), {"content": "REMAINING/UNVERIFIED: verify final output"})()

    llm = Summarizer()
    ContextManager()._get_summary(edit_messages(), llm)
    assert llm.system == SUMMARY_SYSTEM_PROMPT
    for phrase in ("user constraints", "evidence IDs", "Failed, refused, cancelled, or unknown", "never turn a remaining task"):
        assert phrase in llm.system


def test_large_arguments_keep_identity_and_tail(tmp_path):
    # Large argument bodies may be archived, but the operation and tail cannot vanish.
    message = LLMResponse(tool_calls=[ToolCall("large-edit", "edit_file", {
        "file_path": str(tmp_path / "big.py"), "old_string": "old",
        "new_string": "x" * 20_000 + "TAIL_DECISION",
    })]).message
    flat = ContextManager._flatten([message])
    assert "big.py" in flat and "edit_file" in flat and "TAIL_DECISION" in flat


def test_non_object_arguments_remain_failed_without_summary_crash():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "bad", "function": {
            "name": "edit_file", "arguments": "[]"
        }}]},
        {"role": "tool", "tool_call_id": "bad", "content": "Error: invalid_arguments arguments must be an object"},
    ]
    records = ContextManager.extract_records(messages)
    assert len(records) == 1 and records[0].status == "failed" and records[0].target == ""
    summary = ContextManager()._get_summary(messages, None)
    assert "bad" in summary and "failed" in summary
