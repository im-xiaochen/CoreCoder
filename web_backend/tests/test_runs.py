from web_backend.run_store import RunStore


def test_run_journal_recovers_truncated_tail_and_uncommitted_metadata(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    rid = store.create('session')
    store.append(rid, {'type': 'token', 'text': 'first'})
    def fail(run):
        raise OSError('crash after append')
    monkeypatch.setattr(store, '_save', fail)
    try:
        store.append(rid, {'type': 'token', 'text': ' second'})
    except OSError:
        pass
    path = tmp_path / f'{rid}.jsonl'
    with path.open('ab') as f:
        f.write(b'{"broken":')
    restored = RunStore(tmp_path)
    assert restored.view(rid)['text'] == 'first second'
    assert restored.view(rid)['last_seq'] == 2
    assert [e['seq'] for e in restored.events_after(rid, 0)[1]] == [1, 2]
    restored.append(rid, {'type': 'done'})
    assert restored.view(rid)['last_seq'] == 3


def test_run_retention_gap_and_session_scoped_delete(tmp_path, monkeypatch):
    import web_backend.run_store as module
    monkeypatch.setattr(module, 'MAX_EVENTS', 4)
    store = RunStore(tmp_path)
    a, b = store.create('a'), store.create('b')
    for index in range(8):
        store.append(a, {'type': 'token', 'text': str(index)})
    gap, events, _ = store.events_after(a, 0)
    assert gap and events[-1]['seq'] == 8
    assert store.view(a)['text'] == '01234567'
    store.delete_session('a')
    assert store.view(b)['session_id'] == 'b'
    assert not (tmp_path / f'{a}.json').exists()
