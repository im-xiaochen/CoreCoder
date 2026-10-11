"""LLM provider layer - thin wrapper over OpenAI-compatible APIs.

Since most providers (DeepSeek, Qwen, Kimi, GLM, Ollama, etc.) expose an
OpenAI-compatible endpoint, we just use the openai SDK directly.  Switch
provider by changing OPENAI_BASE_URL + OPENAI_API_KEY. That's it.

For providers that are NOT OpenAI-compatible (AWS Bedrock, Google Vertex,
etc.), use the LiteLLM backend which routes to 100+ providers through a
single unified interface. Set CORECODER_PROVIDER=litellm.
"""

import contextvars
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from openai import APIConnectionError, APIError, APITimeoutError, BadRequestError, OpenAI, RateLimitError

from .usage import CostSummary, RequestContext, UsageEntry, UsageLedger, active_usage_scope, usage_scope

_REQUEST_SCOPE: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar("corecoder_request_scope", default=None)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str | None = None
    arguments_error: str | None = None


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def message(self) -> dict:
        """Convert to OpenAI message format for appending to history."""
        msg: dict = {"role": "assistant", "content": self.content if self.content or not self.tool_calls else None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.raw_arguments if tc.raw_arguments is not None else json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


# pricing per million tokens: (input, output)
# sources: openai.com/api/pricing, api-docs.deepseek.com, platform.claude.com,
#          platform.moonshot.ai, alibabacloud.com/help/en/model-studio
_PRICING = {
    # OpenAI - current flagships
    "gpt-5.5": (5, 30),
    "gpt-5.4": (2.5, 15),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.2, 1.25),
    "o4-mini": (1.1, 4.4),
    # OpenAI - previous gen (still widely used)
    "gpt-4.1": (2, 8),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10),
    "gpt-4o-mini": (0.15, 0.6),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # Anthropic Claude
    "claude-opus-4-6": (5, 25),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    # Alibaba Qwen
    "qwen3-max": (0.78, 3.9),
    "qwen3-plus": (0.26, 0.78),
    "qwen-max": (0.78, 3.9),
    # Moonshot Kimi
    "kimi-k3": (3, 15),
    "kimi-k2.5": (0.6, 3),
}


def _safe_endpoint(url: str | None) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        # Query strings and fragments routinely contain API keys or tokens.
        return urlunsplit((parts.scheme, host, parts.path, "", "")) if parts.scheme else url.split("?", 1)[0].split("#", 1)[0]
    except ValueError:
        return "invalid-endpoint"


def _pricing_for(provider: str, endpoint: str, model: str, overrides=None):
    table = overrides or _PRICING
    for key in ((provider, endpoint, model), (endpoint, model)):
        if key in table:
            return table[key]
    return table.get(model) if not endpoint else None


def _adapt_rejected_param(params: dict, exc: BadRequestError) -> bool:
    """Translate or drop one parameter a provider rejected with a 400.

    Newer OpenAI models (gpt-5, o-series) accept only ``max_completion_tokens``
    and the default temperature; the 400 message quotes the offender
    (``'max_tokens'``). Quoted matching is load-bearing: the rejection text
    for ``max_completion_tokens`` must not re-trigger the translation.
    Returns False when nothing recognizable was rejected.
    """
    message = str(exc).lower()
    if "'max_tokens'" in message and "max_tokens" in params:
        params["max_completion_tokens"] = params.pop("max_tokens")
        return True
    if "'temperature'" in message and "temperature" in params:
        params.pop("temperature")
        return True
    return False


class LLM:
    def run_scope(self):
        """Context manager yielding an isolated, nested usage view."""
        return usage_scope()

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        **kwargs,
    ):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        # Keep the provider's actual completion cap explicit so request
        # budgeting can reserve exactly what this client sends.
        if "max_tokens" not in kwargs and "max_completion_tokens" not in kwargs:
            kwargs["max_tokens"] = 4096
        self._pricing = kwargs.pop("pricing", None)
        self.extra = kwargs  # temperature, max_tokens, etc.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self._usage = UsageLedger()
        self.provider = "openai"
        self.endpoint = _safe_endpoint(base_url)

    @property
    def estimated_cost(self) -> float | None:
        summary = self.cost_summary
        if summary.unknown_requests or summary.missing_usage_requests:
            return None
        ledger = self._ledger
        return float(summary.known_cost) if ledger.entries else 0.0

    @property
    def total_prompt_tokens(self) -> int:
        return sum(e.prompt_tokens for e in self._ledger.entries)

    @total_prompt_tokens.setter
    def total_prompt_tokens(self, value: int) -> None:
        if int(value):
            raise ValueError("aggregate token counters are ledger-derived; use import_legacy_usage()")

    @property
    def total_completion_tokens(self) -> int:
        return sum(e.completion_tokens for e in self._ledger.entries)

    @total_completion_tokens.setter
    def total_completion_tokens(self, value: int) -> None:
        if int(value):
            raise ValueError("aggregate token counters are ledger-derived; use import_legacy_usage()")

    @property
    def _ledger(self):
        # A few legacy callers construct LLM with __new__ to test pricing.
        ledger = self.__dict__.get("_usage")
        if ledger is None:
            ledger = UsageLedger()
            self.__dict__["_usage"] = ledger
        return ledger

    @property
    def usage_ledger(self):
        return self._ledger.entries

    @property
    def cost_summary(self) -> CostSummary:
        return self._ledger.summary()

    def usage_summary(self, request_ids=None) -> CostSummary:
        """Return an isolated run summary by request id set."""
        return self._ledger.summary(request_ids)

    def _request_context(self) -> RequestContext:
        pricing = _pricing_for(self.provider, self.endpoint, self.model, getattr(self, "_pricing", None))
        return RequestContext.new(
            provider=self.provider, endpoint=self.endpoint, model=self.model,
            input_price=Decimal(str(pricing[0])) if pricing else None,
            output_price=Decimal(str(pricing[1])) if pricing else None,
        )

    def _record_usage(self, context: RequestContext, attempt_id: str, prompt: int, completion: int,
                      known: bool, *, cached: int = 0, reasoning: int = 0) -> None:
        cost = None
        if known and context.input_price is not None and context.output_price is not None:
            cost = (Decimal(prompt) * context.input_price + Decimal(completion) * context.output_price) / Decimal(1_000_000)
        entry = UsageEntry(
            request_id=context.request_id, attempt_id=attempt_id,
            provider=context.provider, endpoint=context.endpoint, model=context.model,
            prompt_tokens=prompt, completion_tokens=completion,
            cached_tokens=cached, reasoning_tokens=reasoning,
            usage_known=known, input_price=context.input_price,
            output_price=context.output_price, currency=context.currency,
            rate_version=context.rate_version, rate_sampled_at=context.rate_sampled_at,
            estimated_cost=cost,
        )
        self._ledger.append(entry)
        scope = active_usage_scope()
        if scope is not None:
            scope.record(entry)

    def import_legacy_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Import an old aggregate without assigning it today's model price."""
        context = RequestContext.new(
            provider=getattr(self, "provider", "legacy"),
            endpoint=getattr(self, "endpoint", ""), model=getattr(self, "model", "legacy"),
            input_price=None, output_price=None, rate_version="legacy",
        )
        self._record_usage(context, "legacy", int(prompt_tokens), int(completion_tokens), True)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        on_reasoning=None,
    ) -> LLMResponse:
        """Send messages, stream back response, handle tool calls.

        on_reasoning, when given, receives reasoning_content deltas from
        thinking models (deepseek-reasoner, kimi-k2-thinking, ...). Reasoning
        is display-only: it never enters the history, since providers reject
        it when sent back.
        """
        context = self._request_context()
        scope_token = _REQUEST_SCOPE.set(context)
        params: dict = {
            "model": context.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools

        # Provider-dialect fallbacks. A 400 that quotes a parameter is adapted
        # (newer OpenAI models take max_completion_tokens, default temperature
        # only); a 400 that names nothing drops stream_options once, matching
        # the previous single-fallback behavior for servers that reject the
        # OpenAI extension silently. The loop is bounded by the param set, and
        # an unrecognized 400 re-raises instead of doubling _call_with_retry's
        # exhausted retries. LiteLLM never lands here: drop_params strips
        # unsupported keys on that path.
        params["stream_options"] = {"include_usage": True}
        last_err = None
        try:
          for attempt in range(3):
            attempt_id = f"{context.request_id}:{attempt + 1}"
            while True:
              try:
                stream = self._call_with_retry(params)
                break
              except BadRequestError as e:
                if _adapt_rejected_param(params, e):
                    continue
                if "stream_options" not in params:
                    raise
                params.pop("stream_options")
            try:
                return self._drain(stream, on_token, on_reasoning, _context=context, _attempt_id=attempt_id)
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                last_err = e
            except APIError as e:
                # retry 5xx mid-stream drops but not 4xx
                status_code = getattr(e, "status_code", None)
                if not status_code or status_code < 500:
                    raise
                last_err = e
            # The stream died partway through. Nothing has been executed yet,
            # so re-issuing the whole request is safe; whatever partial text
            # on_token already showed is simply regenerated. Create-time
            # transients stay inside _call_with_retry and do not land here.
            if attempt < 2:
                time.sleep(2**attempt)
          raise last_err
        finally:
          _REQUEST_SCOPE.reset(scope_token)

    def _drain(self, stream, on_token, on_reasoning, _context=None, _attempt_id=None) -> LLMResponse:
        """Consume a stream and always close/account it, including malformed chunks."""
        context = _context or self._request_context()
        attempt_id = _attempt_id or f"{context.request_id}:1"
        content_parts: list[str] = []
        tc_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        prompt_tok = completion_tok = 0
        usage_seen = False
        cached_tok = reasoning_tok = 0
        try:
            for chunk in stream:
                # usage info comes in the final chunk; getattr-style reads stay safe
                # across OpenAI SDK objects and litellm's provider-varying shapes
                usage = getattr(chunk, "usage", None)
                if usage:
                    usage_seen = True
                    # some providers send usage with null fields; coerce to 0 so the
                    # running totals below don't blow up on int + None
                    # Providers may repeat usage chunks; each one is cumulative.
                    prompt_tok = getattr(usage, "prompt_tokens", 0) or 0
                    completion_tok = getattr(usage, "completion_tokens", 0) or 0
                    prompt_details = getattr(usage, "prompt_tokens_details", None)
                    completion_details = getattr(usage, "completion_tokens_details", None)
                    cached_tok = (getattr(prompt_details, "cached_tokens", 0) or 0) if prompt_details else 0
                    reasoning_tok = (getattr(completion_details, "reasoning_tokens", 0) or 0) if completion_details else 0

                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta

                # thinking models stream their chain-of-thought separately; show
                # it when someone is listening, but never mix it into the reply.
                # reasoning_content is the DeepSeek/Kimi dialect, reasoning is
                # what aggregators like OpenRouter normalize it to
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning and on_reasoning:
                    on_reasoning(reasoning)

                # accumulate text
                if getattr(delta, "content", None):
                    content_parts.append(delta.content)
                    if on_token:
                        on_token(delta.content)

                # accumulate tool calls across chunks
                if getattr(delta, "tool_calls", None):
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tc_map:
                            tc_map[idx] = {"id": "", "name": "", "args": ""}
                        if tc_delta.id:
                            tc_map[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tc_map[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                tc_map[idx]["args"] += tc_delta.function.arguments

            # parse accumulated tool calls
            parsed: list[ToolCall] = []
            for idx in sorted(tc_map):
                raw = tc_map[idx]
                try:
                    args = json.loads(raw["args"])
                    error = None
                except (json.JSONDecodeError, TypeError) as exc:
                    args = {}
                    error = str(exc)
                parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args,
                                       raw_arguments=raw["args"], arguments_error=error))

            return LLMResponse(
                content="".join(content_parts),
                tool_calls=parsed,
                prompt_tokens=prompt_tok,
                completion_tokens=completion_tok,
            )
        finally:
            self._record_usage(context, attempt_id, prompt_tok, completion_tok, usage_seen,
                               cached=cached_tok, reasoning=reasoning_tok)
            close = getattr(stream, "close", None)
            if close:
                close()

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError):
                context = _REQUEST_SCOPE.get()
                if context:
                    self._record_usage(context, f"{context.request_id}:create:{uuid4().hex}", 0, 0, False)
                if attempt == max_retries - 1:
                    raise
                wait = 2**attempt
                time.sleep(wait)
            except APIError as e:
                context = _REQUEST_SCOPE.get()
                if context:
                    self._record_usage(context, f"{context.request_id}:create:{uuid4().hex}", 0, 0, False)
                # retry 5xx server errors but not 4xx; base APIError has no status_code so read it defensively
                status_code = getattr(e, "status_code", None)
                if status_code and status_code >= 500 and attempt < max_retries - 1:
                    time.sleep(2**attempt)
                else:
                    raise


class LiteLLM(LLM):
    """LLM backend via LiteLLM, supporting 100+ providers.

    Use this when your target provider is NOT OpenAI-compatible
    (AWS Bedrock, Google Vertex, Cohere, etc.) or when you want
    a single interface to switch between any provider by changing
    the model string.

    Set CORECODER_PROVIDER=litellm and use LiteLLM model strings
    like ``anthropic/claude-3-haiku``, ``bedrock/anthropic.claude-v2``,
    ``vertex_ai/gemini-pro``, etc.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs,
    ):
        # skip LLM.__init__ which creates an OpenAI client
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        if "max_tokens" not in kwargs and "max_completion_tokens" not in kwargs:
            kwargs["max_tokens"] = 4096
        self._pricing = kwargs.pop("pricing", None)
        self.extra = kwargs
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.provider = "litellm"
        self.endpoint = _safe_endpoint(base_url)
        self._usage = UsageLedger()

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff via litellm."""
        import litellm

        params["drop_params"] = True
        if self.api_key:
            params["api_key"] = self.api_key
        if self.base_url:
            params["api_base"] = self.base_url

        for attempt in range(max_retries):
            try:
                return litellm.completion(**params)
            except Exception as e:
                err = str(e).lower()
                is_transient = any(kw in err for kw in ["rate_limit", "timeout", "connection", "502", "503", "529"])
                is_server = any(kw in err for kw in ["500", "502", "503", "504"])
                context = _REQUEST_SCOPE.get()
                if context:
                    self._record_usage(context, f"{context.request_id}:create:{uuid4().hex}", 0, 0, False)
                if (is_transient or is_server) and attempt < max_retries - 1:
                    time.sleep(2**attempt)
                else:
                    raise
