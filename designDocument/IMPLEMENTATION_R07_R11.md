# R07–R11 实施与验收记录

日期：2026-10-11（Asia/Shanghai）。本轮完成 designDocument 剩余的 R07–R11，并回归已经交付的 R01–R06、项目原有测试与 Web 后端。沿用当前工作区，保留原有前端及未提交改动，没有创建 Git 提交。

三个 GPT-5.6-luna 子代理分别负责工具运行与 CLI、模型接口与费用账本、配置与持久化及 MCP；另一个 GPT-5.6-luna 子代理整理流消费的收尾逻辑。主代理负责需求核对、代码审查、补充功能验收、回归及交付记录。

## 实现结果

| 设计 | 已实现行为 |
|---|---|
| R07 | ToolCall 保留原始 JSON 与解析错误；注册和执行采用标准 JSON Schema 校验并尊重远端 schema 版本；参数错误在展示、hooks、授权和执行前返回。统一流水线记录五种 outcome 和失败阶段，保留 post hook 失败前的实际结果；批次收尾补齐回复，缓存按批次隔离，已完成轮次可复用 ID。 |
| R08 | frozen UsageEntry、只读账本、Decimal 费用和请求开始时的模型/费率快照；每次实际尝试独立记账并按 request/attempt 去重。重复 usage 使用最后累计值，失败及回调异常仍记账和关闭流；缺用量与未知费率分开。运行 scope 隔离并发 Agent 用量，父级包含子任务请求一次；旧 Web 汇总以未知价格导入。 |
| R09 | chat_result 与原 chat 共用循环，提供 completed/max_rounds/cancelled/failed；保留 chat 的原异常语义。轮数只计算已发起的主模型调用，正文累积多个轮次，completed 仍保留工具错误。CLI --json 输出一个版本化对象，诊断进入 stderr，退出码为 0/2/130/1；支持 --max-rounds，REPL 展示轮数上限及费用小计。 |
| R10 | hooks/MCP 配置严格检查结构，隔离坏项并保留有效项；诊断包含绝对文件路径和字段类型，不输出敏感值或完整 hook 命令。session v2 与 legacy v1 共用完整消息链校验，坏 UTF-8、伪版本和非法角色安全拒绝；同 ID 写入锁、同目录唯一临时文件、flush/fsync、原子替换及失败清理。 |
| R11 | 固定 MCP 2025-06-18 协议，校验 initialize、能力、serverInfo 和工具 schema；无 tools 能力不请求工具列表。共享发现 deadline、原样透传 opaque cursor、空页继续、循环/页数/工具数/重名保护；候选表全部通过后才发布，失败回收子进程。 |

新增运行依赖 `jsonschema>=4.23,<5`。实现仅覆盖工具相关 MCP 接口，协议依据为 [2025-06-18 tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) 和 [lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)。

## 主代理审查与补充验收

[扩展验收](tests/test_acceptance_r07_r11_extended.py) 共 64 项，覆盖嵌套 schema、非法调用 envelope、跨轮 ID 复用与中断、非字符串工具结果、post 中断后的已完成写入、执行前取消和执行结果 unknown、记录失败后的完整工具链、校验异常后的已完成结果、真实 Python 模块入口四种退出状态、Unicode/多轮正文、纯 JSON 与 streaming 不重复、预算拒绝轮数、共享模型并发用量、父子任务用量、失败尝试、400 参数回退、usage details、冻结账目及重放去重、端点凭证清理、Web 旧汇总恢复、会话损坏/版本/并发/fsync 故障和 MCP 发现边界。

审查发现并交回修复的主要问题包括：中断被吞后继续执行、工具已执行却补成取消、跨轮旧缓存冒充新回复、失败阶段不准确、轮数重复计数、Web 批处理签名不兼容、全局 token 差值混入并发请求、失败请求及回调漏账、费用历史重新定价、损坏配置解包崩溃、坏角色触发 TypeError、损坏存档无诊断，以及发现阶段遗漏 schema 校验。

兼容测试按设计迁移：三个费用用例改为注入真实 usage，禁止人工累计计数推定历史费用；进入执行后的中断断言从旧 `[interrupted]/cancelled` 改为 `unknown`，执行前取消保持 cancelled，已完成结果保持 succeeded。工具链验收按同批 ID 唯一检查，独立且已关闭的轮次允许复用 ID。没有删除失败用例、添加 xfail 或放宽副作用和消息链断言。

## 最终验证

全量执行：**441 passed / 1 skipped / 0 failed / 0 errors**。证据：[JUnit](validation/r01-r11-final-1011.xml)、[完整日志](validation/r01-r11-final-1011.log)。

| 检查 | 结果 |
|---|---|
| 项目原有测试 | 214 passed |
| Web 回归 | 12 passed |
| R01–R11 原设计验收 | 104 passed，其中 R07–R11 72 passed |
| R01–R06 扩展回归 | 47 passed / 1 条件 skipped |
| 本轮主代理扩展验收 | 64 passed |
| Ruff：corecoder/tests/designDocument/web_backend/examples | [通过](validation/ruff-final-1011.log) |
| 设计/spec/测试映射、本地链接和 Python 语法 | [通过](validation/delivery-final-1011.log) |
| Git diff 空白检查 | 通过 |

R07–R11 本轮修复前为 **68 failed / 4 passed**：[基线 JUnit](validation/r07-r11-baseline-1011.xml)、[基线日志](validation/r07-r11-baseline-1011.log)。中间失败记录保留，最终状态以上表为准。R01–R06 的历史交付见 [上轮记录](IMPLEMENTATION_R01_R06.md)。

复测命令（项目根目录，使用已安装 dev 依赖的 Python）：

```powershell
python -X utf8 -m pytest -c designDocument/pytest.ini tests web_backend/tests designDocument/tests -q --basetemp=designDocument/validation/tmp-rerun-all --junitxml=designDocument/validation/r01-r11-final-1011.xml
python -m ruff check corecoder tests designDocument web_backend examples
python -X utf8 designDocument/verify_delivery.py
git diff --check
```

本机解释器为 `E:\github项目\.corecoder-review-py312\Scripts\python.exe`。Windows 沙箱访问测试临时目录失败时，离线测试使用提升运行；所有状态目录隔离，不调用付费模型，不使用真实凭证。测试完成后清理本任务的专用 scratch，保留日志和 JUnit。README 与包描述的源码统计已同步。

## 验证边界

唯一跳过项是本机缺少创建 Windows 符号链接的权限。离线验收没有验证真实 provider 的报价和计量、真实模型摘要质量、第三方 MCP 服务、Linux/macOS、断电和跨进程存档竞争。会话 cwd/Todo/checkpoint/完整账本的统一恢复、硬费用预算和可中断子进程执行等产品增强仍属于 [实施计划](IMPLEMENTATION_PLAN.md) 已列出的独立后续范围。
