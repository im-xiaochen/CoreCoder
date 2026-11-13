<div align="center">

# CoreCoder

**The nanoGPT of coding agents. A 2,572-line engine inside 6,754 lines of Python: understand how a coding agent actually works, then fork your own.**

*learn from it · fork it · ship something better*

[中文](README_CN.md) | English | [Source-reading series · 9 bilingual essays](article/00-index_EN.md)

[![PyPI](https://img.shields.io/pypi/v/corecoder)](https://pypi.org/project/corecoder/)
[![Engine](https://img.shields.io/badge/engine-2572_LoC-blue)](corecoder)
[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://github.com/im-xiaochen/CoreCoder/actions/workflows/ci.yml/badge.svg)](https://github.com/im-xiaochen/CoreCoder/actions)
[![essays](https://img.shields.io/badge/source--reading-8_bilingual-orange)](article/00-index_EN.md)

</div>

- **Readable end to end.** Read the whole engine in an afternoon, with no magic hidden anywhere you can't follow it.
- **Hackable.** Set a breakpoint on any line, change it, rerun, all on your own machine. It genuinely works, which makes this a living reference rather than a diagram.
- **The gaps are the point.** It deliberately keeps only the minimal core; what's missing isn't half-finished, it's where you branch off and make it your own.

## How it compares

| | CoreCoder | Claude Code | aider | nanoGPT |
|---|---|---|---|---|
| Lines of code | 2,572 engine / 6,754 total (7,667 physical) | hundreds of thousands (closed) | tens of thousands of Python | ~600 (two files) |
| Time to read it all | one afternoon | can't (closed) | a few days of slogging | one afternoon |
| Breakpoint, change, rerun? | yes, every line | no | yes, but there's a lot | yes |
| What it's for | understand one, then fork your own | production coding assistant | terminal pair-programming | minimal GPT for teaching |

The nanoGPT column is there as a reference point: minimal, readable, but it teaches you to train a GPT. CoreCoder is after the same thing, only the subject is an agent that actually edits code. Sitting it next to Claude Code and aider isn't about competing for their users. CoreCoder is the foundation you stand on while you learn from them and get going; it isn't in the same race.

## What this is

I've always felt coding agents get talked about as if they were arcane. Strip a tool like Claude Code or Cursor all the way down and the core is a `while` loop wrapped around a large model, plus seven or eight tools that let it actually do things. The hard part was never the loop; it's everything the loop has to cope with once it meets the real world. CoreCoder is the minimal version that writes that core out honestly.

The `corecoder` package contains 42 files and 7,667 physical lines (6,754 net, excluding comment-only and blank lines). The growth since the original snapshot includes plan mode, hooks, checkpoints, request budgeting, tool validation, usage accounting and read-only teams.

And it really runs: reads and writes files, executes shell, spawns sub-agents, budgets requests and archives context, and tells you the tokens and dollars a run burned whenever you ask. Anything that would mutate your disk or run a command stops for your consent first. The regression suite checks these behaviors. But the point of it running isn't to become your daily driver. It runs so the walkthrough can't lie: a reference that shows how an agent works has to actually work.

The code came out of a public teardown: open analyses have already exposed a lot of the load-bearing architecture inside production agents like Claude Code. I took the most essential layer and rewrote it honestly, in as little code as I could. So reading CoreCoder is roughly like reading a runnable, annotated take on how that kind of agent works, except it's only a minimal reimplementation, sitting right there on your machine for you to take apart and change.

<p align="center">
  <img src="https://raw.githubusercontent.com/he-yufeng/CoreCoder/main/assets/demo-plan-hooks.gif" width="760"
       alt="Plan mode in action: the agent reads fib.py, gets its edit refused while plan mode is on, presents a plan, and only after approval edits, tests, and reports — with Pre/PostToolUse hooks firing around every call.">
</p>

<p align="center"><sub><i>These thousand lines really do run a full loop end to end: ask it to fix buggy.py and it reads the file, edits the code, runs it once to confirm, then reports back on its own. Watch it, then come back and read the code.</i></sub></p>

This README follows the same arc: the first half helps you **read it** (the code map, the main loop, nine essays), the second half helps you **fork it** and points at a few directions worth pushing further.

## Run it once first (five minutes before you read)

Before you read the source, get it running on your machine once to build some intuition. It's a foundation meant for forking, so the recommended path is to clone it and install editable, reading and changing as you go:

```bash
git clone https://github.com/im-xiaochen/CoreCoder
cd CoreCoder
pip install -e .
```

If you just want to get it running first, `pip install corecoder` works too.

Give it a model and a key and it goes. It speaks the OpenAI-compatible API by default, and switching providers is usually just two environment variables:

| Provider | Example env vars |
|---|---|
| OpenAI (default `gpt-5.5`) | `OPENAI_API_KEY=sk-...` |
| DeepSeek | `OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com CORECODER_MODEL=deepseek-chat` |
| OmniRoute | `OPENAI_API_KEY=your-key OPENAI_BASE_URL=http://localhost:20128/v1 CORECODER_MODEL=auto` |
| Local Ollama | `OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 CORECODER_MODEL=qwen2.5-coder` |

Kimi, Qwen and the like are the same two variables; for providers that don't even offer an OpenAI-compatible endpoint, the optional LiteLLM backend (`pip install "corecoder[litellm]"`) routes to a hundred-plus of them. The third essay goes into this in detail. Thinking models are first-class too: deepseek-reasoner, kimi-k3 and friends stream their chain-of-thought, and CoreCoder shows it dimmed as it works, kept out of the conversation history so providers never see it come back. The key can be `export`ed directly or dropped into a `.env` at the project root, which is loaded on startup. Then:

Smoke-tested end to end (read the file, edit it, run it, report back) against DeepSeek, Qwen3 and Kimi K2 via a single OpenRouter-compatible endpoint; each completed the full loop. One note for one-shot scripts: `-p` refuses mutating tools unless you pass `--yes`, by design.

```bash
corecoder                                             # interactive REPL
corecoder -p "add error handling to parse_config()"   # one-shot mode, exits when done
```

## Read it: the code map

Laid out flat, the whole project is this big. Skim it before you clone and you'll know where everything is. This is the most concrete difference from Claude Code's hundreds of thousands of lines: you can read it like the table of contents of a book. Start from the main loop in `agent.py`; that's the heart of the whole agent.

```
corecoder/
├── team.py         dependency scheduler and stop coordination
├── agent.py        agent loop + parallel tool exec   ← start here
├── llm.py          streaming client + retry + cost
├── context.py      request budget + evidence compaction
├── session.py      validated atomic save / resume
├── state.py        session state + execution evidence
├── permissions.py  consent for mutating tools
├── hooks.py        Pre/PostToolUse shell hooks
├── shell.py        POSIX shell routing (Git Bash on Windows)
├── mcp.py          MCP stdio client + paged discovery
├── prompt.py       system prompt
├── cli.py          REPL + slash commands + one-shot
├── results.py      structured run result + exit codes
├── usage.py        immutable request usage ledger
├── validation.py   config and session validators
├── config.py       env-var config
├── checkpoints.py  /undo snapshot and restore
├── demo.py         offline end-to-end demo
└── tools/
    ├── bash.py       shell + dangerous-command gate + cd
    ├── edit.py       unique-match search/replace + diff
    ├── grep.py       content search
    ├── glob_tool.py  filename matching
    ├── read.py       file read
    ├── write.py      file write
    ├── todo.py       agent-maintained task checklist
    ├── agent.py      sub-agent spawning
    └── base.py       tool schema validation + outcomes
examples/
└── plan_hooks_demo.py  offline plan mode + hooks demo (no API key)
```

Nine tools: `bash`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `repo_map`, `todo_write` (a task checklist the agent maintains for itself), and `agent` (which spawns a sub-agent). Everything else is the CLI shell, config, and packaging wrapped around that engine core. If `~/.corecoder/mcp.json` exists, its MCP servers join these as extra `mcp__*` tools; the MCP section below covers it.

## A `while` loop is the whole agent

The whole of an agent fits in one sentence: hand the user's words to the model, run whatever tools it asks for, stuff the results back into the context, ask again, and keep going until it stops asking for tools and gives an answer. In code, that's about a dozen lines:

```python
# corecoder/agent.py · the main loop (trimmed skeleton)
def chat(self, user_input):
    self.messages.append(user_input)

    for _ in range(self.max_rounds):                   # bounded, so it can't run away
        reply = self.llm.chat(self.messages, self.tools)   # ask the model what to do next
        if not reply.tool_calls:                       # model wants no more tools
            return reply.text                          #   -> done, hand the answer back
        results = run_parallel(reply.tool_calls)       # tools requested -> run in parallel
        self.messages += results                       # feed results back, loop again

    return "(hit the round limit)"
```

That's the whole thing. The core skeleton is about twenty lines; counting parallel execution and the bookkeeping after a Ctrl+C interrupt, maybe forty. Almost everything else in CoreCoder's thousand-odd lines is there to clean up the mess the loop runs into once it meets the real world. `llm.py` ends up the biggest file in the engine, not because calling a model is hard, but because a streamed response splinters each tool call's arguments into fragments you have to restitch in order, a provider will hand you half a JSON object or a null `usage` field, and 429s, timeouts, dropped connections and 5xx all need backoff-and-retry while the other 4xx should just raise. A stream can also die after it has already started, so the retry wraps the whole request, not just the connect. That unglamorous grunt work, not the loop, is where the real engineering of taking an agent from demo to delivery actually lives; the third essay follows it down to the line.

Three decisions are worth a closer look, because they're the kind of call you can only make after you've understood how others did it, and they're judgments you can lift straight into your own fork.

**`edit_file` does search-and-replace on a unique match, not line numbers.** Line numbers are a trap: the model only has to miscount by one and it quietly edits the wrong place. Anchor on a unique snippet of the original instead. If there's no match, it hands the start of the file back so the model can re-anchor; if there are several matches, it makes the model bring more surrounding context rather than gamble on one. On a successful edit it returns a diff. Recoverable on failure, verifiable on success: the whole loop stays inside the tool.

**Every model request passes a context budget check.** The check includes the system prompt, tool schemas, history, a safety margin and the configured output limit. Large tool results and older history are archived before compression; previews retain paths and hashes for later retrieval. If the request still does not fit, or archiving fails, the agent stops with a clear error and preserves the original history. Evidence summaries retain tool parameters and actual execution outcomes.

The `agent` tool accepts either one legacy task or a team of up to eight named tasks. Team members run with isolated state and trusted read-only tools, at most four concurrently. Optional dependencies control scheduling; full member output is retained as a managed artifact while bounded summaries and evidence references return to the parent. Team members share the run budget and usage ledger.

For example, the model can call `agent` with:

```json
{"tasks":[{"name":"inspect","task":"Inspect the parser"},{"name":"review","task":"Review the findings","depends_on":["inspect"]}]}
```

Plan mode permits this read-only team form after the normal authorization check. Chat mode exposes no tools.

Every one of these *whys* is traced down to the actual lines of code in the series below.

## The source-reading series · 9 bilingual essays

The bilingual source-reading series includes an introduction and nine chapters. Follow CoreCoder's loop, tools, and session handling, then build an extension or walk through Git review and verification. Each chapter links to the corresponding implementation.

- **[Intro · Read Claude Code through CoreCoder, then build your own](article/00-index_EN.md)**
- **[01 · An agent, at its core, is a `while` loop](article/01-the-loop_EN.md)** — the main loop in `agent.py`, interrupts, and the round limit
- **[02 · The tool system: letting the model act, safely](article/02-tools_EN.md)** — the nine tools in `tools/` and the bash safety gate
- **[03 · Plug in any LLM, and keep the bill honest](article/03-llm-and-cost_EN.md)** — `llm.py`'s provider wrapper, retries, and cost accounting
- **[04 · Surviving a long task on a finite window](article/04-context_EN.md)** — `context.py`'s three-tier compaction and orphaned tool messages
- **[05 · Parallel execution and sub-agents](article/05-parallel-and-subagents_EN.md)** — read-only teams, task dependencies, and sub-agent isolation
- **[06 · Turning it into a real command-line tool](article/06-session-and-cli_EN.md)** — saved sessions, structured CLI results, and Web reconnection
- **[07 · Fork CoreCoder into your own coding agent](article/07-build-your-own_EN.md)** — from fork to custom tools to swapping models
- **[08 · Three ways to extend without touching the loop: MCP, hooks, and plan mode](article/08-extensibility_EN.md)** — stdio MCP, hooks, session modes, and project skills
- **[09 · From code change to handoff](article/09-review-and-verify_EN.md)** — Git review, project checks, and offline evaluation

## Fork it, build something better

Once you understand it, the natural next step is to fork. Getting started doesn't take much:

- **Swap in a model you actually use.** It's the two env vars from above; `llm.py` (612 lines) is the entry point for all provider adaptation.
- **Add a tool of your own.** Write a new file against the tool base class in `tools/base.py` (77 lines): run tests, fetch a page, call an LSP, whatever. The end of the second essay walks you through your first one by hand.
- **Rewrite the system prompt.** `prompt.py` is all of 50 lines; change one line and you'll watch the agent's temperament shift. It's the cheapest "change one thing, see a result" in the whole project.
- **Import it as a library.** The top level exports `Agent`, `LLM`, and `Config`, ready to embed in your own program:

```python
from corecoder import Agent, LLM

llm = LLM(model="deepseek-chat", api_key="sk-...", base_url="https://api.deepseek.com")
print(Agent(llm=llm).chat("find every TODO comment in this project and list them"))
```

The current implementation also leaves a few directions open for forks:

- **The dangerous-command blocking in bash is just a regex blacklist.** It guards against slips, not a security sandbox. Facing untrusted input means reaching for seccomp or container isolation. This is the hardest of the four; it goes all the way down to the syscall and isolation layer.
- **Model fallback.** CoreCoder retries eligible requests and supports per-run budgets. A fallback across providers would need to preserve those limits and make the destination of each request visible.
- **Isolated editing teams.** The team form already runs read-only tasks in parallel. Extending it to edits would need separate workspaces and a way to review competing changes.
- **MCP resources and prompts.** The stdio client discovers and invokes tools. Resource and prompt support would extend what it can present to a session.

Chapter seven provides a worked example of adding and testing a local tool.

## Commands

Inside the REPL, `/help` lists everything; these are the ones you'll reach for:

```
/model <name>    switch model
/compact         compact the context by hand
/tokens          token usage and cost estimate
/diff            files changed this session
/undo            revert the most recent file change
/plan            toggle plan mode (read-only, then a plan to approve)
/save  /sessions save / list sessions
/mcp             MCP server status; /mcp reconnect <name> revives a dead one
quit / exit      exit (Ctrl+C cancels the current round)
```

Session IDs are sanitized before they become filenames, and conversation saves live under `~/.corecoder/sessions`. Undo checkpoints are scoped by workspace and session under `~/.corecoder/checkpoints/` (or `CORECODER_DATA_DIR`). Save with `/save` and resume that ID from the same workspace to reopen its undo history. `/undo` protects files changed after the agent write; `/undo --force` displays the target and asks for confirmation before overwriting a content conflict. Symbolic links and non-file replacements remain protected.

For scripts, run `corecoder --json --max-rounds 10 -p "inspect this project"`. Standard output contains one JSON object with the accumulated answer, tool errors, run usage, and completion status; tool and reasoning displays go to standard error. Exit codes are `0` for completion, `2` for the round limit, `130` for cancellation, and `1` for failure. Library callers can use `Agent.chat_result()` for the same structured result; `Agent.chat()` continues to return text and propagate exceptions.

Usage is recorded per request attempt with a fixed model and rate snapshot. Switching models does not reprice history. `/tokens` reports known cost separately from requests with unknown rates or missing usage. Legacy aggregate counts resume with unknown prices.

Override rates in `~/.corecoder/pricing.json`, in USD per million input/output tokens. Model-only entries such as `{"my-model": [1, 2]}` apply to the default endpoint. For a custom endpoint, use `{"endpoints": {"https://example.com/v1": {"my-model": [1, 2]}}}` with the exact configured URL. Invalid rates are ignored. Changes affect subsequent requests; recorded requests keep their original rate snapshots.

`/mcp reconnect <name>` starts a fresh process and repeats protocol validation and complete paginated discovery. In-flight calls fail without replay. If tool names, descriptions or schemas change, reconnect fails and asks you to restart the session so the agent can register the new definitions.

## Permissions

Read-only tools (`read_file`, `glob`, `grep`, `todo_write`) run the moment the model asks. The mutating ones (`edit_file`, `write_file`, `bash`, and spawning a sub-agent) stop for consent first, and the REPL banner shows which mode you're in:

- In the REPL you get one prompt per call: allow once, always allow this tool, or deny. "Always" is remembered per tool and persists in `~/.corecoder/permissions.json` across restarts; a sub-agent inherits the same layer, so consent follows the work wherever it happens.
- In one-shot mode (`-p`) there is nobody to ask, so a mutating call is refused on the spot and the refusal goes back to the model as an ordinary tool result: the loop never hangs on input that can't arrive. Pass `--yes` to approve everything up front (scripts, CI).
- The decision itself is pure logic in `permissions.py`, with the terminal only supplying the prompt callback. You can unit-test consent without a TTY, or reuse the layer in your own embedding.

## Plan mode

`/plan` toggles plan mode in the REPL. While it's on, the prompt shows `(plan)` and every mutating call (writes, edits, bash, MCP tools, sub-agents) is refused on the spot: the refusal goes back to the model as an ordinary tool result, telling it to keep investigating read-only and present a numbered plan instead. When the plan looks right, `approve` (or `/plan` again) hands control back and the agent executes. Mechanically it is one flag on the `Agent` plus one refusal branch ahead of the consent gate, which itself stays untouched; there is no plan file and nothing is remembered between sessions.

## Hooks

Drop a `hooks.json` under `~/.corecoder` and your own shell commands run around every tool call, the same idea as Claude Code's hooks:

```json
{
  "PreToolUse":  [{"matcher": "bash", "command": "cat >> ~/.corecoder/audit.jsonl"}],
  "PostToolUse": [{"matcher": "*",    "command": "cat >> ~/.corecoder/trace.jsonl"}]
}
```

Each hook gets the call as JSON on stdin (`tool_name`, `tool_input`; post hooks also get `tool_response`). The matcher is an exact tool name; empty or `*` fires on every tool. A pre hook can veto the call with exit code 2, and its stderr travels back to the model as the reason so it can route around the block. Post hooks only observe and can never block. A hook that errors or runs past ten seconds is skipped with a warning: hooks assist the loop, they never get to kill it. The whole mechanism is `hooks.py`, and the REPL banner shows how many hooks loaded.

Two worth stealing (the commands lean on `jq`, the usual suspect):

```bash
# 1. lint gate: after every edit/write, run the project's fast linter on the
#    touched file. The model sees the output and fixes its own mistakes in
#    the same turn instead of waiting for CI.
{
  "PostToolUse": [{
    "matcher": "edit_file",
    "command": "f=$(jq -r .tool_input.file_path); ruff check \"$f\" 2>&1 | head -20"
  }]
}

# 2. write protect: refuse edits under paths you never want an agent to
#    touch. Exit code 2 vetoes the call and the message reaches the model.
{
  "PreToolUse": [{
    "matcher": "edit_file",
    "command": "case \"$(jq -r .tool_input.file_path)\" in .env*|*/secrets/*|*.pem) echo 'that path is off-limits' >&2; exit 2;; esac"
  }]
}
```

Both are plain shell; nothing here is CoreCoder-specific syntax beyond the JSON shape and the exit-code-2 veto.

## MCP servers

Drop a `mcp.json` under `~/.corecoder` and tools from any MCP server join the agent over stdio, the same config shape as Claude Code's:

```json
{
  "mcpServers": {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}
  }
}
```

Each configured server starts as a subprocess at launch, handshakes, and lists its tools; every one is registered as `mcp__<server>__<tool>`, so hook matchers and the consent gate treat it exactly like a built-in. MCP tools stay out of the read-only set, meaning the agent asks before running one. The handshake gets fifteen seconds, a call gets sixty, and a server that dies or never answers fails that one call as an ordinary tool result instead of killing the loop. The REPL's `/mcp` lists every server with its state, and `/mcp reconnect <name>` starts a dead one fresh: new process, new handshake, and the tools the agent already holds work again with no restart. The client speaks the tools slice of the protocol (initialize, tools/list, tools/call) and nothing else, which keeps the whole thing inside `mcp.py` in a single module. With no `mcp.json` there is no MCP and nothing changes. A runnable starter config lives in [`examples/mcp_filesystem.md`](examples/mcp_filesystem.md): the reference filesystem server, scoped to one directory, behind the same consent gate.

## Related Projects

If working through CoreCoder was useful, here are a few other tools I've built around agents and LLM systems:

- **[RepoWiki](https://github.com/he-yufeng/RepoWiki)** — dropped into an unfamiliar codebase? It gives you a guided wiki and a where-to-start reading path, a self-hostable DeepWiki alternative.
- **[FindJobs-Agent](https://github.com/he-yufeng/FindJobs-Agent)** — stop sifting job boards by hand: it ranks postings against your resume and runs mock interviews.
- **[ContractGuard](https://github.com/he-yufeng/ContractGuard)** — catch the risky clauses before you sign: it reads contracts and flags the dangerous bits.
- **[GitSense](https://github.com/he-yufeng/GitSense)** — want to contribute to open source? It finds issues worth your time and gauges whether your PR will get merged.
- **[CodeABC](https://github.com/he-yufeng/CodeABC)** — understand any codebase even if you don't code, built for non-programmers.

## Contributing / License

Before you send anything, run `pytest tests/ -q` `ruff check`, and `compileall`, and make sure they're green. MIT licensed: fork it, learn from it, ship something better. A mention of this project is appreciated.

---

By [Yufeng He](https://github.com/he-yufeng), formerly at Moonshot AI (Kimi). I earlier wrote a fairly complete [Claude Code source analysis](article/claude-code-source-analysis.md) (written in Chinese); this project is its hands-on counterpart: that one walks you through reading it, this one through rebuilding it.

> CoreCoder was formerly named NanoCoder; it was renamed to avoid confusion with [Nano-Collective/nanocoder](https://github.com/Nano-Collective/nanocoder), and old links redirect here automatically.

Project rules: in code and plan modes CoreCoder reads `AGENTS.md` from the workspace root through the current directory, with bounded file sizes. Chat mode does not discover project rules. Use `/rules` in the CLI or the workspace panel on the web to inspect sources and skipped files.

## Git review, project checks, and offline evaluation

The [change review walkthrough](article/09-review-and-verify_EN.md) follows these features through the Web workbench, CLI, and source code.

Open the Git workspace tab to review HEAD-to-index and index-to-worktree diffs. Select paths and explicitly confirm staging or committing; review tokens reject stale state. Changes observed during a run may also come from external edits.

Project checks are manually authorized commands in `.corecoder/checks.json`. The web Checks tab displays commands before execution and retains result history. In the CLI, `/checks` lists checks and `/check <id>` asks for confirmation. Automatic repair is disabled; check history is available in the web interface.

```json
{"version": 1, "checks": [{"id": "test", "name": "Tests", "command": "python -m pytest", "cwd": ".", "timeout": 120, "files": ["*.py"]}]}
```

From a source checkout (the wheel does not include the web backend), run the isolated scripted regression suite with `python -m corecoder.evaluation --output ./evaluation-output`; add `--task agent` to select one fixture. Reports and fixture repositories are retained under the output directory. The scripted model and synthetic costs measure system behavior, not model quality or real spending.

## Repository search, skills, and run budgets

`grep`, `glob`, and `repo_map` scan the current workspace on demand. Use `offset` and `limit` to page results; edits between calls can change page boundaries. Search respects ignore rules and excludes sensitive paths and links. Scans have file, byte, output, and time limits; an incomplete marker means results may be partial. Regex matching runs in an isolated worker with a five-second deadline. A fixed-repository comparison found the new isolated search slower on small queries; it is not a speed guarantee.

Use `/skills` to list names, sources, and conflicts; invoke `/<name> [task]` explicitly. For conflicts or reserved command names use `/<name>@<source> [task]`, for example `/review@builtin changes`. Project skills live in `.corecoder/skills/<name>/SKILL.md`; optional frontmatter supports `version:` and comma-separated `files:`. At most eight relative text files and 32 KiB total are loaded. See [the project skill example](designDocument/priority_2026_11_08/SKILLS.md). Skills are user-provided text and do not grant additional permissions.

Set per-run limits with `--budget-requests`, `--budget-tokens`, `--budget-cost` (decimal USD), and optionally `--budget-unknown-policy allow_unknown`. Web offers the same controls. Zero prohibits that resource; empty limits keep the default unrestricted behavior. Parent, child, retries and summaries share one run budget. With a cost cap, unknown prices are denied by default; explicit `allow_unknown` reports uncertainty. Token inputs are estimates and provider billing can differ. Budget enforcement currently supports the OpenAI-compatible adapter; budgeted LiteLLM requests are rejected before sending.
