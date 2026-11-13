# Turning it into a real command-line tool

The first five pieces dissected the agent's internals: the loop, the tools, the model interface, context compression, parallelism and sub-agents. As lovely as these parts are, you can't use them directly, because one more layer is missing, a skin, a command-line interface where someone can sit down and talk to it, save, resume, and check status. This piece covers that skin, corresponding to `cli.py` (548 lines) and `session.py` (148 lines).

It's not just "a nice-to-have UI." Hidden in this skin is a security detail well worth discussing, which we save for the finale.

## REPL: a conversation loop wrapped around a conversation loop

The terminal has two loops. [`_repl()`](../corecoder/cli.py) accepts user input, and the agent requests the model and runs tools within each turn. Exiting the terminal, ending a run, and cancelling an active tool each have their own handling.

The CLI displays text and reasoning through `on_token` and `on_reasoning` and presents tool activity through a callback. The structured result comes from `chat_result()`. The core can serve a terminal, a pipeline, or a web workbench through those interfaces.

## Slash commands: managing state without breaking the conversation

Slash commands come in a few groups. Manage conversations with `/reset`, `/model`, `/tokens`, `/compact`, `/save`, and `/sessions`. Manage the workspace with `/rules` to inspect rule sources, `/diff` to view changes, and `/undo` to restore the last file change. Undo protects external edits made after the tool ran. A content conflict requires confirmation with `/undo --force`.

`/plan` toggles CLI plan mode. `/checks` lists configured checks, and `/check <id>` shows the command and asks for confirmation. Use `/mcp` to see server state. Use `/skills` to list skills and their sources, then `/<name> [task]` to invoke one explicitly. Use `/<name>@<source>` for a name conflict. A skill supplies task material under the existing permissions.

The CLI resolves commands before sending a prompt. It handles installed skill commands and reports unknown slash commands, so a typo does not burn a model request.

## One-shot mode: making it scriptable

`-p` runs one prompt and exits. With `--json`, stdout contains a single structured result. Tool events and reasoning go to stderr, so a script can parse stdout directly.

```sh
corecoder -p "Explain how this project saves sessions" --json --max-rounds 8 --budget-requests 5
```

`RunResult` includes status, text, tool errors, and usage, with budget details when configured. Exit codes are `0` for completion, `1` for failure, `2` for the round limit, and `130` for cancellation. In one-shot mode, tools that need permission are refused by default. Add `--yes` only when you intend to authorize those operations. See [piece three](03-llm-and-cost_EN.md) for token and cost limits.

## The finale: don't let a session name tear up your filesystem

A saved session carries messages, model, and usage ledger. Use `/save` for its ID and `/sessions` to list archives, then resume with `corecoder -r <id>`. Undo history is scoped to the workspace and session. Resuming that ID in the same workspace can reopen its undo history.

[`session.py`](../corecoder/session.py) checks the destination in two steps. It normalizes the ID to a restricted filename, then resolves the final path and checks that its parent is the archive directory. The second check guards the actual destination. Saving writes a temporary file before replacing the target. Loading validates the archive, skips invalid files, and supports older formats through a compatibility path.

## Web: reconnect to the same run

The web workbench separates a run from the browser watching it. The backend gives each run an ID and stores ordered events and state in [`RunStore`](../web_backend/run_store.py). The frontend reconnects to [SSE](../web_backend/server.py) with its event cursor. If the cursor is older than the retained events, it resets the view from the server's snapshot. Reconnecting restores the view without executing the task again.

Team members from [piece five](05-parallel-and-subagents_EN.md) have their own status events. When the backend process restarts, unfinished runs and members are marked interrupted. Saved state remains available, and the user can start a new run for unfinished work. The workbench also offers mode switching, project rules, Git diff review, and a project checks panel.

## What this piece leaves you with

- The CLI uses callbacks to show progress and `RunResult` to give scripts a stable output.
- Session archives keep messages and usage, with path checks and atomic replacement.
- The web workbench restores the view from ordered events and snapshots. A restart marks unfinished runs interrupted.

Next, put those interfaces together in [your own agent](07-build-your-own_EN.md).
