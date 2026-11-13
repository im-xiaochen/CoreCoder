# 用 CoreCoder 读懂 Claude Code，再做一个你自己的

这是一份写给工程师的系列导读。它想干两件事。

第一，借 CoreCoder 这个用 Python 写成的小型开源项目，把 Claude Code 这类生产级编码 agent 的内部结构讲清楚。第二，带你把 CoreCoder fork 下来，改出一个属于你自己的 coding agent。

## 为什么要绕一圈

因为 Claude Code 本体太大了。它的代码量在几十万行的量级，光一个负责跑 shell 命令的 BashTool 就上千行，真要逐行读完，多数人会在第三个文件就关掉编辑器。可 agent 的核心其实没那么复杂，复杂的是工程化之后那些为真实世界兜底的边角：模型半路被打断怎么办、上下文塞满了怎么办、十个工具调用同时回来怎么办、provider 偶尔抽风返回 500 又怎么办。这些东西摊在几十万行里，你很难一眼看到骨架。

CoreCoder 把这套骨架留在能通读的规模里。最新代码量见 [README](../README_CN.md)。CLI 和 Web 工作台复用同一套内核，能检索仓库、经授权修改文件、派出只读团队，也能报出一次运行的用量。读它，你可以追完一条真实请求：从用户输入到模型，从模型到工具，再回到用户。

每一篇都能回到源码核对。为了把主线讲清楚，有些示意片段会省掉记账或错误分支。要逐行读时，顺着源码链接往下走就行。

## 这个系列假设你是谁

我假设你写过代码，调过 API，大概知道 LLM 的 function calling 是怎么回事，但没真正拆开过一个 agent 的主循环。你可能用过 Claude Code 或者 Cursor，惊叹过它怎么能自己读文件、改代码、跑测试，然后好奇：这背后到底是魔法还是工程。

答案是工程。而且是那种读完会让人觉得「就这？我也能写」的工程。

## 九篇怎么读

前六篇拆内核和运行入口。第七篇带你动手加一个能跑的工具，第八篇看外部工具、钩子和会话模式分别接在哪，第九篇把一次修改从 Git 审阅追到检查和离线评测。

1. [一个 agent 的本体，是一个 while 循环](01-the-loop.md)。从 `Agent.chat_result()` 看模型请求、工具回填、轮次上限和中断。
2. [工具系统：让模型安全地动手](02-tools.md)。认识包括 `repo_map` 在内的九个内建工具，再往会话工具表里加一个自己的工具。
3. [接入任意大模型，顺便把钱算清楚](03-llm-and-cost.md)。追一遍流式响应、参数兼容、同源推理回传、用量账本和运行预算。
4. [用有限的窗口扛住一个长任务](04-context.md)。看 CoreCoder 怎么估算实际发送的消息、归档太长的证据，并保住工具调用和回复的配对。
5. [并行执行与子 agent](05-parallel-and-subagents.md)。从并行读工具讲到会话状态隔离，再看只读团队怎样按依赖调度。
6. [把它跑成一个真正的命令行工具](06-session-and-cli.md)。看会话保存、斜杠命令和 JSON 结果，再看 Web 工作台怎样断线续接。
7. [Fork CoreCoder，搭一个你自己的 coding agent](07-build-your-own.md)。加一个自定义工具，跑通测试，再把 CoreCoder 当库用。
8. [MCP、钩子与计划模式](08-extensibility.md)。看 stdio 客户端怎样发现工具、钩子在哪一层运行，以及 Web 工作台怎样区分编程、计划、聊天。

9. [从改代码到交付：Git 审阅、项目检查与离线评测](09-review-and-verify.md)。从工作台查看改动、运行项目检查，再用离线夹具验证 CoreCoder 自身。

不想按顺序也行。想搞懂「它凭什么敢自动跑命令」直接跳第二篇；想接自己的模型直接跳第三篇；只想赶紧 fork 出个能用的，第七篇是自洽的。

## 五分钟先把它跑起来

读之前先让它在你机器上活一次，后面所有代码你才有体感。

```bash
git clone https://github.com/im-xiaochen/CoreCoder
cd CoreCoder
pip install -e .
```

手头没有 key，可以先跑 `corecoder --demo` 看循环怎么转。要调用真实模型，就从下面选一组，用对应端点的 key 和模型。换例子前，打开没有旧配置的终端，或清除之前设置的 `OPENAI_BASE_URL` 和 `CORECODER_MODEL`。

```bash
# OpenAI
export OPENAI_API_KEY=sk-...

# 或者 DeepSeek
export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com CORECODER_MODEL=deepseek-chat

# 或者已经安装并启动的本地 Ollama
export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 CORECODER_MODEL=qwen2.5-coder
```

跑起来：

```bash
corecoder
```

进去之后随便给个任务，比如「读一下 corecoder/agent.py，告诉我这个循环最多转多少轮」。如果模型选择调用 `read_file`，CLI 会先显示工具调用，再流式显示答案。那一刻它做的事，和 Claude Code 做的事，本质上是同一件。接下来六篇，就是把这「同一件事」拆开看。

那我们从那个循环开始。

---

作者：[何宇峰](https://github.com/he-yufeng)。早前写过一篇 [Claude Code 源码分析](claude-code-source-analysis.md)，偏完整拆解；这个系列换了个角度，更想让你动手把它复刻出来。
