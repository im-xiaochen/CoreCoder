# Fork CoreCoder and build your own coding agent

Over the first six pieces we took CoreCoder apart all the way through. The loop, the tools, the model interface, context compression, parallelism, sub-agents, the CLI, every part laid out on the table. This piece puts them back together, and assembles them into one that's yours.

Between understanding and building sits a single act of doing. This piece carries you across. When you're done, you'll have in hand a coding agent that runs, plugs into the model you actually use, carries a tool you added yourself, and has been tuned to your temperament.

## Step zero: get a green baseline

Start with the version used in this series, a fork of [he-yufeng/CoreCoder](https://github.com/he-yufeng/CoreCoder). Fork it to your own account on GitHub if you want to push your changes.

```bash
git clone https://github.com/im-xiaochen/CoreCoder.git
cd CoreCoder
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

That last line should show the collected tests passing. Don't skip this step. It confirms your environment is clean, so that when you later break something you can be sure it was you who broke it, not the environment being broken to begin with. The tests this project ships with aren't decoration, they're your safety net while refactoring, and all those pits the earlier pieces kept mentioning (orphaned tool messages, concurrency races, path traversal) are pinned down by tests. Every time you change something next, you should come back and run this line.

## Step one: plug in your own model

CoreCoder defaults to `gpt-5.5`, but you may not want to use it. Piece three covered this: switching models means switching environment variables. During development you can use a local Ollama instance to test the flow without remote API charges:

```bash
# install Ollama, pull a coder model
ollama pull qwen2.5-coder

export OPENAI_API_KEY=ollama
export OPENAI_BASE_URL=http://localhost:11434/v1
export CORECODER_MODEL=qwen2.5-coder

corecoder
```

A local model is a notch weaker than a flagship, but it's more than enough for verifying "did my change keep the flow working," and it spares you from burning API money every time you debug a single tool. Once the logic is all correct, switch the environment variables to DeepSeek or something else and see the real effect.

## Step two: add a genuinely useful tool

Create `corecoder/tools/text_stats.py`. The tool receives text directly, so it needs neither workspace state nor network access.

```python
from .base import Tool


class TextStatsTool(Tool):
    name = "text_stats"
    description = "Count characters and whitespace-separated words in text."
    stateless = True
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 10000}},
        "required": ["text"],
    }

    def execute(self, text: str) -> str:
        return f"characters={len(text)}, words={len(text.split())}"
```

In `corecoder/tools/__init__.py`, add `from .text_stats import TextStatsTool` alongside the imports, then add `TextStatsTool(),` to the list returned by `create_tools(state)`. Restart CoreCoder in code mode and ask it to run text_stats on "hello world". The tool reports 11 characters and two whitespace-separated words.

## Step three: tune its temperament

An agent's behavioral style isn't in some if-else, it's in the system prompt. Open `corecoder/prompt.py`, and that `# Rules` block is your agent's "code of conduct." The original has a few:

```
1. Read before edit.
3. Verify your work.
4. Be concise.
```

These rules directly shape how it works. Want it more cautious, add "ask for confirmation before destructive operations." Want it to write Chinese comments, add "all code comments in Chinese." Want it to always run tests after editing, write rule 3 harder. Change one line of prompt and its whole work habit shifts. This is the single highest-leverage tuning knob in agent engineering, bar none, taking effect faster than changing any code. It's worth setting aside dedicated time to polish this prompt as part of the product.

## Step four: not just a CLI, use it as a library

CoreCoder's top level exports `Agent`, `LLM`, `Config`, which means you're not limited to its interactive terminal; you can use it as a library and build an agent of an entirely different form.

To make a code-review agent with a narrow tool set, pass just the read, search, and file-listing tools when you create it.

```python
from corecoder import Agent, Config, LLM
from corecoder.tools import create_tools

config = Config.from_env()
llm = LLM(model=config.model, api_key=config.api_key, base_url=config.base_url)

# only read, search, and find-files; no write_file, no bash
reviewer = Agent(
    llm=llm,
    tools=[tool for tool in create_tools() if tool.name in {"read_file", "grep", "glob"}],
    max_rounds=15,
)

report = reviewer.chat(
    "Review corecoder/agent.py, find concurrency-related hazards, and list them."
)
print(report)
```

The explicit list limits which tools this agent can call. Model requests still send relevant tool output to the configured provider. Keep the workspace and inputs appropriate for that provider.

The same embedding interface can serve an internal code reviewer or a web backend.

## Step five: pair your change with a test

Add this test to `tests/test_tools.py`. It exercises the real registry as well as the tool output, so forgetting to register the tool fails the test.

```python
from corecoder.tools import create_tools


def test_text_stats_is_registered():
    tool = next(tool for tool in create_tools() if tool.name == "text_stats")
    args = {"text": "hello world"}
    assert tool.validate_arguments(args) is None
    assert tool.execute(**args) == "characters=11, words=2"
```

```sh
python -m pytest tests/test_tools.py::test_text_stats_is_registered -q
```

When you add a built-in tool, update `test_tool_count` to match the new registry total. Starting with nine, adding only `TextStatsTool` makes ten. Keeping `NowTool` from piece two as well makes eleven. The full suite also checks the source line totals documented in the README.

## Where you can go further

Try adding one capability you will actually use. Make the permission boundary explicit, test the ordinary entry point, and decide what evidence users should get back. [Piece eight](08-extensibility_EN.md) shows where MCP, hooks, modes, and project skills fit.

## A last word

If this series leaves you with only one thing, I hope it's this: a coding agent is less mysterious than it seems, it's just a stack of engineering decisions you can fully reach, piled up.

The main loop, tool registration, model adapter, context preparation, team scheduler, and CLI each give you a place to start reading.

Now you don't just understand it, you've forked it, plugged in your own model, added your own tool, and tuned its own temperament. It's yours.

Go give it some ability nobody else has.
