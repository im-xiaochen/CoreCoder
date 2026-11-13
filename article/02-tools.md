# 工具系统：让模型安全地动手

上一篇那个循环里，有一步被我一带而过：执行工具。这一篇把它展开。

模型本身只会做一件事，根据上文吐出下文。它不能读你的文件，不能跑你的测试，不能往磁盘写一个字节。让它从「会说」变成「会做」的，是工具。工具是 agent 真正接触世界的那只手。所以一个 agent 强不强，很大程度上取决于它的工具设计得好不好：接口是否清晰、错误反馈是否到位、危险操作是否拦得住。

CoreCoder 给了模型九个内建工具：`bash`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、`repo_map`、`todo_write`、`agent`。这一篇我们先看它们共同的骨架，再细抠其中两个最值得说的，最后我带你写一个自己的。

## 一个工具长什么样

工具继承自 [`Tool`](../corecoder/tools/base.py)，提供名称、说明、JSON Schema 和执行方法。模型看到的是 `schema()` 包装过的定义，调用时用名称和参数，拿回字符串结果。内建对象默认拒绝 schema 没声明的字段。

CLI 调用 [`create_tools(state)`](../corecoder/tools/__init__.py) 给每个会话创建自己的工具实例。这个工厂就是当前的注册入口。

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

`ALL_TOOLS` 是旧接口的兼容导出，扩展 CLI 时应修改工厂。`grep`、`glob` 和 `repo_map` 按需查询工作区，可用 `offset` 和 `limit` 翻页。检索尊重忽略规则，并用扫描预算限制工作量，`incomplete` 表示结果可能不全。

## edit_file：一个看着平平无奇的关键创新

九个工具里，如果只能挑一个讲，我会挑 `edit_file`。因为「让模型修改一个已有文件」这个看似简单的需求，背后死过好几条路。

第一条死路是让模型按行号打补丁，比如「把第 42 行换成这样」。问题是模型对行号的感知极不可靠，它脑子里的第 42 行和文件里真实的第 42 行经常对不上，差一行就改错地方。而且只要文件在前面被动过一次，后面所有行号全部漂移。

第二条死路是让模型把整个文件重写一遍发回来。小文件还行，文件一大，又慢又贵，而且模型在「誊抄」那些它不该动的部分时，会时不时手滑改掉一两个字符，你还很难发现。

第三条死路是让模型生成标准的 diff/patch 格式。听上去优雅，实际上模型生成那种带 `@@ -42,7 +42,8 @@` 行号头的统一 diff，错误率高得让人头疼，那套上下文行数和偏移量它算不准。

Claude Code 的解法，也是 CoreCoder 照搬的解法，是第四条路：搜索替换，外加一个唯一性约束。模型给出一段「要找的原文」和「替换成的新文」，工具在文件里找这段原文，要求它恰好出现一次，然后替换。看 `tools/edit.py` 的核心：

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

精妙之处全在那个「恰好一次」。

如果原文一次都没找到，说明模型记错了内容，工具不会瞎猜，而是把文件开头贴回去让它重新对照。如果原文出现了不止一次，工具拒绝执行，因为它没法确定模型想改的是哪一处，于是回一句「这段文字出现了 N 次，多带几行上下文让它唯一」。这句话不只是报错，它是在教模型怎么把请求修对：下一轮模型会自觉地把 `old_string` 扩到包含足够的上下文，直到它在全文里唯一。

这个约束把「改文件」这件事从一个模糊问题变成了一个确定问题。模型不需要懂行号，不需要会算偏移，它只需要原样引用一段它想改的代码，工具来保证这段引用没有歧义。Claude Code 的系统提示词里专门叮嘱模型「old_string 必须在文件里唯一」，CoreCoder 的提示词里也有同一条规则。这是一个用约束换可靠性的典范设计。

替换完，工具还会生成一段统一 diff 返回给模型和用户：

```python
diff = _unified_diff(content, new_content, str(p))
return f"Edited {p}\n{diff}"
```

注意方向反过来了：让模型生成 diff 不可靠，但让工具生成 diff 给模型看，完全可靠。模型看着这段 diff 就能确认自己改对了没有。生成放在工具侧，消费放在模型侧，各司其职。

还有个容易被忽略的边角：编辑前先确认文件是 UTF-8 文本。

```python
try:
    content = p.read_text(encoding="utf-8")
except UnicodeDecodeError:
    return f"Error: {file_path} is not a UTF-8 text file (edit_file only edits text files)"
```

要是没这道判断，模型一不小心对一个二进制文件做 `edit_file`，得到的会是一大坨 Python 解码异常栈，既污染上下文又毫无帮助。有了它，模型拿到的是一句它读得懂的话。给模型的每一条错误信息，都该是「人话」，这是贯穿 CoreCoder 所有工具的一条隐性准则。

实际编辑持有文件写入锁，先写临时文件再替换目标，并完成撤销快照。

## bash：把危险操作拦在门外，但别假装这是沙箱

[`BashTool`](../corecoder/tools/bash.py) 在启动命令前会做危险命令检测，普通调用还要经过代理的权限流程。检测属于防误操作措施。运行不可信任务时，仍需要独立的沙箱或隔离环境。

实际执行交给 [`run_command()`](../corecoder/command_runner.py)，它处理超时、取消和输出收集。长输出保留头尾，退出码与执行状态供代理和界面判断。工具使用会话的工作目录。能明确追踪的 `cd` 成功后会更新 `SessionState.cwd`，复杂的目录跳转则会提示当前会话目录没有更新。

## 两阶段：先验形状，再验安全

[`Agent._run_tool_call()`](../corecoder/agent.py) 先检查流式参数解析、工具是否存在、参数的 JSON Schema 和 Python 签名。输入合法之后，才进入界面提示、钩子、权限和执行。工具自己抛异常时，也可以区分「没开始」与「开始后失败」。模型收到与调用 ID 配对的文本结果，CLI 和 Web 则能用结构化执行状态。

## 门之外：plan 模式、hooks，和住在别的进程里的工具

计划模式拦截写入和命令，即使已有 `--yes` 也不能绕过。它允许经过现有权限检查的内建只读团队调用；聊天模式不提供工具。

PreToolUse 钩子可以否决单次调用，PostToolUse 钩子观察执行结果。MCP 工具以 `mcp__server__tool` 注册，接入同一套调用流程。详见 [第八篇](08-extensibility.md)。

## 动手：写你自己的第一个工具

新建 `corecoder/tools/now.py`。查时间没有跨调用的可变状态，可以明确声明 `stateless = True`。

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

在 `corecoder/tools/__init__.py` 的导入区加 `from .now import NowTool`，再把 `NowTool(),` 加进 `create_tools(state)` 返回的列表。重启 CLI，在编程模式询问「用 now 工具告诉我现在的时间」，正常的授权流程仍然生效。[第七篇](07-build-your-own.md) 用同一入口加一个可独立测试的文本统计工具。

## 这一篇带走什么

- 用 schema 描述输入，让参数校验与工具内部失败可以区分。
- 从 `create_tools(state)` 创建会话工具，把目录等状态绑定到当前会话。
- 用唯一匹配编辑文件。权限检查、钩子和命令运行保护各自负责一段流程。

下一篇看 [模型接口和费用](03-llm-and-cost.md)。
