# 实施顺序与交付闸门

## 范围与依赖

本次设计覆盖审查报告 R01–R11。产品增强不作为新增缺陷编号。建议每项一个修复提交，但 R01/R02 和 R03/R04 分别共享一次接口评审，避免同一模块形成两套实现。

| 阶段 | 顺序 | 前置依赖 | 完成闸门 |
|---|---|---|---|
| A：状态和文件正确性 | R01 → R02 | 无 | 工具/授权隔离，统一目录，依赖调用串行 |
| A：可恢复修改 | R03 → R04 | state/store 归属 | 冲突不覆盖，恢复失败/提交失败可重试，REPL 存活 |
| A：输入边界 | R07 → R10 | 统一结果/validator 约定 | 非法参数不进入副作用，配置坏项隔离、存档原子写 |
| B：长任务可靠性 | R05 → R06 | 动态提示/工具 schemas、outcome | 每次发送过预算闸门，摘要保留工具证据及最新事实 |
| B：计量和脚本 | R08 → R09 | request context、outcome | 历史费用不变，状态/退出码/JSON 可消费 |
| C：扩展完整性 | R11 | R10 的配置隔离 | 分页完整，发现有限时长、有限页数，失败回收进程 |

每项开发时先跑对应 test_rXX 确认 RED，再实现，最终全量运行本目录验收；不能以缩小断言、删除失败用例、加入 xfail 代替修复。spec 修改需同步设计和测试，并写明行为取舍。

## 原有测试的兼容性处理

1. 正常路径、授权拒绝、计划模式、工具错误和中断补回复继续保留。
2. `ALL_TOOLS` helper 改为每次工厂创建，仍可兼容导出但不作为新 Agent 的默认共享列表。
3. R03 对外部删除采取冲突保护，原“无条件重建父目录”测试应改为默认拒绝、显式 force 后重建。
4. 原直接 `record` 后人工写入测试要补 finalize after 或显式 legacy force，不能让未知 after 记录绕过保护。
5. R08 不再允许直接改累计 token 变量推定历史费用，使用注入 usage 的请求测试；新增未知费用断言。
6. 原 README 的代码行数断言在运行源码增加时需要按真实结果更新；这次只新增设计目录，尚无需改品牌统计。

完成后的检查命令（项目根目录）：

```powershell
python designDocument/verify_delivery.py
python -m pytest -c designDocument/pytest.ini designDocument/tests -q
python -m pytest tests/ -q
python -m ruff check corecoder tests designDocument
```

不要把审查用 `reproduce_findings.py` 的 CONFIRMED 作为修复成功标准；修复后该探针可能不再复现，这是预期变化。

## 报告第 4 节的后续功能

| 功能建议 | 与当前修复的关系 | 后续独立交付范围 |
|---|---|---|
| 工作区与会话 state | R01/R02 先落最小结构 | 完整 workspace 生命周期与跨会话展示 |
| 权限查看和撤销 | R01 保证许可归属 | /permissions、工作区许可撤销和 diff 授权预览 |
| 真实变更审查 | R03 提供冲突预览 | /diff 实际 patch、新文件/净变更与批量撤销预览 |
| 完整会话恢复 | R10 提供版本化原子存档 | cwd/Todo/任务状态/模型标识/账本/checkpoint 关联恢复 |
| token/费用/时间预算 | R05 请求上下文、R08 计费基础 | 总 token、墙钟时间和硬费用预算；不与上下文窗口混淆 |
| 可取消工具执行 | R07 提供 cancelled/unknown | 进程组或 Windows Job Object、hooks/MCP 取消和子进程回收 |
| 大仓搜索与分页读取 | R05 归档引用与读取 | rg、gitignore、二进制过滤、扫描限额、输出采集上限 |
| 自动加载项目说明 | 状态中有 workspace_root | 分层 AGENTS/CORECODER、优先级、长度上限与数据/指令区分 |
| 结构化执行记录 | R06/R07 提供核心 outcome | 稳定事件格式、时序、脱敏与可检索 trace |
| 启动诊断 | R10/R11 已有局部诊断 | /doctor 依赖、Git Bash、端点和 MCP；网络探测需显式触发 |

## 未覆盖的运行边界

离线验收无法证明真实 LLM 摘要质量、各 provider 实际 token 计量、真实第三方 MCP 兼容性、断电/跨进程竞争与全平台行为。各 spec 已列出后续测试；生产修复完成时需按使用场景补充，不能以本次 104 个用例替代这些验证。
