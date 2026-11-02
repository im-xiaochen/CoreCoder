"""Durable, bounded run event journals; subscriptions never execute work."""
import json
import os
import threading
import time
import uuid
from pathlib import Path

MAX_EVENTS = 10000
MAX_BYTES = 10 * 1024 * 1024
TERMINAL = {'completed', 'cancelled', 'failed', 'interrupted'}


class RunStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.runs = {}
        for path in self.root.glob('*.json'):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                rid = data['id']
                if path.stem != rid or len(rid) != 32 or not rid.isalnum():
                    continue
                journal = self.root / f'{rid}.jsonl'
                raw = journal.read_bytes() if journal.exists() else b''
                lines = raw.splitlines(keepends=True)
                events = []
                good_bytes = 0
                for index, line in enumerate(lines):
                    try:
                        if not line.endswith(b'\n'):
                            raise ValueError('incomplete record')
                        event = json.loads(line)
                        if not isinstance(event, dict) or type(event.get('seq')) is not int or (events and event['seq'] != events[-1]['seq'] + 1):
                            raise ValueError('invalid event sequence')
                        events.append(event)
                        good_bytes += len(line)
                    except (ValueError, UnicodeError):
                        if index != len(lines) - 1:
                            raise
                        with journal.open('r+b') as out:
                            out.truncate(good_bytes)
                saved_seq = data.get('last_seq', 0)
                for event in events:
                    if event['seq'] > saved_seq and event.get('type') in ('token', 'reasoning'):
                        key = 'text' if event['type'] == 'token' else 'reasoning'
                        data[key] = (data.get(key, '') + event.get('text', ''))[-MAX_BYTES // 4:]
                data['events'] = events
                data['last_seq'] = max(saved_seq, events[-1]['seq'] if events else 0)
                self.runs[rid] = data
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _save(self, run):
        data = {k: v for k, v in run.items() if k != 'events'}
        path = self.root / f"{run['id']}.json"
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)

    def create(self, sid):
        with self.lock:
            now = time.time()
            run = {'id': uuid.uuid4().hex, 'session_id': sid, 'status': 'running', 'created_at': now,
                   'updated_at': now, 'last_seq': 0, 'events': [], 'text': '', 'reasoning': ''}
            self._save(run)
            self.runs[run['id']] = run
            return run['id']

    def status(self, rid, status):
        with self.lock:
            run = self.runs[rid]
            run['status'] = status
            run['updated_at'] = time.time()
            self._save(run)
            self.changed.notify_all()

    def append(self, rid, event, guard=None):
        with self.lock:
            run = self.runs[rid]
            if guard is not None and (not guard.is_set() or run['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}):
                return None
            entry = {**event, 'run_id': rid, 'seq': run['last_seq'] + 1}
            encoded = json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + '\n'
            path = self.root / f'{rid}.jsonl'
            with path.open('a', encoding='utf-8') as out:
                out.write(encoded)
                out.flush()
            run['events'].append(entry)
            run['last_seq'] = entry['seq']
            run['updated_at'] = time.time()
            if event.get('type') in ('token', 'reasoning'):
                key = 'text' if event['type'] == 'token' else 'reasoning'
                run[key] = (run[key] + event.get('text', ''))[-MAX_BYTES // 4:]
            if len(run['events']) > MAX_EVENTS or path.stat().st_size > MAX_BYTES:
                retained = []
                size = 0
                for item in reversed(run['events']):
                    line = json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n'
                    n = len(line.encode('utf-8'))
                    if len(retained) >= MAX_EVENTS // 2 or size + n > MAX_BYTES // 2:
                        break
                    retained.append((item, line))
                    size += n
                retained.reverse()
                temp = path.with_suffix('.tmp')
                temp.write_text(''.join(line for _, line in retained), encoding='utf-8')
                os.replace(temp, path)
                run['events'] = [item for item, _ in retained]
            self._save(run)
            self.changed.notify_all()
            return entry

    def view(self, rid):
        with self.lock:
            run = self.runs.get(rid)
            if run is None:
                raise KeyError(rid)
            return {k: v for k, v in run.items() if k != 'events'}

    def events_after(self, rid, after):
        with self.lock:
            run = self.runs[rid]
            events = run['events']
            gap = bool(after < (events[0]['seq'] - 1 if events else run['last_seq']))
            return gap, [e for e in events if e['seq'] > after], run['status']

    def delete_session(self, sid):
        with self.lock:
            for rid, run in list(self.runs.items()):
                if run['session_id'] == sid:
                    (self.root / f'{rid}.jsonl').unlink(missing_ok=True)
                    (self.root / f'{rid}.json').unlink(missing_ok=True)
                    del self.runs[rid]
            self.changed.notify_all()
