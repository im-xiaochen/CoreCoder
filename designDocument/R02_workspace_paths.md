# R02：统一工作目录和相对路径语义

优先级 P1。关联 [spec](specs/R02.spec.md)、[功能测试](tests/test_r02_workspace_paths.py)、[公共契约 §1](CONTRACTS.md)。依赖 R01 的工具/state 归属。

## 问题与目标

`tools/bash.py` 使用线程局部 cwd，read/write/edit/glob/grep 使用进程 cwd。执行 `cd sub` 后读写同名文件可能落在根目录。线程池中同时调度 cd 和写入，还会在目录更新之前执行后者。

目标：所有工具从同一个会话 cwd 解析路径；子任务起始目录继承且后续独立；目录变更与依赖操作按顺序执行；进程 cwd 不变。

## 具体设计

1. `SessionState.resolve_path(value)` 先 expanduser；绝对路径直接规范化，相对路径与 `state.cwd` 拼接再 resolve。初始化 cwd/root 均为绝对 Path。目录是否允许越出 workspace 是独立授权策略，首版不偷偷引入与旧接口冲突的路径限制。
2. 文件工具只使用该 resolver；glob 的默认 path、grep 的搜索根及 checkpoint 记录也走同一入口。结果使用解析后的绝对路径，降低同名文件歧义。系统提示动态显示 `state.cwd`，不能缓存启动时的目录。
3. BashTool 的 subprocess cwd 来自 state。首版明确保留可跟踪的简单 cd、引号、`&&`/`;` 顺序语法，并复用已有解析器；失败 cd 不改变 state。复杂 shell（函数、变量间接路径、条件分支）要么用可信 shell 包装器返回实际最终 cwd，要么明确提示“未跟踪，请显式指定 cwd”，不能根据正则猜测并悄悄改状态。
4. batch 执行只并行纯只读调用；bash、write、edit、Todo 变更、子 Agent 和副作用未知的 MCP 都作为串行屏障。`cd → write`、`write → test` 严格按调用顺序。屏障前的只读任务全部完成后，再执行屏障；后续读调用读取更新后的目录。
5. state 的 cwd 不依赖工作线程局部变量。子任务复制父 cwd；子 cd 只更新子 state；父 state 无需在 finally 猜测并恢复其他线程的值。

## 文件改动落点

`state.py` 实现 resolver/fork；`tools/bash.py` 改目录来源；read/write/edit/glob/grep 替换 `Path(...).resolve()`；`agent.py` 改批调度；`prompt.py` 接受当前 cwd；hooks 若需目录则明确传 `cwd=state.cwd`，避免用户命令隐式取错目录。

## 实施与兼容

先新增 state 注入，工具独立构造时保留基于构造 cwd 的默认 state。其次统一六类文件工具，再接 BashTool，最后落串行屏障。旧 `get_tracked_cwd/set_tracked_cwd` 留作兼容适配，不参与新 Agent 路径决策；旧测试不应继续要求会话目录等于任意工作线程的局部变量。

Windows 路径处理留在 `shell.py` 的 Git Bash 路由层；不用全局 `os.chdir()` 修复。shell cwd 的 native Path 和命令字符串的 POSIX 转义分别处理，中文、空格及盘符不能在不明语境中整体替换。

## 验收与风险

测试实际运行 shell cd 后检查 read/write/edit/glob/grep 与提示、同 batch 顺序、父子目录及失败 cd/绝对路径。Windows 真实 Git Bash 是本机边界；Linux/macOS、复杂 shell cd 和并行只读性能另列后续验证。串行副作用调用会降低吞吐，这是首版确保文件目标可预测的取舍。
