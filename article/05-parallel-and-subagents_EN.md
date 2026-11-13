# Parallel execution and sub-agents

When piece one covered the main loop, I left a gap at the "execute tools" step: the model wanting one tool at a time and the model wanting several at once take two different paths. This piece first fills that gap, then covers a tool that lets the agent spawn a "clone" of itself. The two are really two sides of one theme: how to let one agent handle several things at once without scrambling them together.

## Several tool calls come back at once

The model can return several tool calls together. CoreCoder uses a pool of at most eight workers when every call in the batch is `read_file`, `glob`, `grep`, or `repo_map`. Workers finish at their own pace, while replies go back to the model in call order.

A mixed batch runs in order. If it writes a file and then reads the same file, the read can see the new contents. Follow [`Agent._exec_tools_parallel()`](../corecoder/agent.py) for the dispatch.

## Where this simplification falls short of Claude Code

Concurrency starts after the model has returned a complete batch. The loop collects the calls and then decides how to run them. Parallel reads and ordered mixed batches give the loop a small set of ordering rules you can check by inspection.

## Parallelism isn't free: shared mutable state will bite

The working directory belongs to the session. Bound tools resolve relative paths through [`SessionState`](../corecoder/state.py), and a child gets its own cwd and to-dos through `parent.state.fork()`. It starts in the parent's working directory. Later changes to the child's working directory leave the parent where it was.

Isolated state still shares any configured run budget. Each member records its own usage, while every physical model request draws from any configured parent run budget. The parent can report usage for the whole team.

## Sub-agents: spawning a clone of yourself

The `agent` tool accepts either `task` or `tasks`. The legacy single-task form starts one child and returns synchronously under the existing parent permissions. Tools are rebound to the child session. The child uses `chat_result()`, so failure and reaching the round limit have distinct outcomes. Ctrl+C propagates to the parent and stops the run.

The team form gives each task a name and uses `depends_on` to order them. For example, read the tool registry and the budget code independently, then combine the findings. Here are the arguments to the `agent` tool:

```json
{
  "tasks": [
    {"name": "registry", "task": "Read corecoder/tools/__init__.py and locate the registration entry point"},
    {"name": "budget", "task": "Read corecoder/budget.py and explain when request quota is reserved"},
    {"name": "summary", "task": "Summarize the findings and cite the evidence", "depends_on": ["registry", "budget"]}
  ]
}
```

The [scheduler](../corecoder/team.py) accepts one to eight tasks and runs at most four at once. It checks for duplicate names, unknown dependencies, and cycles before starting. A downstream task receives summaries and evidence references once its dependencies succeed. A failed task skips branches that depend on it, while unrelated branches can proceed. Exhausting the budget or cancelling the parent prevents new scheduling and sends. Requests already sent still need to settle. Each member returns a bounded summary and stores full output as a session artifact when the write succeeds. An artifact write error is reported separately.

## Why a sub-agent isn't allowed to spawn a grand-sub-agent

A single child has no `agent` tool, and team members do not get one either. Team members inherit trusted built-in read-only tools and their own to-do tool. They cannot run shell commands, edit the workspace, or call MCP. The parent still goes through its permission and hook flow, while members run without hooks. This keeps parallel investigation inside the team and leaves changes to the parent run.

## Compared with Claude Code

CoreCoder provides a read-only investigation inside one parent run. Explicit dependencies determine the schedule, and the parent collects the results during the run. The web workbench also records member state and restores its display after a disconnect, as described in [piece six](06-session-and-cli_EN.md).

## What this piece leaves you with

- A batch of reads can run in parallel. Mixed batches run in order.
- Children have separate session state and share the parent run’s configured budget.
- `depends_on` controls when work starts. Structured results include status, usage, and evidence.

Next comes [sessions and the CLI](06-session-and-cli_EN.md).
