# Three Ways to Extend the Agent: MCP, Hooks, and Plan Mode

By the end of [chapter seven](07-build-your-own_EN.md) you had a working agent. Now imagine three things you'd like to change. You want a tool supplied by another process, a check around a tool call, or a session that can investigate before you approve edits. Each attaches at a different point in the same loop.

## MCP: cutting the protocol down to the slice an agent uses

CoreCoder's [MCP client](../corecoder/mcp.py) starts a configured child process and exchanges newline-delimited JSON-RPC over stdin and stdout. Set the executable and arguments in `~/.corecoder/mcp.json`. The repository's [filesystem example](../examples/mcp_filesystem.md) shows the configuration shape and how to select the directories the example server may access.

Discovery starts with `initialize` and `notifications/initialized`. If the server advertises tools, the client calls `tools/list` and follows each `nextCursor` within one startup deadline. It rejects a repeated cursor, a duplicate tool name, or a malformed schema before registering the server's tools. The limits on pages and tools keep discovery bounded.

Each discovered tool is exposed as `mcp__<server>__<tool>` and calls through to `tools/call`. A reader thread matches replies by request ID, and a write lock keeps requests from mixing bytes on stdin. If the process dies, the CLI's `/mcp` shows its state and `/mcp reconnect <name>` restarts it. A successful reconnect must present the same tool names, descriptions, and schemas. Restart the session to register a changed tool set.

The stdio client lives outside the model loop. Once registered, the tools still go through argument validation, hooks, and the ordinary authorization path. Giving a tool a descriptive name or schema doesn't grant it read-only status in plan mode.

## Hooks: allowed to veto, never allowed to kill

[Hooks](../corecoder/hooks.py) attach your own shell commands to a call. They live in `~/.corecoder/hooks.json`. This is a configuration example. Supply your own local script before using it.

```json
{"PreToolUse": [{"matcher": "bash", "command": "python check.py"}]}
```

The core sends JSON on stdin. A matching `PreToolUse` hook can exit with code 2 to veto that call. Its stderr becomes the refusal reason the model sees. `PostToolUse` observes execution and receives the response alongside the tool name and arguments. A timeout or launch error is logged and skipped. A post hook can't retroactively change whether the tool ran.

The order matters. The agent validates the call before the pre hook, then checks mode and permission before executing the tool. Plan mode restricts tool execution, and a locally configured pre hook can still run before a call is denied. Chat mode declines tool calls before hooks. A read-only team's members don't inherit hooks, while the parent's delegation call follows the usual path. For a self-contained example, see [the offline plan and hooks demo](../examples/plan_hooks_demo.py).

## Plan mode: investigate before approving edits

In the CLI, `/plan` toggles plan mode. `approve` or `/approve` ends planning and sends approval to the model. The Web workbench exposes three session modes.

- **Code** offers the session's tools and applies its permissions. The CLI's `--yes` skips ordinary approval prompts.
- **Plan** permits the built-in read-only names in [`Permission.READ_ONLY`](../corecoder/permissions.py), including repository search and the session's local to-do list. It refuses ordinary mutating calls even with `--yes`.
- **Chat** sends no tool schemas and refuses unexpected tool calls. It also skips project rule discovery and skills.

Plan mode makes one narrow exception for delegation. The actual built-in `AgentTool` or Web `BrowserAgentTool` can receive the `tasks` form without `task`. The tool validates the task graph, and members receive a restricted tool set with independent session state. A tool with the same name doesn't qualify, and the legacy single-task form doesn't qualify. The parent delegation still passes its ordinary permission check. [Chapter five](05-parallel-and-subagents_EN.md) follows the scheduler itself.

## Where project instructions fit

In code and plan modes, [project rule discovery](../corecoder/project_rules.py) reads applicable `AGENTS.md` files along the path from workspace root to the current directory, with size and path checks. The rules go into the system prompt under the user's request and mode restrictions. The CLI's `/rules` shows what was loaded.

Skills come from a separate, explicit command path. Use `/skills` to see names, sources, and conflicts. Run a skill with `/<name> [task]`, or qualify its source when the name is ambiguous. The selected text gives the model guidance for this turn while permissions still come from the session. [Chapter six](06-session-and-cli_EN.md) puts these commands alongside the rest of the CLI.

MCP adds tools, hooks act around a call, and session modes decide which calls may run. Keeping those decisions at the tool boundary is what lets the same loop serve the terminal and the Web workbench.
