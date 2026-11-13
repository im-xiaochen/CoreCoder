# Fork CoreCoder，搭一个你自己的 coding agent

前六篇我们把 CoreCoder 拆开看了个遍。循环、工具、模型接口、上下文压缩、并发、子 agent、CLI，每个零件都摊在桌上看过了。这一篇我们把它们装回去，而且装成你自己的。

读懂和做出来之间，隔着一次动手。这篇就是带你跨过去。读完，你手上会有一个能跑、接着你常用的模型、带着一个你亲手加的工具、按你的脾气调教过的 coding agent。

## 第零步：拿到一个绿的基线

这套文章以当前分支为准，项目最初来自 [he-yufeng/CoreCoder](https://github.com/he-yufeng/CoreCoder)。如果要推送自己的改动，可以先在 GitHub 上 fork。

先确认起点是好的。

```bash
git clone https://github.com/im-xiaochen/CoreCoder.git
cd CoreCoder
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

最后那行应该看到本地收集到的测试通过。这一步别跳。它确认了你的环境是干净的，后面你改出问题时，才能确定是你改的，不是环境本来就坏。这个项目自带的测试不是摆设，它们是你重构时的安全网，前几篇反复提到的那些坑（孤儿 tool 消息、并发竞态、路径穿越）全都有测试钉着。你接下来每改一处，都该回来跑一遍这行。

## 第一步：接上你自己的模型

CoreCoder 默认 `gpt-5.5`，但你未必想用它。第三篇讲过，换模型就是换环境变量。开发期可以先用本地 Ollama 验证流程，省去远程 API 调用费用：

```bash
# 装个 Ollama，拉一个 coder 模型
ollama pull qwen2.5-coder

export OPENAI_API_KEY=ollama
export OPENAI_BASE_URL=http://localhost:11434/v1
export CORECODER_MODEL=qwen2.5-coder

corecoder
```

本地模型能力比旗舰差一截，但用来验证「我的改动有没有把流程跑通」绰绰有余，还省得你调试一个工具就烧一次 API 的钱。等逻辑都对了，再把环境变量换成 DeepSeek 或者别的，看真实效果。

## 第二步：加一个真正有用的工具

新建 `corecoder/tools/text_stats.py`。这个工具直接接收文本，不读取工作区或网络，也不需要保存会话状态。

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

在 `corecoder/tools/__init__.py` 的导入区加 `from .text_stats import TextStatsTool`，再把 `TextStatsTool(),` 加进 `create_tools(state)` 返回的列表。重启 CoreCoder，在编程模式请它用 text_stats 统计 “hello world”。结果应为 11 个字符和两个以空白分隔的单词。

## 第三步：调教它的脾气

agent 的行为风格，不在某段 if-else 里，在系统提示词里。打开 `corecoder/prompt.py`，那段 `# Rules` 就是你的 agent 的「行为准则」。原版里有这么几条：

```
1. Read before edit. 改文件前先读。
3. Verify your work. 改完跑相关测试确认。
4. Be concise. 多给代码，少废话。
```

这些规则直接塑造它怎么干活。想让它更谨慎，加一条「破坏性操作前先征求确认」；想让它写中文注释，加一条「代码注释一律用中文」；想让它每次改完必跑测试，把第 3 条写得更硬。改一行提示词，它的整个工作习惯就变了。这是 agent 工程里性价比最高的调参旋钮，没有之一，比你去改任何代码都见效快。值得你专门留出时间，把这段提示词当成产品的一部分来打磨。

## 第四步：不止做个 CLI，把它当库用

CoreCoder 顶层导出了 `Agent`、`LLM`、`Config`，意味着你不必只能用它那个交互式终端，你可以把它当库，搭出一个完全不同形态的 agent。

如果只想让审查代理读文件、搜索内容和定位路径，创建实例时就只传入这三种工具。

```python
from corecoder import Agent, Config, LLM
from corecoder.tools import create_tools

config = Config.from_env()
llm = LLM(model=config.model, api_key=config.api_key, base_url=config.base_url)

# 只给读、搜、找文件这三样，没有 write_file，没有 bash
reviewer = Agent(
    llm=llm,
    tools=[tool for tool in create_tools() if tool.name in {"read_file", "grep", "glob"}],
    max_rounds=15,
)

report = reviewer.chat(
    "审查 corecoder/agent.py，找出并发相关的隐患，列成一份清单。"
)
print(report)
```

工具列表明确限制这个实例可以调用的能力。模型请求仍会把相关工具输出发给配置的供应商，使用前应选好工作区和输入范围。

同一个嵌入接口也可以用来搭内部代码审查器，或者接到 Web 后端。

## 第五步：给你的改动配上测试

在 `tests/test_tools.py` 加下面的测试。它同时验证实际注册入口和工具结果，忘了登记也会被测试抓到。

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

新增内建工具后，也要更新 `test_tool_count` 中的总数。原来有九个，只加 `TextStatsTool` 后是十个；如果也保留第二篇的 `NowTool`，就是十一个。全量测试还会核对 README 记录的源码行数。

## 你可以往哪走得更远

挑一个自己确实会用的能力，加好权限边界，验证正常入口，再决定要给用户留什么证据。[第八篇](08-extensibility.md) 会继续讲 MCP、钩子、模式和项目技能应该接在哪里。

## 写在最后

如果这个系列只让你记住一件事，我希望是这个：编码 agent 没有想象中那么神秘，它就是一摞你完全够得着的工程决定堆起来的。

主循环、工具注册、模型适配、上下文准备、团队调度和 CLI，各有清楚的阅读入口。

现在你不光看懂了，你还 fork 了它，接了自己的模型，加了自己的工具，调了自己的脾气。它是你的了。

去给它加点别人没有的能力吧。
