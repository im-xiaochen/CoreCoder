# R07：参数先校验，工具调用始终有确定收尾

优先级 P1。关联 [spec](specs/R07.spec.md)、[功能测试](tests/test_r07_tool_validation.py)、[公共契约 §4](CONTRACTS.md)。向 R06/R09 提供统一 outcome。

## 问题与目标

`json.loads` 可以产生 list/null/string；当前解析失败又静默变 `{}`。CLI 显示、hooks、授权先于参数绑定，异常时仅 KeyboardInterrupt 补 reply。目标是坏参数不产生副作用，每次已接受的调用恰有一个 tool reply，失败后可继续对话。

## 参数边界

1. LLM 解析保留原始字符串及 `arguments_error`；错误 JSON 不修成空参数。正常 dict 继续兼容 ToolCall 的原构造函数。
2. Agent 在任何回调之前查工具注册表、验证参数根是对象，再按工具 JSON Schema 验证 required/type/enum/nested items/额外字段。Python 的 bool 不等于 JSON integer；未知工具也直接得到明确错误，不进入 hook/授权。
3. 首版推荐引入标准 JSON Schema validator 作为显式依赖，同时保留 execute 签名 bind 作实现侧校验。内置工具 schema 补 `additionalProperties=False`；MCP 工具尊重远端 schema 的真实设置，不能全局强行拒绝任意额外字段。非法工具 schema 在注册阶段拒绝，避免执行阶段解释不一致。

## 执行和收尾

统一 `_run_tool_call` 覆盖 single 和 batch：validate → on_tool → pre → permission → execute → post。每步记录 stage；普通异常转换为该调用的 Error outcome。pre 阶段失败不执行；post 异常保留工具实际结果并附 warning。

batch 先为各 call 建 pending outcome。一个调用坏参数不妨碍独立合法调用；副作用排序遵循 R02。所有 reply 按模型提供顺序提交，不能 futures 完成顺序插入。以当前 batch 的 IDs 做 exactly-once 收尾，不能依赖跨全历史“以前是否出现过同 ID”。

最外层 finally 为仍 pending 的调用补 reply。KeyboardInterrupt 后未开始者 cancelled；工具已开始但无法确认结果者 unknown，保留实际已完成 outcome。线程取消不等于外部进程停止，本方案不谎称已终止 shell；完整进程树取消属于后续增强。

空 ID、重复 ID 或缺工具名属于响应结构无效：应在追加 assistant 消息前拒绝该 response，返回明确错误，避免制造无法匹配的历史。正常工具结果必须转换为字符串；工具内部 TypeError 与调用绑定 TypeError 分别显示执行错误和参数错误。

## 文件改动落点与兼容

`llm.py` 扩 ToolCall，保留 raw；`agent.py` 统一校验/流水线/收尾；`tools/base.py` 定义 outcome 和参数验证适配；内置 schemas 补约束；`cli.py` 的 `_brief` 仍作防御性类型检查，但不是参数校验主入口；`pyproject.toml` 记录 schema validator 依赖及版本范围。

保留 hooks 在已验证调用的授权前执行这个旧顺序；hooks 本身仍是用户配置命令，计划模式不是整个进程的沙箱。schema 失败不得运行任何 hooks。

## 验收

数组/null/字符串/数字/缺字段/错类型/未知字段、截断 JSON、显示/pre/授权异常、post 异常、未知工具和混合 batch 均有测试。检查 side effect、hook 次数、reply 一次性及下一轮可继续，而非仅断言“不抛异常”。后续补重复 ID、执行中断、nested schema 与未知远端 outcome。
