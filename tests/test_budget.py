from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from corecoder.budget import BudgetExceeded, BudgetLimits, RunBudget
from corecoder.usage import UsageEntry


def entry(attempt, prompt, completion, cost):
    return UsageEntry("request", attempt, "test", "endpoint", "model", prompt_tokens=prompt,
                      completion_tokens=completion, estimated_cost=Decimal(cost) if cost is not None else None)


def request():
    return {"input_tokens": 2, "output_tokens": 3}


def test_concurrent_last_reservation():
    budget = RunBudget(BudgetLimits(requests=1))
    def reserve(i):
        try:
            budget.reserve(str(i), request())
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 1


def test_zero_validation_and_decimal():
    with pytest.raises(BudgetExceeded):
        RunBudget(BudgetLimits(total_tokens=0)).reserve("x", request())
    for value in ("NaN", "Infinity", "-1"):
        with pytest.raises(ValueError):
            BudgetLimits(cost=value)
    with pytest.raises(ValueError):
        BudgetLimits(requests=True)
    budget = RunBudget(BudgetLimits(cost="0.5"))
    budget.reserve("a", request(), {"input": "100000", "output": "100000"})
    with pytest.raises(BudgetExceeded):
        budget.reserve("b", request(), {"input": "100000", "output": "100000"})


def test_unknown_settle_restore_and_release():
    budget = RunBudget(BudgetLimits(cost="1"))
    with pytest.raises(BudgetExceeded):
        budget.reserve("x", request())
    budget = RunBudget(BudgetLimits(requests=1, unknown_policy="allow_unknown", cost="1"))
    budget.reserve("x", request())
    budget.settle("x", None, sent=False)
    budget.reserve("y", request())
    budget.settle("y")
    budget.settle("y", sent=False)
    restored = RunBudget.restore(budget.snapshot())
    assert restored.snapshot()["attempts"]["y"]["state"] == "unconfirmed"
    with pytest.raises(BudgetExceeded):
        restored.reserve("z", request())


def test_actual_overage_blocks_future():
    budget = RunBudget(BudgetLimits(total_tokens=5))
    budget.reserve("x", request())
    budget.settle("x", entry("x", 5, 5, None))
    with pytest.raises(BudgetExceeded):
        budget.reserve("y", {"input_tokens": 0, "output_tokens": 0})


def test_grouped_snapshot_and_restored_inflight():
    budget = RunBudget()
    budget.reserve("actual", request(), {"input": "0.1", "output": "0.2"})
    budget.settle("actual", entry("actual", 1, 2, "0.5"))
    budget.reserve("unknown", request())
    budget.settle("unknown")
    budget.reserve("flying", request(), {"input": "0.1", "output": "0.2"})
    snapshot = budget.snapshot()
    assert snapshot["groups"]["actual"]["known_cost"] == "0.5"
    assert snapshot["groups"]["unconfirmed"]["unknown_cost_requests"] == 1
    assert snapshot["groups"]["in_flight"]["tokens"] == 5
    assert RunBudget.restore(snapshot).snapshot()["groups"]["unconfirmed"]["requests"] == 2


def test_restore_validation_and_rate_copy():
    budget = RunBudget()
    rates = {"input": "2", "output": "3"}
    first = budget.reserve("id", request(), rates)
    assert budget.reserve("id", request(), rates) == first
    rates["input"] = "999"
    snap = budget.snapshot()
    snap["attempts"]["id"]["rates"]["input"] = "888"
    assert budget.snapshot()["attempts"]["id"]["rates"]["input"] == "2"
    snap["attempts"]["id"]["tokens"] = -1
    with pytest.raises(ValueError):
        RunBudget.restore(snap)


def test_report_unknown_and_cached_actual_cost():
    budget = RunBudget(BudgetLimits(cost="1", total_tokens=5, unknown_policy="allow_unknown"))
    budget.reserve("x", request(), {"input": "10", "output": "20"})
    actual = UsageEntry("req", "x", "provider", "endpoint", "model", prompt_tokens=4,
                        completion_tokens=3, cached_tokens=3, estimated_cost=Decimal("0.00001"))
    budget.settle("x", actual)
    report = budget.snapshot()
    assert report["groups"]["actual"]["known_cost"] == "0.00001"
    assert report["report"]["tokens_overage"] == 2
    budget = RunBudget(BudgetLimits(cost="1", unknown_policy="allow_unknown"))
    budget.reserve("unknown", request())
    assert budget.snapshot()["report"]["cost_remaining"] is None


def test_checkpoint_before_send_and_restore(tmp_path):
    from web_backend.run_store import RunStore
    store = RunStore(tmp_path)
    rid = store.create("session")
    budget = RunBudget(BudgetLimits(requests=2), on_change=lambda snap: store.budget(rid, snap))
    budget.reserve("attempt", request())
    reloaded = RunStore(tmp_path).view(rid)["budget"]
    assert reloaded["groups"]["in_flight"]["requests"] == 1
    restored = RunBudget.restore(reloaded).snapshot()
    assert restored["groups"]["unconfirmed"]["requests"] == 1


def test_failed_checkpoint_prevents_return_from_reserve():
    def fail(snapshot):
        raise OSError("disk failed")
    budget = RunBudget(on_change=fail)
    with pytest.raises(OSError):
        budget.reserve("attempt", request())
