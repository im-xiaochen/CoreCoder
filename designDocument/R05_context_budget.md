# R05：完整请求预算闸门与大输出归档

优先级 P1。关联 [spec](specs/R05.spec.md)、[功能测试](tests/test_r05_context_budget.py)、[公共契约 §3](CONTRACTS.md)。R06 使用相同归档和摘要管道。

## 问题与目标

当前 only-history 计量漏 system/schemas/output reserve；保留完整 tool 组时切点可能为 0，增加摘要反而更大；少行长输出不截短。`compressed=True` 仅说明执行过操作，不能作为发送许可。

目标：每次请求都验证 `input_tokens + output_reserve <= context_limit`；不能满足就停止并说明原因；压缩保持工具调用链完整和关键用户约束。

## 具体流程

1. 新 `prepare_request` 在消息副本上工作，先计 system、动态 Todo、工具 schemas、history、消息框架、安全余量及输出预留。`estimate_request_tokens` 是唯一计量入口。用户显式窗口优先；已知模型窗口配置用于默认值，切模型重新构建预算配置与阈值。
2. 固定成本已经超窗时直接返回 `budget_ok=False/reason=context_budget_exceeded`。不能偷偷移除 MCP 工具 schema 或裁掉系统约束。最近用户消息本身过大时也拒绝，并建议拆分输入。
3. 大 tool result 按字符/token 限制头尾预览，不依赖行数。完整原文存入会话 artifacts，消息保留工具 ID、状态、绝对归档路径、hash 和分页提示。单行输出应支持字节/字符 offset；read_file 的按行分页不能独自解决单行巨量文本。
4. 历史按 user turn 与 assistant/tool 完整组切分。先确定可移出的完整组，再交 R06 结构化摘要。没有可移出的旧组时直接缩短组内 result 预览；不要为空历史再追加摘要两条消息。
5. 每一步重新计量，仅在 token 减少或预算满足时提交候选；重复处理相同内容应幂等。最后仍超限则返回失败，原 history 保留。归档失败也拒绝丢弃原文。
6. Agent 每轮在 llm.chat 前调用 prepare，预算失败抛 `ContextBudgetExceeded`；`chat_result` 映射 failed/finish_reason。摘要调用和子任务同样检查预算，不能为压缩发出另一个超限请求。

## 文件改动落点

`context.py` 新完整请求计量和 CompressionResult；`agent.py` 增发送闸门；`config.py/cli.py` 区分 context_limit/output_limit 并处理 /model；`tools/read.py` 增归档分页读取（或新增只读 artifact 工具）；`state.py` 管理 artifact_dir；`llm.py` 参数中的输出上限与预留一致。

## 容量、计量与兼容

保留 `maybe_compress -> bool` 用于旧调用者，内部可复用新流程；发送闸门必须读取 budget_ok。输出预留不得固定比模型可用窗口还大；小窗口测试通过显式 reserve 建立可满足场景。

已知 tokenizer 时使用实际计量；未知 provider 估算须加可配置余量并标 `estimated`，不能对真实服务宣称数学保证。若 provider 仍返回 context overflow，记录估算偏差并返回明确失败，避免无限重试相同请求。测试只证明本地计量契约，真实 tokenizer 与 provider 上限需后续集成验证。

归档有会话生命周期、配额和敏感内容访问控制；输出超过磁盘配额时应报告截断/拒绝，不能假装原文已保存。shell 输出采集阶段的内存硬限属于后续增强，压缩发生在采集后不能解决该问题。

## 验收

超长单行、长中文、六调用同组能保留全部回复并满足预算；大 system 或 schemas、过大最新用户请求明确失败；归档内容可精确回读；预算拒绝发生在模型调用前。切模型窗口变更、归档满盘和大量 schemas 的混合边界继续扩充。
