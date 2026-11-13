# 一个 agent 的本体，是一个 while 循环

如果只能用一句话解释编码 agent，我会这么说：它是一个循环，反复地问模型「下一步干什么」，照着模型说的去动手，把结果再讲给模型听，直到模型说「不用动手了，我有答案了」。

听起来朴素到有点失望。但这就是真相。Claude Code 把这件事做到了几十万行，可那个最中心的东西，公开拆解里叫 `query.ts`，它的主体是一个一千七百行上下的 `while` 循环。CoreCoder 把同一个循环写在 `corecoder/agent.py` 里。这个形状你只要顺着一个文件就能读下来。

这一篇，我们就把这个循环逐段读透。

## 先看骨架

从 [`Agent.chat_result()`](../corecoder/agent.py) 进去。它先建立本次运行的预算和用量范围，然后交给 `_chat_result_unscoped()`。下面是流程示意，省去了记账和错误分支：

```text
追加用户消息
在允许的轮次内循环：
    准备下一次请求，检查上下文预算
    调用模型
    如果没有工具调用，保存回复，返回 completed
    校验调用批次，保存 assistant 消息
    执行工具，逐条补齐对应的 tool 回复
轮次用尽，返回 max_rounds
```

每轮第一步在 `_run_turn()`。它把系统提示、工具 schema、历史和输出预留交给 `ContextManager.prepare_request()`。经过[第四篇](04-context.md)讲的处理仍放不下时，模型请求不会发出。放得下就用准备好的历史继续。

模型不再要工具，循环就收尾。这只说明模型不再请求下一步。测试有没有通过、文件到底改没改，要回到工具结果里看。

## 为什么是 for，不是 while(true)

CoreCoder 写的是 `for _ in range(self.max_rounds)`，默认 50 轮。模型有时会卡在「读一遍，再读一遍」里。轮次用尽时，结构化结果标记为 `max_rounds`，并保留已经生成的文本。

轮次上限约束的是主循环走几圈。[运行预算](03-llm-and-cost.md)还可以在 OpenAI 兼容路径限制实际请求数、token 或预估费用。重试、摘要、子任务可能没给父循环增加一轮，却同样会消耗共享预算。

要读取终态、工具错误和用量，用 `chat_result()`。旧接口 `chat()` 返回文本，遇到失败或中断会向调用者抛出异常。

## 工具结果长什么样

OpenAI 兼容协议里，带 `tool_calls` 的 assistant 消息，每个调用都要有一个对应的 `tool` 回复。循环先检查这一批调用的结构，再按调用 ID 写回。下面这一行就是循环里的追加操作：

```python
self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": outcome.content})
```

模型看到的是回复内容。调用者还能读到结构化的工具结果，区分成功、拒绝、失败、取消和结果未知。第四篇会再说到这个配对为什么不能在压缩时拆开。

## 一个工具的执行，怎么区分「参数错」和「工具自己炸了」

现在的 `_run_tool_call()` 先检查模型参数有没有解析错误、是否符合工具的 JSON Schema。如果 Python 能提供签名，它还会试一次 `inspect.signature(tool.execute).bind(**args)`。这些检查都发生在回调、钩子、授权和执行之前。

为什么不直接调工具，等它抛出 `TypeError` 再说？因为模型参数没填对，和工具内部自己抛了 `TypeError`，该给两条不同的反馈。前者告诉模型怎么改调用，后者告诉它执行出了问题。测试 [`test_exec_tool_distinguishes_bad_args_from_internal_error`](../tests/test_core.py) 就盯住这条区分。

## 每个 agent 只认自己那套工具

工具从实例自己的 `self._tool_by_name` 里找。[子 agent](05-parallel-and-subagents.md)可以拿到裁过的工具集和独立的会话状态。一个工具没分给它，即使模型知道名字也调不到。测试 [`test_agent_tool_scope_is_per_instance`](../tests/test_core.py) 用只有 `read_file` 的 agent 验证了这条。聊天模式再收紧一步，既不提供工具 schema，也会拒绝服务端仍然发来的调用。

## 模型被打断的那一瞬间

Ctrl+C 可能打在模型已经返回一批调用、工具还没全部答复的时候。历史里这时有带 `tool_calls` 的 assistant 消息，却缺了部分 tool 回复。直接把这段历史发回模型，就会破坏调用 ID 的配对。

`_answer_pending_tool_calls()` 会保留已经写入的回复，能从本轮缓存里恢复的就恢复，只给剩下的补一条。未开始的调用可以标取消。已经开始、无法确认执行结果的调用保留「结果未知」，不会因为历史修好了就宣称命令成功或已经撤销。

`chat_result()` 会返回 `cancelled`。旧式单任务子 agent 被 Ctrl+C 打断时，中断也会传回父 agent，父循环不会再向模型发下一轮请求。测试 [`test_interrupt_backfills_missing_tool_replies`](../tests/test_core.py) 验证已有回复不会被重复填。

## 和 Claude Code 的对照

对照 Claude Code 的拆解，最熟悉的仍是拼消息、带工具问模型、执行、回填、再问这一圈。CoreCoder 把几处保护拆在旁边，方便顺着读：[请求重试](../corecoder/llm.py)、[上下文准备](../corecoder/context.py)、[运行预算](../corecoder/budget.py)和[工具执行](../corecoder/agent.py)。一次运行提前停下时，先看结构化结果，就知道从哪一块开始追。

## 收个尾

读这段循环可以读两遍。第一遍跟着模型问答和工具执行走，第二遍专门看每个可能停下来的位置。两条路都得保住工具回复配对，轮次上限和请求预算也各管各的一笔账。下一篇看让循环动起来的[工具系统](02-tools.md)。
