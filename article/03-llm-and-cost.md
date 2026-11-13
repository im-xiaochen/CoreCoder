# 接入任意大模型，顺便把钱算清楚

前两篇讲的是循环和工具，也就是 agent 的手脚。这一篇讲大脑接口：模型怎么接进来，流式输出怎么处理，provider 抽风了怎么扛，以及一个被很多教程跳过、但你上线后第一天就会关心的问题，这一轮到底花了多少钱。

对应的文件是 `corecoder/llm.py`，612 行，是引擎里最大的单文件。它大，是因为它替你扛下了和真实 API 打交道时所有不优雅的部分。

## 一个赌注：大家都长得像 OpenAI

默认的 `LLM` 适配器封装 OpenAI SDK，走 chat completions 接口。换到兼容服务时配置密钥、端点和模型名。例如已有 DeepSeek 账号和密钥时：

```sh
export OPENAI_API_KEY='your-api-key'
export OPENAI_BASE_URL='https://api.deepseek.com'
export CORECODER_MODEL='deepseek-chat'
```

接口统一以后，代理仍使用同一套消息与工具调用。需要其他接口时可以使用下文的 LiteLLM 可选适配器。先看默认路径。

## 流式输出，比你想的麻烦一点

工具参数跟正文一样会分片到达。`LLM._drain()` 按调用的 index 收集 ID、函数名和参数片段，流结束后才解析 JSON。拼出来的参数不合法时，工具调用会带上解析错误，交给主循环拒绝。这一步能区分「没有参数」和「参数没解析出来」。

正文和推理分别走 `on_token` 和 `on_reasoning`。对于工具调用，明确标成 `reasoning_content` 的片段还会与 assistant 的调用一起保存，并标记来源。下一次发送时，只有供应商、实际端点和模型都相同，才会回传这段推理。`reasoning` 这样的通用显示字段不按这个协议回传，普通最终回答也不把推理字段写进历史。

沿着源码看这个边界，可以从 `LLM.chat()` 找到 `project_messages(messages, context)`。发送请求时用的是这个函数返回的消息列表。它复制消息，并在发给供应商之前去掉内部来源标记。

## token 是怎么数出来的

`stream_options={"include_usage": True}` 请求供应商附带用量。如果流多次带回累计用量，适配器记最后一份累计值。已返回的用量字段可能为空，单个空字段按零读取；整次请求没有报告用量，则在账本上记为缺失。即使一轮失败或流被打断，适配器也会关闭流并记录能确认的用量。

供应商实际用量用于结算。发送前用来判断上下文或预留运行额度的 token 数仍是估算，见 [第四篇](04-context.md)。

## provider 抽风了，自己扛

`_call_with_retry()` 对限流、超时、连接错误和部分服务端错误退避重试。流开始后仍可能断掉，外层发送流程也处理这类故障。失败鉴权一类的客户端错误无需重试。

400 还有一种情况是供应商明确不支持某个参数。`_adapt_rejected_param()` 可以把被拒绝的 `max_tokens` 换成 `max_completion_tokens`，或者在供应商拒绝 `temperature` 时移除它，包括报错没有给参数加引号的情况。无法识别具体参数时，发送流程可尝试去掉 `stream_options`。这里适配的是当前请求参数，没有自动切换备用模型。

## 不兼容 OpenAI 的，交给 LiteLLM

OpenAI 兼容接口之外，可以安装项目的 LiteLLM 可选依赖，再通过 `CORECODER_PROVIDER=litellm` 选择它。CLI 和 agent 继续使用同一套基本消息与结果接口。运行预算目前只支持 OpenAI 兼容适配器。给 LiteLLM 请求设置预算时，CoreCoder 会在发出请求前拒绝运行。

## 把钱算出来

费用从 [用量账本](../corecoder/usage.py) 汇总。每次请求保存当时的模型、端点、输入和输出价格，之后切换模型或修改价目表，不会重算旧请求。可在 `~/.corecoder/pricing.json` 覆盖默认价格。`/tokens` 会展示用量和费用信息，账本区分已知小计、未知价格和缺失用量。

预算与账本各管一件事。[运行预算](../corecoder/budget.py) 在每次物理请求发出前预留额度，包括首次调用、参数回退、重试、摘要和子代理请求。请求结束后按能确认的用量结算，无法确认的部分会单独保留。比如脚本可这样限制一次只读任务：

```sh
corecoder -p "概括本项目的工具注册方式" --json --budget-requests 5 --budget-tokens 20000 --budget-cost 0.10
```

费用上限使用美元。设为零表示禁止消耗该资源，省略上限则不限制该项。设了费用上限时默认拒绝未知价格，可显式用 `--budget-unknown-policy allow_unknown` 改变策略。应用的预估和供应商的实际账单可能不同。

## 配置从哪来

最后串一下 `config.py`（60 行）。它从环境变量读配置，带一个合理的优先级：

```python
api_key = (
    os.getenv("CORECODER_API_KEY")
    or os.getenv("OPENAI_API_KEY")
    or os.getenv("DEEPSEEK_API_KEY")
    or ""
)
```

专属变量优先，然后退到通用的 `OPENAI_API_KEY`，再退到 `DEEPSEEK_API_KEY`。它还会从当前目录往上一直找到家目录，加载 `.env` 文件（`override=False`，不覆盖你已经设好的真环境变量）。这样你在项目目录放一个 `.env`，进来就能用，不用每次 export。小事，但顺手。

## 这一篇带走什么

- 流式工具调用先拼齐参数，坏数据交给主循环拒绝。
- 工具调用的推理回传有来源约束，切换模型或端点不会把旧推理发过去。
- 参数回退与瞬时错误重试都经过物理请求预算。账本记录实际用量及不确定项。

下一篇看 [请求在发出前怎样判断能不能放进上下文](04-context.md)。
