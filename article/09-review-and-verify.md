# 从改代码到交付：Git 审阅、项目检查与离线评测

[English](09-review-and-verify_EN.md) · [返回目录](00-index.md)

拿一个小改动来走完整流程。假设你请 CoreCoder 修了一个 Python 函数，接下来要确认改了哪些文件、项目测试是否通过，以及 CoreCoder 自己的审阅流程是否可靠。这三个问题分别对应工作台的 Git 面板、项目检查和源码仓库里的离线评测。

## 先看工作区里实际改了什么

在 Git 仓库内启动 Web 工作台，按[启动说明](../frontend/README.md)进入界面。打开 Git 面板，分别查看已暂存和未暂存的差异。前者比较 HEAD 与暂存区，后者比较暂存区与工作区。未跟踪文件也会出现在状态列表里。这样，即使文件是由终端命令改的，你仍能从 Git 看到工作区变化。

先核对文件，再选择要暂存或提交的路径。如果打开差异后又有人编辑文件，服务端会比较当前状态与审阅令牌。令牌过期时，刷新差异再确认。运行期间观察到的改动也可能来自编辑器或其他进程，归属要由审阅者核对。

读源码可以从 [Git 工作区](../corecoder/git_workspace.py)里的 `status()`、`diff()`、`stage()` 和 `commit()` 开始，再看 [Web 路由](../web_backend/server.py)怎样把状态、差异和确认后的操作交给它。

## 用项目自己的命令验证

例如，项目已安装 pytest，并且通常在仓库根目录运行测试。可以将下面的配置保存为 `.corecoder/checks.json`。已有配置时，将这个检查项加入现有 `checks` 数组，并为它选一个唯一的 ID。

```json
{
  "version": 1,
  "checks": [
    {"id": "test", "name": "Python tests", "command": "python -m pytest", "cwd": ".", "timeout": 120}
  ]
}
```

在 CLI 交互会话中用 `/checks` 查看可用检查，输入 `/check test` 后核对并确认命令。在 Web 工作台可以通过 Checks 面板查看命令、发起检查和查看历史。项目配置提供命令，执行前仍要确认。检查失败时，先看退出状态和原始输出，再决定是否修复和重跑。

[项目检查模块](../corecoder/project_checks.py)负责解析配置、运行授权后的命令和记录结果。CLI 的斜杠命令入口见 [CLI 实现](../corecoder/cli.py)。

## 改 CoreCoder 本身时，用离线夹具复核

如果你在改 CoreCoder 的 Git 集成，可以在已安装项目依赖的 **CoreCoder 源码检出目录**运行：

```bash
python -m corecoder.evaluation --task git_index --output ./evaluation-output
```

评测会在指定输出目录创建夹具仓库并保留报告。这个任务用离线数据检查所选改动和既有暂存内容如何处理。省略 `--task git_index` 可运行整套夹具，覆盖任务恢复、停止、项目检查等场景。评测使用脚本化模型和合成用量，衡量 CoreCoder 的行为。要验证实际项目修改，仍应运行该项目的检查命令。

入口和断言都在[离线评测模块](../corecoder/evaluation.py)。读完本章可以回到[会话与 CLI](06-session-and-cli.md)，继续追运行结果怎样保存和恢复。
