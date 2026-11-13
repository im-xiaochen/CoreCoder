# The tool system: letting the model act, safely

In the loop from the last piece, one step got glossed over: executing tools. This piece unpacks it.

The model itself does only one thing, emitting the next text given the text so far. It can't read your files, can't run your tests, can't write a single byte to disk. What turns it from "able to talk" into "able to do" is tools. A tool is the hand through which an agent actually touches the world. So how strong an agent is depends largely on how well its tools are designed: whether the interface is clear, whether the error feedback lands, whether dangerous operations get stopped.

CoreCoder gives the model nine built-in tools: `bash`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `repo_map`, `todo_write`, `agent`. In this piece we first look at the skeleton they share, then dig into the two most worth discussing, and finally I'll have you write one of your own.

## What a tool looks like

Tools inherit from [`Tool`](../corecoder/tools/base.py) and supply a name, description, JSON Schema, and execution method. The model sees the definition wrapped by `schema()`, calls it by name with arguments, and gets back a string. Built-in object schemas reject undeclared fields by default.

The CLI uses [`create_tools(state)`](../corecoder/tools/__init__.py) to build fresh tool instances for a session. This factory is the current registration point.

```python
def create_tools(state=None):
    return [
        BashTool(state=state), ReadFileTool(state=state),
        WriteFileTool(state=state), EditFileTool(state=state),
        GlobTool(state=state), GrepTool(state=state),
        RepoMapTool(state=state),
        TodoWriteTool(state=state), AgentTool(),
    ]
```

`ALL_TOOLS` remains a compatibility export. Extend the factory to add a CLI tool. `grep`, `glob`, and `repo_map` query the workspace on demand and accept `offset` and `limit` for pagination. Search respects ignore rules and limits the work of each scan. An `incomplete` marker means results can be partial.

## edit_file: a key innovation that looks unremarkable

If I could only discuss one of the nine tools, I'd pick `edit_file`. Because "let the model modify an existing file," a need that looks simple, has several dead bodies behind it.

The first dead end is having the model patch by line number, say "replace line 42 with this." The problem is the model's sense of line numbers is wildly unreliable; the line 42 in its head and the real line 42 in the file often don't match, and being off by one means editing the wrong place. Worse, the moment something earlier in the file gets touched, every line number after it shifts.

The second dead end is having the model rewrite the whole file and send it back. Fine for small files, but once a file is large it's slow and expensive, and while the model is transcribing the parts it shouldn't touch, it'll occasionally fumble a character or two, and you'll have a hard time noticing.

The third dead end is having the model produce a standard diff/patch format. It sounds elegant, but in practice the model's unified diffs, with those `@@ -42,7 +42,8 @@` line-number headers, have an exasperating error rate; it can't get those context-line counts and offsets right.

Claude Code's solution, which CoreCoder copies wholesale, is the fourth path: search and replace, plus a uniqueness constraint. The model gives a chunk of "text to find" and a chunk of "text to replace it with"; the tool finds that text in the file, requires it to appear exactly once, then replaces it. Here's the core of `tools/edit.py`:

```python
occurrences = content.count(old_string)

if occurrences == 0:
    preview = content[:500] + ("..." if len(content) > 500 else "")
    return (
        f"Error: old_string not found in {file_path}.\n"
        f"File starts with:\n{preview}"
    )
if occurrences > 1:
    return (
        f"Error: old_string appears {occurrences} times in {file_path}. "
        f"Include more surrounding lines to make it unique."
    )

new_content = content.replace(old_string, new_string, 1)
```

The brilliance is all in that "exactly once."

If the text isn't found at all, it means the model misremembered the content; the tool doesn't guess, it pastes the file's opening back so the model can re-check. If the text appears more than once, the tool refuses, because it can't be sure which occurrence the model meant, so it replies "this text appears N times, include more surrounding lines to make it unique." That sentence isn't just an error; it's teaching the model how to fix its request: on the next round the model will dutifully expand `old_string` to include enough context until it's unique across the file.

This constraint turns "editing a file" from a fuzzy problem into a determinate one. The model doesn't need to understand line numbers, doesn't need to compute offsets; it only needs to quote verbatim a chunk of the code it wants to change, and the tool guarantees that quote is unambiguous. Claude Code's system prompt specifically reminds the model that "old_string must be unique in the file," and CoreCoder's prompt has the same rule. This is a model design that trades constraint for reliability.

After replacing, the tool also generates a unified diff to return to the model and the user:

```python
diff = _unified_diff(content, new_content, str(p))
return f"Edited {p}\n{diff}"
```

Notice the direction reversed: having the model generate a diff is unreliable, but having the tool generate a diff for the model to read is perfectly reliable. The model looks at this diff and confirms whether it edited correctly. Generation on the tool side, consumption on the model side, each doing its own job.

There's also an easily overlooked corner: confirm the file is UTF-8 text before editing.

```python
try:
    content = p.read_text(encoding="utf-8")
except UnicodeDecodeError:
    return f"Error: {file_path} is not a UTF-8 text file (edit_file only edits text files)"
```

Without this check, the moment the model accidentally runs `edit_file` on a binary file, what it gets back is a big lump of Python decode traceback, which pollutes context and helps nobody. With it, the model gets a sentence it can understand. Every error message handed to the model should be plain language, an implicit rule running through all of CoreCoder's tools.

The edit runs under the file mutation lock. It writes a temporary file, replaces the target, and finalizes an undo checkpoint.

## bash: keep dangerous operations out, but don't pretend it's a sandbox

[`BashTool`](../corecoder/tools/bash.py) checks for dangerous commands before launching them. Ordinary calls also go through the agent's permission flow. The command checks are a guard against mistakes. An untrusted deployment still needs a separate sandbox or isolation boundary.

[`run_command()`](../corecoder/command_runner.py) handles execution, timeouts, cancellation, and output collection. Long output keeps its beginning and end, and exit codes and execution status are available to the agent and UI. The tool runs in the session working directory. A successful `cd` updates `SessionState.cwd` when it can be tracked reliably. Complex directory changes produce a note that the session directory did not change.

## Two phases: validate shape first, then validate safety

[`Agent._run_tool_call()`](../corecoder/agent.py) checks for parse errors, looks up the tool, and validates arguments against both JSON Schema and the Python signature. Valid input then goes through display callbacks, hooks, permission checks, and execution. A tool failure can be distinguished from a call that never started. The model receives a textual reply paired with the call ID, while the CLI and web workbench can use the structured outcome.

## Beyond the gates: plan mode, hooks, and tools that live in another process

Plan mode refuses writes and shell commands even with `--yes`. It permits the built-in read-only team form subject to the existing permission checks. Chat mode does not expose tools.

PreToolUse hooks can veto an individual call. PostToolUse hooks observe its result. MCP tools register as `mcp__server__tool` and use the same call flow. See [piece eight](08-extensibility_EN.md).

## Hands-on: write your first tool

Create `corecoder/tools/now.py`. Checking the time holds no mutable state between calls, so the tool can declare `stateless = True`.

```python
import time

from .base import Tool


class NowTool(Tool):
    name = "now"
    description = "Return the current local date and time."
    stateless = True
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")
```

Add `from .now import NowTool` to the imports in `corecoder/tools/__init__.py`, then add `NowTool(),` to the list returned by `create_tools(state)`. Restart the CLI in code mode and ask it to use the now tool to report the current time. The usual permission flow still applies. [Piece seven](07-build-your-own_EN.md) adds a text-counting tool that can be tested in isolation.

## What this piece leaves you with

- Describe inputs with a schema and distinguish invalid arguments from failures during execution.
- Create session tools in `create_tools(state)` and bind state such as the working directory to the session.
- Edit by unique match. Permission checks, hooks, and command guards each cover a different part of the tool call.

Next comes the [model interface and costs](03-llm-and-cost_EN.md).
