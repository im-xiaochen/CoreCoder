"""Thread-safe per-run reservations for physical model attempts."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .usage import UsageEntry


class BudgetExceeded(Exception):
    """A model attempt cannot be reserved within the run budget."""


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _decimal(value, name):
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return result


@dataclass(frozen=True)
class BudgetLimits:
    requests: int | None = None
    total_tokens: int | None = None
    cost: str | None = None
    unknown_policy: str = "deny_when_capped"

    def __post_init__(self):
        for name in ("requests", "total_tokens"):
            value = getattr(self, name)
            if value is not None:
                _integer(value, name)
        if self.cost is not None:
            _decimal(self.cost, "cost")
        if self.unknown_policy not in {"deny_when_capped", "allow_unknown"}:
            raise ValueError("Invalid unknown policy")


class RunBudget:
    def __init__(self, limits=None, on_change=None):
        self._checkpoint_lock = threading.RLock()
        self._on_change = on_change
        self.limits = limits or BudgetLimits()
        self._lock = threading.Lock()
        self._attempts = {}

    @staticmethod
    def _cost(tokens, rates):
        if rates is None:
            return None
        if not isinstance(rates, dict) or set(rates) != {"input", "output"}:
            raise ValueError("Rates must contain input and output decimal strings")
        return (Decimal(tokens["input_tokens"]) * _decimal(rates["input"], "input rate")
                + Decimal(tokens["output_tokens"]) * _decimal(rates["output"], "output rate")) / Decimal(1_000_000)

    def reserve(self, attempt_id, request, rates=None):
        with self._checkpoint_lock:
            with self._lock:
                existed = attempt_id in self._attempts
            result = self._reserve(attempt_id, request, rates)
            try:
                if self._on_change is not None:
                    self._on_change(self.snapshot())
            except Exception:
                if not existed:
                    with self._lock:
                        self._attempts[attempt_id]["state"] = "released"
                raise
            return result

    def _reserve(self, attempt_id, request, rates=None):
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ValueError("attempt_id must be a nonempty string")
        if not isinstance(request, dict) or set(request) != {"input_tokens", "output_tokens"}:
            raise ValueError("Invalid request estimate")
        tokens = {k: _integer(request[k], k) for k in ("input_tokens", "output_tokens")}
        cost = self._cost(tokens, rates)
        with self._lock:
            if attempt_id in self._attempts:
                prior = self._attempts[attempt_id]
                if prior["state"] == "in_flight" and prior["input_tokens"] == tokens["input_tokens"] and prior["output_tokens"] == tokens["output_tokens"] and prior["rates"] == rates:
                    return {"attempt_id": attempt_id, "tokens": prior["tokens"],
                            "cost": str(prior["cost"]) if prior["cost"] is not None else None}
                raise ValueError("Conflicting or settled attempt ID")
            active = [a for a in self._attempts.values() if a["state"] != "released"]
            if self.limits.requests is not None and len(active) >= self.limits.requests:
                raise BudgetExceeded("request limit")
            total = sum(a["tokens"] for a in active) + sum(tokens.values())
            if self.limits.total_tokens is not None and total > self.limits.total_tokens:
                raise BudgetExceeded("token limit")
            if self.limits.cost is not None:
                if cost is None and self.limits.unknown_policy == "deny_when_capped":
                    raise BudgetExceeded("unknown cost")
                known = sum((a["cost"] or Decimal(0) for a in active), Decimal(0))
                if known + (cost or Decimal(0)) > _decimal(self.limits.cost, "cost"):
                    raise BudgetExceeded("cost limit")
            self._attempts[attempt_id] = {"state": "in_flight", "tokens": sum(tokens.values()),
                                          "input_tokens": tokens["input_tokens"], "output_tokens": tokens["output_tokens"],
                                          "cost": cost, "rates": dict(rates) if rates is not None else None}
            return {"attempt_id": attempt_id, "tokens": sum(tokens.values()),
                    "cost": str(cost) if cost is not None else None}

    def settle(self, attempt_id, usage_or_unknown=None, *, sent=True):
        with self._checkpoint_lock:
            self._settle(attempt_id, usage_or_unknown, sent=sent)
            if self._on_change is not None:
                self._on_change(self.snapshot())

    def _settle(self, attempt_id, usage_or_unknown=None, *, sent=True):
        with self._lock:
            attempt = self._attempts[attempt_id]
            if attempt["state"] != "in_flight":
                return
            if type(sent) is not bool or (not sent and usage_or_unknown is not None):
                raise ValueError("Invalid sent status")
            if not sent:
                attempt["state"] = "released"
            elif usage_or_unknown is None:
                attempt["state"] = "unconfirmed"
            else:
                entry = usage_or_unknown
                if not isinstance(entry, UsageEntry) or entry.attempt_id != attempt_id or not entry.usage_known:
                    raise ValueError("Usage entry must match the attempt and contain known usage")
                prompt = _integer(entry.prompt_tokens, "prompt_tokens")
                completion = _integer(entry.completion_tokens, "completion_tokens")
                cost = entry.estimated_cost
                actual_cost = _decimal(str(cost), "estimated_cost") if cost is not None else None
                attempt["tokens"] = prompt + completion
                attempt["cost"] = actual_cost
                attempt["state"] = "actual"

    def snapshot(self):
        with self._lock:
            attempts = {key: {**value, "rates": dict(value["rates"]) if value["rates"] is not None else None,
                              "cost": str(value["cost"]) if value["cost"] is not None else None}
                        for key, value in self._attempts.items()}
            groups = {}
            for state in ("actual", "in_flight", "unconfirmed"):
                items = [a for a in self._attempts.values() if a["state"] == state]
                groups[state] = {"requests": len(items), "tokens": sum(a["tokens"] for a in items),
                                 "known_cost": str(sum((a["cost"] or Decimal(0) for a in items), Decimal(0))),
                                 "unknown_cost_requests": sum(a["cost"] is None for a in items)}
            active = [a for a in self._attempts.values() if a["state"] != "released"]
            tokens = sum(a["tokens"] for a in active)
            known = sum((a["cost"] or Decimal(0) for a in active), Decimal(0))
            unknown = sum(a["cost"] is None for a in active)
            cost_cap = _decimal(self.limits.cost, "cost") if self.limits.cost is not None else None
            report = {
                "requests_remaining": None if self.limits.requests is None else max(0, self.limits.requests - len(active)),
                "tokens_remaining": None if self.limits.total_tokens is None else max(0, self.limits.total_tokens - tokens),
                "tokens_overage": 0 if self.limits.total_tokens is None else max(0, tokens - self.limits.total_tokens),
                "cost_remaining": None if cost_cap is None or unknown else str(max(Decimal(0), cost_cap - known)),
                "cost_overage_known": None if cost_cap is None else str(max(Decimal(0), known - cost_cap)),
                "unknown_cost_requests": unknown,
            }
            return {"report": report, "groups": groups, "limits": {"requests": self.limits.requests, "total_tokens": self.limits.total_tokens,
                               "cost": self.limits.cost, "unknown_policy": self.limits.unknown_policy},
                    "attempts": attempts}

    @classmethod
    def restore(cls, snapshot):
        if not isinstance(snapshot, dict) or set(snapshot) != {"limits", "attempts", "groups", "report"}:
            raise ValueError("Invalid budget snapshot")
        if not isinstance(snapshot["limits"], dict) or set(snapshot["limits"]) != {"requests", "total_tokens", "cost", "unknown_policy"}:
            raise ValueError("Invalid saved limits")
        if not isinstance(snapshot["attempts"], dict):
            raise TypeError("Invalid saved attempts")
        budget = cls(BudgetLimits(**snapshot["limits"]))
        for key, value in snapshot["attempts"].items():
            if not isinstance(key, str) or not key or not isinstance(value, dict) or set(value) != {"state", "tokens", "input_tokens", "output_tokens", "cost", "rates"}:
                raise ValueError("Invalid saved attempt")
            if value["state"] not in {"actual", "in_flight", "unconfirmed", "released"}:
                raise ValueError("Invalid saved attempt state")
            _integer(value["tokens"], "tokens")
            _integer(value["input_tokens"], "input_tokens")
            _integer(value["output_tokens"], "output_tokens")
            if value["state"] in {"in_flight", "unconfirmed", "released"} and value["tokens"] != value["input_tokens"] + value["output_tokens"]:
                raise ValueError("Invalid saved reservation totals")
            item = dict(value)
            if item["rates"] is not None:
                cls._cost({"input_tokens": 0, "output_tokens": 0}, item["rates"])
                item["rates"] = dict(item["rates"])
            item["cost"] = _decimal(item["cost"], "cost") if item["cost"] is not None else None
            if item["state"] == "in_flight":
                item["state"] = "unconfirmed"
            budget._attempts[key] = item
        return budget
