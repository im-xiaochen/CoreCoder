# Read Claude Code through CoreCoder, then build your own

This is a series for engineers. It sets out to do two things.

First, use CoreCoder, a small open source project written in Python, to lay out clearly how a production-grade coding agent like Claude Code is built on the inside. Second, walk you through forking CoreCoder and turning it into a coding agent of your own.

## Why take the detour

Because Claude Code itself is too big. Its codebase runs into the hundreds of thousands of lines, and the BashTool alone, the part that runs shell commands, is over a thousand. Read it line by line and most people close the editor by the third file. Yet the core of an agent isn't really that complicated. What's complicated is everything engineered around it to survive the real world: what to do when the model gets interrupted mid-stream, when the context window fills up, when ten tool calls come back at once, when a provider hiccups and returns a 500. Spread across hundreds of thousands of lines, that skeleton is hard to see.

CoreCoder keeps that skeleton small enough to read. The [README](../README.md) tracks the current line counts. The same core runs the CLI and the Web workbench. It can inspect a repository, edit files with approval, dispatch read-only teams, and report what a run spent. That's enough machinery to trace a real request from the user's prompt to the model, through the tools, and back to the user.

Each chapter points to the implementation. Where a snippet leaves out bookkeeping, it says so. The source links are where to look when you're ready to follow every branch.

## Who this series assumes you are

I assume you've written code, called an API, roughly know how LLM function calling works, but have never actually cracked open an agent's main loop. You may have used Claude Code or Cursor, been impressed that it reads files, edits code, and runs tests on its own, and then wondered whether there's magic behind it or just engineering.

The answer is engineering. And the kind that, once you've read it, makes you think "that's it? I could write that too."

## How to read the eight pieces

The first six chapters unpack the core and its interfaces. Chapter seven builds a working extension, and chapter eight shows where tools, hooks, and session modes attach.

1. [An agent is, at heart, a while loop](01-the-loop_EN.md). Follow `Agent.chat_result()` through context checks, run limits, tool replies, and cancellation.
2. [The tool system: letting the model act, safely](02-tools_EN.md). Meet the nine built-in tools, including `repo_map`, and add a tool to the session's registry.
3. [Plug in any model, and get the bill right while you're at it](03-llm-and-cost_EN.md). Follow stream parsing, parameter fallback, same-source reasoning, the usage ledger, and per-run budgets.
4. [Surviving a long task in a finite window](04-context_EN.md). See how CoreCoder estimates the messages it will send, archives oversized evidence, and keeps tool calls paired with replies.
5. [Parallel execution and sub-agents](05-parallel-and-subagents_EN.md). Follow parallel read calls, isolated session state, and the dependency scheduler for read-only teams.
6. [Turning it into a real command-line tool](06-session-and-cli_EN.md). Start with saved sessions, slash commands, and JSON results, then follow the Web workbench as it reconnects to a run.
7. [Fork CoreCoder and build your own coding agent](07-build-your-own_EN.md). Add and test a custom tool, then use the agent as a library.
8. [MCP, hooks, and plan mode](08-extensibility_EN.md). See how the stdio client discovers tools, how hooks observe calls, and how the workbench distinguishes code, plan, and chat.

You don't have to read in order. Want to understand how it dares to run commands on its own? Jump to piece two. Want to plug in your own model? Jump to piece three. Just want to fork something usable fast? Piece seven stands on its own.

## Five minutes to get it running first

Before you read, let it run once on your machine, so the code that follows has a feel to it.

```bash
git clone https://github.com/im-xiaochen/CoreCoder
cd CoreCoder
pip install -e .
```

To try the loop without a key, run `corecoder --demo`. For a live run, pick one configuration below and use the key and model for that endpoint. When switching examples, open a clean shell or unset the previous `OPENAI_BASE_URL` and `CORECODER_MODEL` values.

```bash
# OpenAI
export OPENAI_API_KEY=sk-...

# or DeepSeek
export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com CORECODER_MODEL=deepseek-chat

# or a running local Ollama instance
export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 CORECODER_MODEL=qwen2.5-coder
```

Run it:

```bash
corecoder
```

Once you're in, give it any task, say "read corecoder/agent.py and tell me the most rounds this loop can run." If the model chooses `read_file`, the CLI shows the call before streaming the answer. What it does in that moment is, in essence, the same thing Claude Code does. The next six pieces take that same thing apart.

So let's start with that loop.

---

Author: [He Yufeng](https://github.com/he-yufeng). I wrote an earlier piece, a [Claude Code source code analysis](claude-code-source-analysis.md) (in Chinese), which leans toward a full teardown; this series takes a different angle, more about getting you to rebuild it by hand.
