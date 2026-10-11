"""Immutable, thread-safe request usage and cost accounting."""

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from typing import Any
from uuid import uuid4


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class UsageEntry:
    request_id: str
    attempt_id: str
    provider: str
    endpoint: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    usage_known: bool = True
    input_price: Decimal | None = None
    output_price: Decimal | None = None
    currency: str = "USD"
    rate_version: str | None = None
    rate_sampled_at: str | None = None
    estimated_cost: Decimal | None = None


@dataclass(frozen=True)
class CostSummary:
    known_cost: Decimal = Decimal(0)
    unknown_prompt_tokens: int = 0
    unknown_completion_tokens: int = 0
    unknown_requests: int = 0
    missing_usage_requests: int = 0

    # Handy compatibility view for callers that used a mapping in prototypes.
    def __getitem__(self, key: str):
        return getattr(self, key)

    def as_dict(self) -> dict[str, Any]:
        return {
            "known_cost": self.known_cost,
            "unknown_prompt_tokens": self.unknown_prompt_tokens,
            "unknown_completion_tokens": self.unknown_completion_tokens,
            "unknown_requests": self.unknown_requests,
            "missing_usage_requests": self.missing_usage_requests,
        }


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    provider: str
    endpoint: str
    model: str
    input_price: Decimal | None
    output_price: Decimal | None
    currency: str = "USD"
    rate_version: str | None = None
    rate_sampled_at: str | None = None

    @classmethod
    def new(cls, **kwargs: Any) -> "RequestContext":
        kwargs.setdefault("rate_sampled_at", datetime.now(timezone.utc).isoformat())
        return cls(request_id=kwargs.pop("request_id", uuid4().hex), **kwargs)


class UsageLedger:
    """Append-only ledger. Entries are copied into an immutable tuple on read."""

    def __init__(self):
        self._entries: list[UsageEntry] = []
        self._keys: set[tuple[str, str]] = set()
        self._lock = RLock()

    def append(self, entry: UsageEntry) -> None:
        with self._lock:
            key = (entry.request_id, entry.attempt_id)
            if key in self._keys:
                return
            self._entries.append(entry)
            self._keys.add(key)

    @property
    def entries(self) -> tuple[UsageEntry, ...]:
        with self._lock:
            return tuple(self._entries)

    def summary(self, request_ids=None) -> CostSummary:
        with self._lock:
            known = Decimal(0)
            up = uc = unknown = missing = 0
            request_ids = None if request_ids is None else set(request_ids)
            selected = self._entries if request_ids is None else [e for e in self._entries if e.request_id in request_ids]
            for entry in selected:
                if not entry.usage_known:
                    missing += 1
                elif entry.input_price is None or entry.output_price is None:
                    unknown += 1
                    up += entry.prompt_tokens
                    uc += entry.completion_tokens
                elif entry.estimated_cost is not None:
                    known += entry.estimated_cost
            return CostSummary(known, up, uc, unknown, missing)

    def entries_for(self, request_ids) -> tuple[UsageEntry, ...]:
        ids = set(request_ids)
        with self._lock:
            return tuple(e for e in self._entries if e.request_id in ids)


_SCOPE_STACK: contextvars.ContextVar[tuple["RunUsageScope", ...]] = contextvars.ContextVar("corecoder_usage_scopes", default=())


class RunUsageScope:
    """Per-run view; nested scopes share storage with their parent."""
    def __init__(self, parent=None):
        self.parent = parent
        self._entries = parent._entries if parent is not None else []
        self.request_ids: set[str] = set()
        self._lock = parent._lock if parent is not None else RLock()

    def record(self, entry: UsageEntry) -> None:
        with self._lock:
            key = (entry.request_id, entry.attempt_id)
            if any((e.request_id, e.attempt_id) == key for e in self._entries):
                return
            self._entries.append(entry)
            scope = self
            while scope is not None:
                scope.request_ids.add(entry.request_id)
                scope = scope.parent

    def summary(self) -> CostSummary:
        with self._lock:
            ids = set(self.request_ids)
            entries = tuple(self._entries)
        known = Decimal(0); up = uc = unknown = missing = 0
        for entry in entries:
            if entry.request_id not in ids:
                continue
            if not entry.usage_known: missing += 1
            elif entry.input_price is None or entry.output_price is None:
                unknown += 1; up += entry.prompt_tokens; uc += entry.completion_tokens
            elif entry.estimated_cost is not None: known += entry.estimated_cost
        return CostSummary(known, up, uc, unknown, missing)

    def as_dict(self) -> dict[str, object]:
        summary = self.summary()
        result = summary.as_dict()
        result["known_cost"] = str(summary.known_cost)
        result["request_ids"] = sorted(self.request_ids)
        result["prompt_tokens"] = sum(e.prompt_tokens for e in self._entries if e.request_id in self.request_ids)
        result["completion_tokens"] = sum(e.completion_tokens for e in self._entries if e.request_id in self.request_ids)
        return result


def active_usage_scope() -> RunUsageScope | None:
    stack = _SCOPE_STACK.get()
    return stack[-1] if stack else None


@contextmanager
def usage_scope():
    parent = active_usage_scope()
    scope = RunUsageScope(parent)
    token = _SCOPE_STACK.set((*_SCOPE_STACK.get(), scope))
    try:
        yield scope
    finally:
        _SCOPE_STACK.reset(token)


@contextmanager
def request_scope(request_id: str | None = None):
    """Small run-scope hook for Agent/tool_runtime request attribution."""
    with usage_scope() as scope:
        yield scope
