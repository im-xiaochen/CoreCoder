# R01–R06 实施与验收记录

日期：2026-10-08。范围：按照 designDocument 实现 R01–R06；R07–R11 保留为后续任务。本轮沿用当前工作区，保留原有前端、Web 后端和设计资料，没有创建 Git 提交。

由三个 GPT-5.6-luna 子代理分别实现状态/路径、撤销事务、上下文/摘要；主代理负责集成、审查、补充功能验收及最终回归。原始修复前结果保留在 [验证基线](validation/VALIDATION.md)。

## 实现结果

| 设计 | 实现与验收重点 |
|---|---|
| R01 | 每个 Agent 独立 SessionState、默认工具工厂、Todo、修改集合与检查点；子代理继承直接父代理的模型、权限、hooks 和实际工具能力，自定义工具必须声明无状态或支持克隆。 |
| R02 | 文件、搜索、shell、hooks 和动态系统提示使用 session cwd，进程 cwd 不变；纯读批次可并发，其他批次按调用顺序执行；复杂 shell 的 cwd 变化无法可靠追踪时明确报告。 |
| R03 | 按工作区/会话保存 before 原始字节及实际 after hash；默认撤销保护外部改动、删除和类型变化；legacy 必须 force，CLI force 显示目标并要求确认；Web 保留精确字节冲突保护。 |
| R04 | 恢复前持久化 journal，临时文件 fsync 后原子替换，成功恢复后提交栈；提交失败补偿，补偿失败阻止后续写入；重载按哈希判断恢复状态，保护恢复期间的外部修改；slash 错误不退出 REPL。 |
| R05 | 每轮主请求及摘要请求检查 system、schemas、history、协议开销、余量和实际输出预留；仅预算通过才提交；长工具输出与被压缩历史保存 UTF-8 归档、hash、绝对路径，支持字符分页；归档失败保留原文。 |
| R06 | 通过 call_id 关联 ExecutionRecord/TaskMemory，保留参数、路径、执行 cwd、头尾事实、约束及真实状态；缺回复为 unknown，拒绝/取消/失败分开；完整调用组分块摘要，失败时确定性提取；派生记录脱敏，canonical 消息和归档保留证据。 |

CLI 入口使用会话工具工厂，保存与恢复共用会话 ID，使同一工作区内恢复的会话能重新打开自身检查点。Web 适配会话工具、检查点及 compact 预算闸门；演示 hooks 同步支持 cwd 参数。

## 主代理审查修复

除原设计测试外，新增 [扩展验收](tests/test_acceptance_r01_r06_extended.py)，覆盖自定义工具权限不被替换、跨 Agent 工具所有权、子代理权限继承、写后读顺序、条件 cd、hook cwd、空文件与不存在文件、legacy/force、会话检查点隔离、CLI 连续启动及保存/恢复撤销、journal 各阶段失败、双重补偿失败、重启冲突、归档故障与精确 CRLF 字符分页回读、每轮预算、纯对话归档、真实执行状态、批次中断以及结构化脱敏。

审查中发现并修复了 journal 阶段更新失败后的无保护写入、Windows fsync 句柄错误、Web 隐式换行规范化撤销、条件 shell 误判 cwd、批次 hooks 使用旧 cwd、摘要遗漏原始历史归档、工具状态误判及敏感字段进入派生记录等问题。

原测试按新契约迁移：Todo/目录以 Agent 所拥有的 state 为准；Web 恢复 agent 内容使用捕获的原始字节；legacy/外部修改不再默认覆盖。R05 的 Windows 路径断言改为检查实际模型可见 content，避免 Python 容器 repr 的反斜杠转义影响断言。默认配置测试隔离本机 `.env`。这些修改保留原验证目的，没有添加 xfail 或弱化冲突保护。

## 最终验证

最终全量执行：**305 passed / 1 skipped / 0 failed / 0 errors**，耗时 31.38 秒。证据：[JUnit](validation/r01-r06-final.xml)、[完整日志](validation/r01-r06-final.log)。

| 检查 | 结果 |
|---|---|
| 项目原有测试 | 214 passed |
| Web 回归（含 CRLF 外部修改保护） | 12 passed |
| R01–R06 原设计验收 | 32 passed |
| 主代理扩展验收 | 47 passed / 1 条件 skipped |
| Ruff：corecoder、tests、designDocument、web_backend、examples | [通过](validation/ruff-final.log) |
| 设计/spec/测试映射、本地链接及 Python 语法 | [通过](validation/delivery-final.log) |
| Git diff 空白检查 | 通过 |

复测命令（在项目根目录，`python` 指向上述已有 dev 环境）：

```powershell
python -X utf8 -m pytest -c designDocument/pytest.ini tests web_backend/tests designDocument/tests/test_r01_agent_state_isolation.py designDocument/tests/test_r02_workspace_paths.py designDocument/tests/test_r03_undo_conflicts.py designDocument/tests/test_r04_undo_transaction.py designDocument/tests/test_r05_context_budget.py designDocument/tests/test_r06_summary_evidence.py designDocument/tests/test_acceptance_r01_r06_extended.py -q --basetemp=designDocument/validation/tmp-final-rerun --junitxml=designDocument/validation/r01-r06-final.xml
python -m ruff check corecoder tests designDocument web_backend examples
python -X utf8 designDocument/verify_delivery.py
git diff --check
```

`--basetemp` 仅可指向专用测试 scratch 目录。最终验证后清理了本任务创建的临时目录，保留日志和 JUnit；运行时的 `.corecoder-artifacts/` 已加入忽略规则。README 和包描述中的源码统计已按最终源码更新。

测试使用已有 Python 3.12 隔离环境和脚本模型，不使用真实 API key 或付费请求；测试状态目录独立。Windows pytest 临时目录的受限沙箱访问问题通过仅针对离线测试的提升运行解决。

## 验证边界

Windows 主机缺少创建符号链接的权限，相应替换链接用例会条件跳过；需要在有权限的主机补跑。未知 provider 的 token 数是带余量的本地估算，本轮没有真实 provider/tokenizer 的跨模型集成、Linux/macOS、跨进程竞争或断电测试。模型摘要的语义保真仍需固定任务集评估；自动化证明的是输入证据、状态与预算契约。R07–R11 未在本轮实现或宣称通过。
