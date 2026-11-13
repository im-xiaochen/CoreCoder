# An agent is, at heart, a while loop

If I had only one sentence to explain a coding agent, I'd put it like this: it's a loop that keeps asking the model "what's next," does what the model says, reports the result back to the model, and repeats until the model says "no need to act, I have the answer."

It sounds almost disappointingly plain. But that is the truth of it. Claude Code took this same thing and built it out to hundreds of thousands of lines, yet the most central piece, the part public teardowns call `query.ts`, is at its core a `while` loop of around seventeen hundred lines. CoreCoder writes the same loop in `corecoder/agent.py`. The shape is familiar, and you can follow it in one file.

In this piece we read that loop closely, section by section.

## The skeleton first

Start at [`Agent.chat_result()`](../corecoder/agent.py). It opens the run budget and usage scopes, then hands control to `_chat_result_unscoped()`. Here is the flow, with error handling and bookkeeping left out:

```text
append the user's message
for each allowed round:
    prepare the next request and check the context budget
    ask the model
    if the reply has no tool calls, record it and return completed
    validate the call envelope and record the assistant reply
    execute the calls and append their paired tool replies
return max_rounds
```

The work in the first step of each round lives in `_run_turn()`. It passes the system prompt, tool schemas, history, and output allowance to `ContextManager.prepare_request()`. If they still don't fit after the preparation described in [chapter four](04-context_EN.md), the run stops before calling the model. Otherwise it uses the prepared history for the next request.

A reply with no tool calls ends the loop. That tells you the model stopped asking for work. Read the recorded tool outcomes to see whether a test passed or a file was actually changed.

## Why for, not while(true)

CoreCoder uses `for _ in range(self.max_rounds)`, with a default of 50. A model can get stuck reading the same file, deciding it needs another look, and doing it all over again. At the round cap the structured result says `max_rounds` and keeps the text produced so far.

The round limit bounds trips through the loop. A [run budget](03-llm-and-cost_EN.md) separately limits model requests, tokens, or estimated cost on the OpenAI-compatible path. A retry, summary, or delegated call can consume that shared budget without consuming a new parent round.

Use `chat_result()` when you need status, tool errors, or usage. The older `chat()` wrapper returns text and propagates failure and interruption as exceptions.

## What a tool result looks like

An OpenAI-compatible assistant message with `tool_calls` needs a matching `tool` reply for each call. After checking the call envelope, the loop records each result with its call ID. The following line is the append operation from the loop:

```python
self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": outcome.content})
```

The model sees the reply content. The caller can also inspect structured outcomes that distinguish success, refusal, failure, cancellation, and an unknown result. Chapter four comes back to why these pairs must stay together during compaction.

## How a single tool call tells "bad arguments" apart from "the tool blew up"

There are two chances to catch malformed arguments in `_run_tool_call()`. First it checks parsing and the tool's JSON Schema. Then, when Python exposes a signature, it tries `inspect.signature(tool.execute).bind(**args)`. These checks run before tool callbacks, hooks, authorization, and execution.

Why bind the arguments before the call? A mismatch in the model's arguments and a `TypeError` raised inside the tool need different responses. The first asks the model to correct the call. The second says execution failed. [`test_exec_tool_distinguishes_bad_args_from_internal_error`](../tests/test_core.py) keeps that distinction in place.

## Each agent only knows its own set of tools

Tool lookup uses `self._tool_by_name`, built from that instance's tools. A [child agent](05-parallel-and-subagents_EN.md) can receive a narrower tool set and a forked state. If a tool was left out, a call by name still can't reach it. [`test_agent_tool_scope_is_per_instance`](../tests/test_core.py) exercises this with an agent that only has `read_file`. Chat mode goes further and hides tool schemas while refusing any tool call the provider sends anyway.

## The instant the model gets interrupted

Ctrl+C can arrive after the assistant returned a batch of calls but before every tool has replied. Sending the unfinished batch back to the model would leave unmatched call IDs. The loop has to close those pairs even when the run can't continue.

`_answer_pending_tool_calls()` keeps replies already written, recovers cached outcomes where available, and fills only the missing ones. A call stopped before execution can be marked cancelled. If it had already started and its result can't be determined, the outcome stays unknown. A repaired history lets the conversation continue later without claiming that an interrupted command succeeded or that its effects were undone.

`chat_result()` reports `cancelled`. An interruption inside the legacy single-task sub-agent also reaches the parent, which stops instead of asking the model for another round. [`test_interrupt_backfills_missing_tool_replies`](../tests/test_core.py) checks that an existing reply isn't duplicated.

## Compared with Claude Code

The basic shape is familiar from the Claude Code walkthrough: assemble messages, ask the model, run tools, and feed replies back. CoreCoder gives each part a place you can open directly: [provider retries](../corecoder/llm.py), [context preparation](../corecoder/context.py), [run budgets](../corecoder/budget.py), and [tool execution](../corecoder/agent.py). If a run stops early, its structured result is the first clue to which part stopped it.

## Wrapping up

Read the loop once for the happy path and again for the points where work can stop. Tool replies have to stay paired on both paths, and the round cap and request budget protect different resources. Next, we'll look at [the tools](02-tools_EN.md) that let the loop act.
