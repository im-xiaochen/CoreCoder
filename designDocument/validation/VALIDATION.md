# 设计与测试验证记录

2026-10-11 更新：R01–R11 已全部实现并验收，最新全量结果为 **441 passed / 1 条件 skipped / 0 failed / 0 errors**。见 [剩余任务实施记录](../IMPLEMENTATION_R07_R11.md)、[最终 JUnit](r01-r11-final-1011.xml) 和 [最终日志](r01-r11-final-1011.log)。以下继续保留设计编写时及 2026-10-08 的历史基线，不能用历史范围说明判断当前交付状态。

2026-10-08 更新：本文件以下内容保留 2026-10-07 的修复前基线。R01–R06 的实现、代码审查、补充验收和最终回归结果见 [本轮实施记录](../IMPLEMENTATION_R01_R06.md)，最终执行证据为 [JUnit](r01-r06-final.xml) 和 [日志](r01-r06-final.log)。R07–R11 仍在本轮范围外。

日期：2026-10-07（Asia/Shanghai）。被测源码：CoreCoder 0.7.0，`c2d841719019a96346c42173ed96dbe71c5cdeda`。本次仅新增 `designDocument/`，原有 `review/` 材料保留；没有修改运行源码。

## 有效执行结果

| 检查 | 结果 | 证据 |
|---|---|---|
| 新验收用例收集 | 104 项，11 个测试模块 | pytest collect-only |
| 修复前功能验收 | **99 failed / 5 passed / 0 errors / 0 skipped**，预期 RED，pytest exit 1 | [JUnit](acceptance-baseline.xml)、[日志](acceptance-baseline.log) |
| 项目原有测试 | **214 passed**，pytest exit 0 | [JUnit](existing-tests.xml)、[日志](existing-tests.log) |
| Python 静态检查 | `ruff check corecoder tests designDocument` 通过 | 最终检查命令 |
| 文档完整性 | 11 个独立方案、11 个 spec、11 个功能测试模块；测试函数与 spec 对应；本地链接和 Python 语法有效 | [verify_delivery.py](../verify_delivery.py) |

5 个已通过用例是已有的正常或容错路径：无外部修改撤销、没有显示/授权回调时的混合 batch 回复、legacy/截断会话、MCP 单页/空工具列表。它们不能证明相应问题已修复。其余失败包括实际缺陷和待实现契约；未使用 xfail、skip 或“确认缺陷存在”的通过断言。

## 按问题分组的修复前基线

| 问题 | 用例数 | failed | passed | errors |
|---|---:|---:|---:|---:|
| R01 | 4 | 4 | 0 | 0 |
| R02 | 5 | 5 | 0 | 0 |
| R03 | 6 | 5 | 1 | 0 |
| R04 | 4 | 4 | 0 | 0 |
| R05 | 8 | 8 | 0 | 0 |
| R06 | 5 | 5 | 0 | 0 |
| R07 | 13 | 12 | 1 | 0 |
| R08 | 6 | 6 | 0 | 0 |
| R09 | 14 | 14 | 0 | 0 |
| R10 | 29 | 28 | 1 | 0 |
| R11 | 10 | 8 | 2 | 0 |
| 合计 | 104 | 99 | 5 | 0 |

R02/部分 R05/R08/R09 的失败直接指明缺少 SessionState、prepare_request、cost_summary、chat_result 等拟议接口。这是实施入口未落地的验收结果，不把它们另算成原审查报告之外的新 bug。

## 运行条件与隔离

使用已有隔离 Python 3.12 环境：`E:\github项目\.corecoder-review-py312\Scripts\python.exe`。新测试通过 autouse fixture 重定向 checkpoint、session、CORECODER_DATA_DIR；FileHistory 使用内存；socket connect 被禁止。模型均为脚本或假 stream；MCP 为 stdlib Python 子进程；R02 通过项目 shell 路由使用 Git Bash。

首次受限沙箱执行遇到 Windows pytest 临时目录访问 PermissionError，未产生有效功能结果，未计入上表。之后在允许访问测试临时目录的环境重跑，得到无 setup/teardown errors 的结果。scratch 已在验证后清理，不作为交付文件。

重跑命令（从项目根目录，python 指向已有 dev 环境）：

```powershell
python -X utf8 -m pytest -c designDocument/pytest.ini designDocument/tests -q --tb=short --basetemp=designDocument/validation/tmp-validated --junitxml=designDocument/validation/acceptance-baseline.xml
python -X utf8 -m pytest tests/ -q --basetemp=designDocument/validation/tmp-existing --junitxml=designDocument/validation/existing-tests.xml
python -m ruff check corecoder tests designDocument
python -X utf8 designDocument/verify_delivery.py
```

`--basetemp` 目录会被 pytest 清空，只应用于上述专门的测试 scratch 路径，不能指向含有用户材料的目录。

## 证明范围

本次证明设计资料完整、测试代码可收集并实际执行、修复前行为与验收差距可见、原测试未受新增资料影响。**未证明运行缺陷已修复。** 修复后的目标为本目录 104 个用例全部通过，并通过调整明确兼容断言后的原有测试。

未执行真实付费模型、真实 provider usage/计价、真实第三方 MCP、Linux/macOS、断电恢复、跨进程竞争或极大仓库压力验证。每项 spec 的“后续验证”是明确的覆盖缺口，不能在后续发布时忽略。
