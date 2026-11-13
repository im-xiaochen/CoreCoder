# Surviving a long task in a finite window

An agent has one physical constraint it can't get around: the context window is only so big.

And coding tasks happen to be prolific token producers. The model reads a thousand-line file, and those thousand lines, line numbers and all, go into the history; it runs a test, and several hundred lines of output go into the history; it greps once, and dozens of matches go into the history. A halfway-decent task running a dozen-odd rounds burns tens of thousands of tokens. Once the window fills, either the API errors or you have to cut the history, and cut it badly and the agent starts "forgetting": a file it read earlier it reads again, a decision it just made it overturns.

So fitting a long task into a finite window is one of the most hardcore subproblems in agent engineering. This piece looks at how `corecoder/context.py` (587 lines) solves it.

## Layered, lightest to heaviest

Start with [`ContextManager.prepare_request()`](../corecoder/context.py). Before each model call, `Agent._run_turn()` passes it the system prompt, tool schemas, and history. The check also accounts for a safety margin and the output reservation. If the request already fits, the agent can send it without compacting anything.

The estimator can use a tokenizer and otherwise approximates from Chinese characters, other text, and punctuation density. What it measures matters as much as how. `LLM.project_messages()` filters out reasoning fields that belong to a different model or endpoint before the estimator counts the request. A resumed session keeps that reasoning in its history, while the estimator and the wire request both leave it out.

The call below is abbreviated from `Agent._run_turn()` to show where the decision is made.

```python
prepared = self.context.prepare_request(
    self.messages,
    system=system,
    tools=self._tool_schemas(),
    output_reserve=output_reserve_for(self.llm, 4096),
    llm=self.llm,
)
if not prepared.budget_ok:
    raise ContextBudgetExceeded(prepared.reason or "context_budget_exceeded")
```

## Layer one: old tool outputs have a shelf life

When the request is too large, look for long tool results first. The compactor writes the full result to the session's artifact directory and leaves a preview with a path, SHA-256, and character range. The reference tells you where a conclusion came from and how to read the original again.

Imagine grep returned hundreds of matches. After the model has seen them, a preview often suffices for the next round. Keeping the full result on disk frees space in the window. If writing the archive fails, request preparation reports the failure and preserves the original history.

## Layer two: have the model write a summary of the old conversation

If shorter tool results still do not leave enough room, `prepare_request()` finds a boundary between complete tool turns. It saves the original messages, then builds an evidence summary with a reference to that archive. A summary distinguishes tools that succeeded from tools that failed, were refused, or were interrupted. A plan to run a test is different from a passing test result.

The summary request shares the [run budget](03-llm-and-cost_EN.md). The summary only replaces older messages in the request if it actually uses fewer tokens. Recent messages remain available for the task in progress.

## The trap that's bound to bite you: orphaned tool messages

An assistant message with `tool_calls` and its tool replies must stay together. A cut in the middle of those replies can leave a partial group that the provider rejects on the next call. `_safe_split()` moves the cut backward to avoid tool replies and assistant messages carrying tool calls. This is the same `tool_call_id` pairing from the [main loop](01-the-loop_EN.md).

## Layer three: the last resort

If the summary still does not fit, the compactor further shortens tool previews that already have full backups. If the fixed portions or latest user input are too large, or the request is still too large after compaction, preparation reports the diagnostic `context_budget_exceeded` and keeps the original history. The user can narrow the task or choose a model with a larger window.

## Compared with Claude Code

Earlier versions of CoreCoder used fixed window percentages for snipping, summarizing, and hard collapse. The current path brings those decisions into request preparation. It still tries cheaper steps first, and it decides whether to keep going by checking the space needed by the next request.

## What this piece leaves you with

- Check inputs and reserved output before each request. Estimate the messages that will actually be sent.
- Archive full results or original messages before replacing them with previews or summaries. Keep references for checking the evidence.
- Preserve tool-call and reply pairs. If the request cannot fit, keep the original history and report the failure.

Next comes [parallel execution and sub-agents](05-parallel-and-subagents_EN.md).
