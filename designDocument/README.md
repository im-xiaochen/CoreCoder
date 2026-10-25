# CoreCoder 审查问题解决方案

依据：[审查报告（2026-10-03）](../review/审查报告_2026-10-03.md)。源码基线：`c2d841719019a96346c42173ed96dbe71c5cdeda`，版本 0.7.0。方案编写日期：2026-10-07。

本目录包含报告第 3 节 **R01–R11 共 11 项已复现问题**的实施设计、关键行为 spec 和离线功能验收代码。R01–R06 于 2026-10-08 交付，见 [历史记录](IMPLEMENTATION_R01_R06.md)；2026-10-11 已完成剩余 R07–R11，并通过主代理代码审查及全量验收：441 passed / 1 条件 skipped。见 [最新实施与验收记录](IMPLEMENTATION_R07_R11.md)。报告第 4 节的产品增强建议列入 [实施计划](IMPLEMENTATION_PLAN.md) 的后续工作。

## 逐项索引

| 问题 | 优先级 | 独立设计方案 | 关键 spec | 功能测试代码 |
|---|---|---|---|---|
| R01 默认工具实例共享 | P1 | [agent 状态隔离](R01_agent_state_isolation.md) | [R01 spec](specs/R01.spec.md) | [test_r01](tests/test_r01_agent_state_isolation.py) |
| R02 shell 与文件工具目录不一致 | P1 | [统一工作目录](R02_workspace_paths.md) | [R02 spec](specs/R02.spec.md) | [test_r02](tests/test_r02_workspace_paths.py) |
| R03 undo 覆盖外部修改 | P1 | [撤销冲突保护](R03_undo_conflicts.md) | [R03 spec](specs/R03.spec.md) | [test_r03](tests/test_r03_undo_conflicts.py) |
| R04 undo 失败丢快照、退出 REPL | P2 | [撤销事务和重试](R04_undo_transaction.md) | [R04 spec](specs/R04.spec.md) | [test_r04](tests/test_r04_undo_transaction.py) |
| R05 压缩不能满足预算 | P1 | [完整请求预算](R05_context_budget.md) | [R05 spec](specs/R05.spec.md) | [test_r05](tests/test_r05_context_budget.py) |
| R06 摘要遗漏参数和后段信息 | P2 | [结构化摘要证据](R06_summary_evidence.md) | [R06 spec](specs/R06.spec.md) | [test_r06](tests/test_r06_summary_evidence.py) |
| R07 非法参数破坏工具消息链 | P1 | [校验和调用收尾](R07_tool_validation.md) | [R07 spec](specs/R07.spec.md) | [test_r07](tests/test_r07_tool_validation.py) |
| R08 切模型重算历史费用 | P2 | [请求级费用账本](R08_cost_ledger.md) | [R08 spec](specs/R08.spec.md) | [test_r08](tests/test_r08_cost_ledger.py) |
| R09 one-shot 隐藏未完成状态 | P2 | [结束状态和退出码](R09_cli_result.md) | [R09 spec](specs/R09.spec.md) | [test_r09](tests/test_r09_cli_result.py) |
| R10 JSON 结构错误导致崩溃 | P2 | [配置和存档校验](R10_config_session_validation.md) | [R10 spec](specs/R10.spec.md) | [test_r10](tests/test_r10_config_session_validation.py) |
| R11 MCP 发现遗漏分页 | P2 | [MCP 完整发现](R11_mcp_pagination.md) | [R11 spec](specs/R11.spec.md) | [test_r11](tests/test_r11_mcp_pagination.py) |

公共接口集中定义在 [公共契约](CONTRACTS.md)。设计文档保留编写时的“新增”“建议接口”等措辞，具体实现状态以本轮验收记录为准。spec 编号用于串联需求与测试；各 spec 的“后续验证”列出自动化尚未覆盖的边界。

## 运行功能验收

依赖沿用项目 `dev` 环境：Python >= 3.10、pytest 和项目运行依赖。无需真实模型、API key、网络或第三方 MCP 服务。R02 的实际 shell 用例在 Windows 需要项目已有的 Git Bash；R11 使用 `sys.executable` 启动本地 stdio 假服务端。

从 **CoreCoder 项目根目录**执行（显式指定测试目录，避免收集项目原有测试）：

```powershell
python -m pytest -c designDocument/pytest.ini designDocument/tests --collect-only -q
python -m pytest -c designDocument/pytest.ini designDocument/tests -q --basetemp=designDocument/validation/tmp
python -m ruff check designDocument
```

只验收某个问题，例如 R03：

```powershell
python -m pytest -c designDocument/pytest.ini designDocument/tests/test_r03_undo_conflicts.py -q
```

本机已有隔离环境，可将命令中的 `python` 替换为 `E:\github项目\.corecoder-review-py312\Scripts\python.exe`。测试不会安装依赖。

原始 11 个模块作为修复前 RED 验收集交付；当前 R01–R11 的 104 项设计验收全部转绿。执行 `python -m pytest -c designDocument/pytest.ini tests web_backend/tests designDocument/tests -q` 可覆盖原有、Web 和全部设计及扩展回归。测试没有使用 `xfail` 或无条件跳过；符号链接用例在 Windows 不允许创建测试链接时报告条件跳过。

验证结果、覆盖范围和局限见 [验证记录](validation/VALIDATION.md)。JUnit XML 和日志保留本次真实执行结果，不能把测试可运行等同于缺陷已修复。

## 阅读顺序

先看本索引及公共契约，再看单项设计与相应 spec。开发建议按 `R01 → R02 → R03 → R04 → R07 → R10 → R05 → R06 → R08 → R09 → R11` 推进；每项设计列出改动文件、实现步骤、兼容策略和完成标准。
