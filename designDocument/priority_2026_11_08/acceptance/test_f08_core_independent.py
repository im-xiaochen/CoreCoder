"""Run-budget contract checks, independent of transport and developer tests."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from corecoder.budget import BudgetExceeded, BudgetLimits, RunBudget
from corecoder.usage import UsageEntry


def estimate(prompt=10, completion=5):
    return {"input_tokens": prompt, "output_tokens": completion}


def usage(attempt, prompt=3, completion=2, cost="0.123456789"):
    return UsageEntry(request_id="independent-request", attempt_id=attempt,
                      provider="offline", endpoint="fixture", model="fixture-model",
                      prompt_tokens=prompt, completion_tokens=completion,
                      input_price=Decimal("91"), output_price=Decimal("83"),
                      estimated_cost=None if cost is None else Decimal(cost))


def test_parallel_shared_last_request_has_exactly_one_winner():
    budget = RunBudget(BudgetLimits(requests=1, total_tokens=15, cost="1"))
    gate = Barrier(16)

    def child(number):
        gate.wait(timeout=5)
        try:
            budget.reserve(f"child-{number}", estimate(), {"input": "1", "output": "2"})
            return "reserved"
        except BudgetExceeded:
            return "denied"

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(child, range(16)))
    assert results.count("reserved") == 1 and results.count("denied") == 15
    snapshot = budget.snapshot()
    assert snapshot["groups"]["in_flight"]["requests"] == 1
    assert snapshot["report"]["requests_remaining"] == 0
    assert snapshot["report"]["tokens_remaining"] == 0


@pytest.mark.parametrize("limits", [BudgetLimits(requests=0), BudgetLimits(total_tokens=0), BudgetLimits(cost="0")])
def test_zero_caps_reject_positive_reservation_without_state_mutation(limits):
    budget = RunBudget(limits)
    before = budget.snapshot()
    with pytest.raises(BudgetExceeded):
        budget.reserve("zero", estimate(), {"input": "1", "output": "2"})
    assert budget.snapshot() == before


def test_decimal_per_million_boundary_and_f01_actual_cost_is_authoritative():
    # 3 * .1 / million + 7 * .2 / million = .0000017 exactly.
    budget = RunBudget(BudgetLimits(cost="0.0000017", total_tokens=10))
    rates = {"input": "0.1", "output": "0.2"}
    reservation = budget.reserve("exact", estimate(3, 7), rates)
    assert Decimal(reservation["cost"]) == Decimal("0.0000017")
    with pytest.raises(BudgetExceeded):
        budget.reserve("extra", estimate(0, 1), rates)
    # F01 reports a different cost: never recompute it from the estimate's rates.
    budget.settle("exact", usage("exact", 2, 1, "0.0000009"))
    snapshot = budget.snapshot()
    assert snapshot["groups"]["actual"]["tokens"] == 3
    assert Decimal(snapshot["groups"]["actual"]["known_cost"]) == Decimal("0.0000009")
    assert Decimal(snapshot["report"]["cost_remaining"]) == Decimal("0.0000008")
    assert snapshot["report"]["tokens_remaining"] == 7


def test_actual_over_estimate_retained_and_blocks_next_even_if_next_free():
    budget = RunBudget(BudgetLimits(total_tokens=20, cost="0.1"))
    budget.reserve("over", estimate(), {"input": "1", "output": "2"})
    budget.settle("over", usage("over", 21, 9, "0.25"))
    snapshot = budget.snapshot()
    assert snapshot["groups"]["actual"]["tokens"] == 30
    assert snapshot["report"]["tokens_overage"] == 10
    assert Decimal(snapshot["report"]["cost_overage_known"]) == Decimal("0.15")
    assert snapshot["report"]["tokens_remaining"] == 0
    with pytest.raises(BudgetExceeded):
        budget.reserve("after-over", estimate(0, 0), {"input": "0", "output": "0"})


def test_unknown_price_default_denial_explicit_allow_and_uncertainty_report():
    strict = RunBudget(BudgetLimits(cost="1"))
    with pytest.raises(BudgetExceeded):
        strict.reserve("unknown-denied", estimate(), None)
    assert not strict.snapshot()["attempts"]
    allowed = RunBudget(BudgetLimits(requests=1, cost="1", unknown_policy="allow_unknown"))
    allowed.reserve("unknown-allowed", estimate(), None)
    allowed.settle("unknown-allowed", usage("unknown-allowed", cost=None))
    snapshot = allowed.snapshot()
    assert snapshot["groups"]["actual"]["unknown_cost_requests"] == 1
    assert snapshot["report"]["unknown_cost_requests"] == 1
    assert snapshot["report"]["cost_remaining"] is None
    assert snapshot["report"]["requests_remaining"] == 0
    uncapped = RunBudget()
    uncapped.reserve("uncapped-unknown", estimate(), None)
    assert uncapped.snapshot()["report"]["cost_remaining"] is None


def test_missing_usage_retains_reservation_but_provably_unsent_releases():
    budget = RunBudget(BudgetLimits(requests=1, total_tokens=15))
    budget.reserve("unsent", estimate())
    budget.settle("unsent", sent=False)
    assert budget.snapshot()["report"]["requests_remaining"] == 1
    budget.reserve("sent-missing", estimate())
    budget.settle("sent-missing", None)
    snapshot = budget.snapshot()
    assert snapshot["groups"]["unconfirmed"]["requests"] == 1
    assert snapshot["groups"]["unconfirmed"]["tokens"] == 15
    assert snapshot["report"]["requests_remaining"] == 0
    with pytest.raises(BudgetExceeded):
        budget.reserve("third", estimate(1, 0))


def test_identical_reserve_and_repeated_settle_do_not_double_count():
    budget = RunBudget(BudgetLimits(requests=1))
    rates = {"input": "1", "output": "2"}
    first = budget.reserve("once", estimate(), rates)
    assert budget.reserve("once", estimate(), dict(rates)) == first
    assert budget.snapshot()["groups"]["in_flight"]["requests"] == 1
    budget.settle("once", usage("once"))
    final = budget.snapshot()
    budget.settle("once", usage("once"))
    assert budget.snapshot() == final
    with pytest.raises(ValueError):
        budget.reserve("once", estimate(), rates)


def test_duplicate_id_changed_input_output_split_is_not_identical_request():
    budget = RunBudget(BudgetLimits(cost="0.0001"))
    rates = {"input": "1", "output": "10"}
    budget.reserve("same-id", estimate(10, 1), rates)
    before = budget.snapshot()
    with pytest.raises(ValueError):
        budget.reserve("same-id", estimate(1, 10), rates)
    assert budget.snapshot() == before


def test_snapshot_and_restore_detach_nested_rates_and_recover_inflight_as_unknown():
    budget = RunBudget(BudgetLimits(requests=2, cost="1"))
    rates = {"input": "0.1", "output": "0.2"}
    budget.reserve("done", estimate(), rates)
    budget.settle("done", usage("done", cost="0.01"))
    budget.reserve("at-crash", estimate(), rates)
    rates["input"] = "999"
    snapshot = budget.snapshot()
    assert snapshot["attempts"]["at-crash"]["rates"]["input"] == "0.1"
    damaged_copy = copy.deepcopy(snapshot)
    damaged_copy["attempts"]["at-crash"]["rates"]["input"] = "88"
    assert budget.snapshot() == snapshot
    recovered = RunBudget.restore(json.loads(json.dumps(snapshot)))
    assert recovered.snapshot()["groups"]["actual"]["requests"] == 1
    assert recovered.snapshot()["groups"]["in_flight"]["requests"] == 0
    assert recovered.snapshot()["groups"]["unconfirmed"]["requests"] == 1
    assert recovered.snapshot()["report"]["requests_remaining"] == 0
    snapshot["attempts"]["at-crash"]["rates"]["input"] = "77"
    assert recovered.snapshot()["attempts"]["at-crash"]["rates"]["input"] == "0.1"
    with pytest.raises(BudgetExceeded):
        recovered.reserve("no-replay", estimate())


@pytest.mark.parametrize("bad", [-1, True, 1.2, "1"])
def test_invalid_integer_limits_rejected(bad):
    with pytest.raises((TypeError, ValueError)):
        BudgetLimits(requests=bad)
    with pytest.raises((TypeError, ValueError)):
        BudgetLimits(total_tokens=bad)


@pytest.mark.parametrize("bad", ["-0.1", "NaN", "Infinity", True, 0.1])
def test_invalid_cost_limits_rejected(bad):
    with pytest.raises((TypeError, ValueError)):
        BudgetLimits(cost=bad)


@pytest.mark.parametrize("field,value", [("tokens", -1), ("tokens", True), ("state", "invented"), ("cost", "NaN"), ("rates", {"input": "-1", "output": "2"})])
def test_invalid_saved_attempt_rejected(field, value):
    budget = RunBudget()
    budget.reserve("saved", estimate())
    snapshot = budget.snapshot()
    snapshot["attempts"]["saved"][field] = value
    with pytest.raises((TypeError, ValueError)):
        RunBudget.restore(snapshot)


def test_invalid_or_mismatched_usage_does_not_partially_mutate_reservation():
    budget = RunBudget()
    budget.reserve("valid-id", estimate())
    before = budget.snapshot()
    for invalid in [usage("wrong-id"), usage("valid-id", prompt=-1), usage("valid-id", cost="NaN")]:
        with pytest.raises(ValueError):
            budget.settle("valid-id", invalid)
        assert budget.snapshot() == before

