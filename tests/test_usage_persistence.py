from decimal import Decimal

import pytest

from corecoder.llm import LLM
from corecoder.usage import UsageEntry, deserialize_usage, serialize_usage


def test_ledger_roundtrip_and_atomic_restore():
    entries = [UsageEntry('r', 'a', 'p', 'e', 'm', prompt_tokens=1000, completion_tokens=100, input_price=Decimal(1), output_price=Decimal(2), estimated_cost=Decimal('0.0012')), UsageEntry('r2', 'a2', 'p', 'e2', 'm2', usage_known=False)]
    payload = serialize_usage(entries)
    llm = LLM.__new__(LLM)
    llm.restore_usage(payload + payload)
    assert llm.usage_ledger == tuple(entries)
    assert llm.cost_summary.known_cost == Decimal('0.0012')
    assert llm.cost_summary.missing_usage_requests == 1
    broken = [dict(payload[0]), dict(payload[0], prompt_tokens=2)]
    with pytest.raises(ValueError):
        llm.restore_usage(broken)
    assert llm.usage_ledger == tuple(entries)
    with pytest.raises(ValueError):
        deserialize_usage([dict(payload[0], estimated_cost='NaN')])

def test_old_schema_rejects_bad_ledger():
    from corecoder.validation import validate_session
    assert not validate_session({'schema_version': 2, 'id': 'x', 'model': 'm', 'messages': [], 'usage_ledger': [{}]})
