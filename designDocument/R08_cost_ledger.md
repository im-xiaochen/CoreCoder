# R08：按请求保存不可变费用账本

优先级 P2。关联 [spec](specs/R08.spec.md)、[功能测试](tests/test_r08_cost_ledger.py)、[公共契约 §5](CONTRACTS.md)。后续硬费用预算应基于该账本。

## 问题与目标

当前两个累计 token 计数器配合“当前模型单价”估费，切模型或改价表会重定价全部历史。目标是已产生费用稳定、未知部分可见、两个 provider 后端行为一致。

## 数据模型与计费流程

`UsageEntry` 使用 frozen dataclass，记录 request_id、attempt_id、provider、endpoint 标识、model、prompt/completion/cached/reasoning tokens、usage_known、输入/输出单价、currency、费率版本或采样时间、estimated_cost。endpoint 标识不含凭证；缓存/推理字段暂不参与未知的折扣规则。

1. chat 开始固定 model 和费率快照，用于实际请求 params 和后续记账；不能在 stream 结束时重读 `self.model`。费率查找按 provider/endpoint/model 精确匹配，可由用户价格配置覆盖，避免只凭同名模型假设收费一致。
2. 每个实际 attempt 仅追加一次账目；usage chunk 重复出现时采用最终累计 usage，不把每个 chunk 相加。共享 LLM 的父子/并行请求用独立 request context 和账本锁，不能用一个可变 `current_model` 临时字段。
3. 有 usage 且已知费率：Decimal 计算并固定费用；有 usage 无费率：计入未知价格 token；无 usage：missing_usage_requests 加一，不假装是免费调用。
4. 中途失败若已经收到可用 usage 则记录该 attempt；尚未收到则记 usage_unknown。网络重试的每个 attempt 都可能被收费；不要只把最后成功 attempt 当全部成本。请求回放和恢复使用 request_id 去重，不能重新生成历史费用。
5. total_prompt_tokens/total_completion_tokens 保留为账本已知 usage 的求和兼容视图；`estimated_cost` 全部可计价时返回总额，否则 None。`cost_summary` 无论是否完整都提供已知小计和未知计数。

## 文件改动落点

`llm.py` 引入 Ledger/RequestContext，LLM/LiteLLM 同一初始化函数；`cli.py` 的 /model 只改后续配置，/tokens 显示部分小计；`session.py` 可在 v2 的可选字段保存账本快照；`agent.py` 的 RunResult 用请求 ID 汇总本次增量，父子共享账本时只计一次。

## 兼容与迁移

测试使用人工固定费率 model-a/model-b，不查询或宣称任何模型当前报价。现有 `_PRICING` 可先作为费率来源，后续再维护真实价格；账本要固定当时的值。旧 session 只有汇总 token 没有历史模型归属时，将其标记 legacy unknown，不能按恢复时模型补算历史。

`_drain` 在单独调用时捕获入口模型快照，便于复用原有离线测试；正式 chat 将开始时 snapshot 作为显式参数传入，测试覆盖 stream 内模型切换。不要通过从累计 token 差值倒推并发单次 usage。

## 验收

切模型和改费率不改旧账；新请求按新费率增加；未知模型显示已知小计和未知 tokens；缺 usage 与零用量区分；请求中途改模型不改计费归属；LiteLLM 同样通过。重试 attempt、重复 usage chunk、账本恢复和并发累计列入后续扩展验收。
