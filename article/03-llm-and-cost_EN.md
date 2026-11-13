# Plug in any model, and get the bill right while you're at it

The last two pieces covered the loop and the tools, the agent's hands and feet. This piece covers the brain's interface: how the model gets plugged in, how streaming output is handled, how to survive a provider acting up, and a question many tutorials skip but that you'll care about on day one after going live, namely how much this round actually cost.

The file is `corecoder/llm.py`, 612 lines, the largest single file in the engine. It's large because it carries, on your behalf, all the inelegant parts of dealing with a real API.

## A bet: everyone looks like OpenAI

The default `LLM` adapter wraps the OpenAI SDK and uses the chat completions interface. To use a compatible service, set its API key, endpoint, and model name. For example, with a DeepSeek account and key:

```sh
export OPENAI_API_KEY='your-api-key'
export OPENAI_BASE_URL='https://api.deepseek.com'
export CORECODER_MODEL='deepseek-chat'
```

The common interface lets the rest of the agent work with the same messages and tool calls. Services that need a different interface can use the optional LiteLLM adapter described below. First, follow the main path.

## Streaming output, a bit more trouble than you'd think

Tool arguments arrive in pieces, just like text. `LLM._drain()` groups IDs, function names, and argument fragments by call index, then parses the JSON after the stream ends. If the assembled arguments are invalid, the tool call carries the parse error so the main loop can refuse it. An empty argument object and a parse failure are different outcomes.

Text and reasoning use separate callbacks, `on_token` and `on_reasoning`. For tool-call turns, fragments explicitly marked as `reasoning_content` also go into the assistant history with their origin. Later requests replay that field only to the same provider, actual endpoint, and model. Generic `reasoning` is for display, and ordinary final answers do not keep reasoning fields in history.

Read `LLM.chat()` for the boundary between saved and sent messages. Its request setup calls `project_messages(messages, context)`, then uses the returned list as the request’s `messages`. That method copies each message and removes the internal source marker before it reaches the provider.

## How tokens get counted

`stream_options={"include_usage": True}` asks the provider to include usage. If the stream reports cumulative usage more than once, the adapter uses the final cumulative figures. Null fields in a reported usage object are read as zero. A request with no reported usage is recorded separately as missing usage. Even on a failed or interrupted stream, the adapter closes the stream and records what it knows.

Provider usage is for settlement. The token counts used to check context space and reserve run budget before sending are estimates. See [piece four](04-context_EN.md).

## The provider acts up, and we ride it out ourselves

`_call_with_retry()` backs off on transient failures such as rate limits, timeouts, connection failures, and some server errors. The surrounding send path also handles transient failures after the stream has started. Client errors such as authentication failures do not warrant a retry.

A 400 can also mean that a provider rejects a request parameter. `_adapt_rejected_param()` can translate a rejected `max_tokens` to `max_completion_tokens` or remove a rejected `temperature`, including when the provider leaves the parameter name unquoted. When the error does not identify a recognized parameter, the send path can try dropping `stream_options`. These fallbacks adapt the request to the current model.

## The OpenAI-incompatible ones go to LiteLLM

For a provider outside the OpenAI-compatible interface, install the optional LiteLLM dependency and select it with `CORECODER_PROVIDER=litellm`. The CLI and agent still use the same basic message and result interface. Per-run budget enforcement currently works with the OpenAI-compatible adapter. CoreCoder refuses a budgeted LiteLLM request before sending it.

## Working out the cost

Cost comes from the [usage ledger](../corecoder/usage.py). A request records its model, endpoint, and input and output rates at the time of the call. Switching models or editing the price table later does not reprice history. You can override built-in prices in `~/.corecoder/pricing.json`. Use `/tokens` to view usage and cost information. The ledger distinguishes the known subtotal from unknown prices and missing usage.

The [run budget](../corecoder/budget.py) reserves quota before each physical request. That covers the initial call, parameter fallbacks, retries, summaries, and children. Requests settle against confirmed usage where available, and unconfirmed amounts are tracked separately. For example, limit a read-only one-shot task like this:

```sh
corecoder -p "Summarize how this project registers its tools" --json --budget-requests 5 --budget-tokens 20000 --budget-cost 0.10
```

Cost limits use USD. A zero limit prohibits that resource and an omitted limit leaves it unrestricted. With a cost limit, unknown prices are refused by default. Use `--budget-unknown-policy allow_unknown` to opt in. Local estimates and provider bills can differ.

## Where config comes from

Finally, a thread through `config.py` (60 lines). It reads config from environment variables with a sensible priority:

```python
api_key = (
    os.getenv("CORECODER_API_KEY")
    or os.getenv("OPENAI_API_KEY")
    or os.getenv("DEEPSEEK_API_KEY")
    or ""
)
```

The dedicated variable wins, then it falls back to the generic `OPENAI_API_KEY`, then to `DEEPSEEK_API_KEY`. It also walks up from the current directory to the home directory loading a `.env` file (`override=False`, so it won't overwrite the real environment variables you've already set). This way you drop a `.env` in the project directory and it works on entry, no need to export every time. A small thing, but handy.

## What this piece leaves you with

- Assemble streamed tool-call arguments before parsing. The loop refuses malformed calls.
- Tool-call reasoning can return only to its original provider, model, and endpoint.
- Parameter fallbacks and transient retries pass through the physical request budget. The ledger records usage and uncertainty.

Next, [how to decide whether a request fits the context window](04-context_EN.md).
