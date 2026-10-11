# R09：结构化结束状态和稳定 CLI 退出码

优先级 P2。关联 [spec](specs/R09.spec.md)、[功能测试](tests/test_r09_cli_result.py)、[公共契约 §6](CONTRACTS.md)。依赖 R07 的完整结果、R08 的增量 usage。

## 问题与目标

当前 `_run_once` 只打印流式回调，忽略 chat 的轮次耗尽返回值，导致未完成也 exit 0。目标是脚本可靠识别正常结束、轮次耗尽、中断和错误，同时保留现有交互文本接口。

## 具体设计

1. 提取一个内部循环 `_run_turn` 返回 RunResult；新增 `chat_result` 包装普通异常和 KeyboardInterrupt。现有 `chat` 复用循环并维持 str 返回及异常习惯，demo/嵌入调用不需立刻改成结构体。
2. 状态为 completed、max_rounds、cancelled、failed；max_rounds 仅在耗尽主循环仍需要继续时产生。达到上限后的历史仍包含最后一组工具的完整 reply；状态不是通过搜索返回文字推断。
3. completed 表示模型给了终止回答，不保证业务全部成功；`tool_errors` 保留执行失败、拒绝、结果未知及 ID。finish_reason 提供精确原因；usage 是本次运行增量，含摘要与子任务且不重复计费。
4. `_run_once(..., json_output=False) -> int` 统一 status/code 映射：0、2、130、1；main 总是传播这个返回值。argparse 自身的参数错误保持其约定的 code 2，并通过 reason 区分。
5. 新 `--json` 缓冲正文，不在 stdout 输出流 token；结束只序列化一份 schema_version=1 对象。文本模式仍可流式显示正文。工具摘要、权限说明、reasoning、warning 和错误诊断全移到 stderr，两种模式都保持 stdout 可消费。
6. REPL 读取结构化结果显示结束原因，用户仍能继续；取消先由 R07 补齐消息链。未确认终止的外部进程体现在 tool outcome=unknown，不能因为 cancelled 就显示“所有工具已停止”。

## 输出示例

```json
{"schema_version":1,"status":"max_rounds","text":"","rounds":1,"tool_errors":[],"usage":{"prompt_tokens":100,"completion_tokens":20},"finish_reason":"max_rounds","exit_code":2}
```

这是契约示例，用量为虚构值。用户正文可以与工具调用共存；JSON 模式的 text 必须累积需要返回的实际正文，不能丢掉最后一个工具轮的内容。

## 文件改动落点与兼容

`agent.py` 内部结果和 legacy wrapper；建议新增 `results.py`；`cli.py` 参数、one-shot、main、REPL；`__main__.py` 核对退出传播；demo 等继续调用 chat。JSON 未知字段允许消费者忽略，新增不兼容改动必须升 schema_version。

不把所有工具 Error 自动改成 exit 1：模型可能通过重试或替代方案恢复。业务成功策略可由上层结合 tool_errors 决定；首版不新增模糊“success=True”。普通 str 模式不要再把流程状态塞进容易被误读的正文。

## 验收

四种状态、对应退出码、clean JSON、text 模式耗尽诊断、main 退出传播和 completed 时仍保留工具错误都有测试。后续补真实 `python -m corecoder` 子进程级输出、取消外部进程与 JSON Unicode/多轮混合正文。
