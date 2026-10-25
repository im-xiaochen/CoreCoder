"""Persistent, conflict-aware undo checkpoints for file mutations."""
# Recovery boundaries intentionally catch provider/storage exceptions so the
# undo transaction can return a retryable status instead of escaping REPL.
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

CHECKPOINTS_FILE = Path.home() / ".corecoder" / "checkpoints.json"
_stack: list[Checkpoint] = []
_loaded = False
_lock = RLock()
_active_store = None

def _store_file(workspace_id, session_id):
    root = Path(os.environ.get("CORECODER_DATA_DIR", str(Path.home() / ".corecoder")))
    digest = hashlib.sha256(str(Path(workspace_id).expanduser().resolve()).encode()).hexdigest()[:16]
    return root / "checkpoints" / digest / f"{session_id}.json"

def _lexical_path(value):
    """Make an absolute path without resolving symlink components."""
    p = Path(value).expanduser()
    return str(p if p.is_absolute() else Path.cwd() / p)

class CheckpointStore:
    """Workspace/session-scoped store while retaining legacy module globals."""
    def __init__(self, workspace_id, session_id):
        self.workspace_id, self.session_id = str(workspace_id), str(session_id)
        self.path = _store_file(self.workspace_id, self.session_id)
        self.journal = self.path.with_suffix(".journal.json")

    @contextmanager
    def _scope(self):
        global CHECKPOINTS_FILE, _stack, _loaded
        from .tools.base import FILE_MUTATION_LOCK
        with FILE_MUTATION_LOCK, _lock:
            old = (CHECKPOINTS_FILE, _stack, _loaded)
            CHECKPOINTS_FILE, _stack, _loaded = self.path, [], False
            try:
                yield
                self._stack = _stack
            finally:
                CHECKPOINTS_FILE, _stack, _loaded = old

    def begin(self, path):
        with self._scope(): return begin(path, workspace_id=self.workspace_id, session_id=self.session_id)
    def finalize(self, token, path=None):
        with self._scope(): return finalize(token, path)
    def abort(self, token):
        with self._scope(): return abort(token)
    def undo(self, force=False):
        with self._scope(): return undo(force=force)
    def pending(self):
        with self._scope(): return pending()
    def clear(self):
        with self._scope(): return clear()
    def preview(self):
        with self._scope(): return preview()

@contextmanager
def use_store(store):
    """Context manager binding module compatibility wrappers to a store."""
    global CHECKPOINTS_FILE, _stack, _loaded
    if store is None:
        yield
        return
    # Keep the compatibility global swap in the same order as file
    # mutations.  This prevents a caller holding the store lock from waiting
    # on FILE_MUTATION_LOCK while a writer does the inverse.
    from .tools.base import FILE_MUTATION_LOCK
    with FILE_MUTATION_LOCK, _lock:
        old = (CHECKPOINTS_FILE, _stack, _loaded)
        CHECKPOINTS_FILE, _stack, _loaded = store.path, [], False
        try: yield
        finally: CHECKPOINTS_FILE, _stack, _loaded = old

def _sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None

@dataclass
class Checkpoint:
    path: str
    before: bytes | None
    before_exists: bool
    after_exists: bool | None = None
    after_sha256: str | None = None
    status: str = "committed"
    id: str = ""
    workspace_id: str = "default"
    session_id: str = "default"
    legacy: bool = False
    def __post_init__(self):
        if not self.id: self.id = uuid.uuid4().hex
    def __getitem__(self, index):
        if index == 0: return self.path
        if index == 1: return self.before
        raise IndexError(index)

def _ensure_loaded():
    global _loaded
    if _loaded: return
    _loaded = True
    try: raw = json.loads(CHECKPOINTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError): return
    if not isinstance(raw, list): return
    try:
        for item in raw:
            if "before_exists" not in item:
                prior = item.get("prior")
                _stack.append(Checkpoint(_lexical_path(item["path"]), base64.b64decode(prior) if prior is not None else None, prior is not None, legacy=True))
            else:
                before = item.get("before_bytes")
                _stack.append(Checkpoint(_lexical_path(item["absolute_path"]), base64.b64decode(before) if before is not None else None, bool(item.get("before_exists")), item.get("after_exists"), item.get("after_sha256"), item.get("status", "committed"), item.get("id", ""), item.get("workspace_id", "default"), item.get("session_id", "default")))
    except (KeyError, TypeError, ValueError, base64.binascii.Error):
        _stack.clear()
    # Recover a process that stopped after restoring the target but before
    # committing the shortened checkpoint stack.  The journal carries the
    # exact agent-after bytes needed to put the file back for a retry.
    try:
        journal = json.loads(_journal_path().read_text(encoding="utf-8"))
        entry = next((e for e in _stack if e.id == journal.get("checkpoint_id")), None)
        if entry is None:
            _clear_journal()
        elif journal.get("phase") == "compensation_failed":
            entry.status = "recovery_required"
        elif journal.get("phase") == "finalize_committed":
            after = journal.get("current_bytes")
            data = base64.b64decode(after) if after is not None else None
            entry.after_exists = bool(journal.get("current_exists"))
            entry.after_sha256 = _sha(data)
            entry.status = "committed"
        elif journal.get("phase") == "prepared":
            # Undo had only recorded its intent.  The target must still be
            # the recorded agent post-image; otherwise recovery cannot know
            # whether an external writer intervened.
            jp = Path(journal["path"])
            if _unsafe_target(jp):
                entry.status = "recovery_required"
                return
            current_exists, current_data = _current(jp)
            after_matches = current_exists == bool(journal.get("expected_after_exists")) and (not current_exists or _sha(current_data) == journal.get("expected_after_sha256"))
            if after_matches:
                _clear_journal()
            else:
                entry.status = "recovery_required"
        elif journal.get("phase") == "target_restored":
            jp = Path(journal["path"])
            if _unsafe_target(jp):
                entry.status = "recovery_required"
                return
            current_exists, current_data = _current(jp)
            before_matches = current_exists == entry.before_exists and (not current_exists or current_data == entry.before)
            expected_after = journal.get("expected_after_sha256")
            after_matches = current_exists == bool(journal.get("expected_after_exists")) and (not current_exists or _sha(current_data) == expected_after)
            # target_restored means the target may already be restored, or a
            # crash may have happened before restore.  Never write the journal
            # snapshot back merely because it exists: that can overwrite a
            # user's change made while recovering.
            if before_matches:
                _stack[:] = [x for x in _stack if x.id != entry.id]
                try:
                    _save()
                except Exception:  # noqa: BLE001
                    # Keep the record and journal when the shortened stack
                    # cannot be committed; a later startup can retry.
                    _stack.append(entry)
                    entry.status = "recovery_required"
                    return
                _clear_journal()
            elif after_matches:
                entry.status = "committed"
                _clear_journal()
            else:
                entry.status = "recovery_required"
    except (OSError, ValueError, TypeError, KeyError, base64.binascii.Error):
        pass

def _payload(e):
    return {"id": e.id, "workspace_id": e.workspace_id, "session_id": e.session_id, "absolute_path": e.path, "before_exists": e.before_exists, "before_bytes": base64.b64encode(e.before).decode("ascii") if e.before is not None else None, "after_exists": e.after_exists, "after_sha256": e.after_sha256, "status": e.status}

def _save():
    CHECKPOINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINTS_FILE.with_name(CHECKPOINTS_FILE.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            json.dump([_payload(e) for e in _stack], fh); fh.flush(); os.fsync(fh.fileno())
        tmp.replace(CHECKPOINTS_FILE)
    finally:
        try: tmp.unlink(missing_ok=True)
        except OSError: pass

def _current(path):
    if not path.exists() or not path.is_file(): return False, None
    return True, path.read_bytes()

def _unsafe_target(path):
    """Detect replacement symlinks or non-directory parent components."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return True
    parent = path.parent
    while parent != parent.parent:
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            return True
        parent = parent.parent
    return False

def _journal_path():
    return CHECKPOINTS_FILE.with_suffix(".journal.json")

def _write_journal(entry, current, phase="prepared"):
    jp = _journal_path(); jp.parent.mkdir(parents=True, exist_ok=True)
    tmp = jp.with_name(jp.name + f".{uuid.uuid4().hex}.tmp")
    payload = {"checkpoint_id": entry.id, "path": entry.path, "phase": phase,
               "current_exists": current is not None,
               "current_bytes": base64.b64encode(current).decode("ascii") if current is not None else None,
               # Persist the expected agent post-image before touching the
               # target.  Recovery must compare hashes, never re-hash an
               # arbitrary current file and treat it as authorized.
               "expected_after_exists": entry.after_exists,
               "expected_after_sha256": entry.after_sha256}
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh); fh.flush(); os.fsync(fh.fileno())
        tmp.replace(jp)
    finally:
        try: tmp.unlink(missing_ok=True)
        except OSError: pass

def _clear_journal():
    try: _journal_path().unlink(missing_ok=True)
    except OSError: pass

def preview():
    """Return a bounded description of the current undo target."""
    with _lock:
        _ensure_loaded()
        if not _stack:
            return None
        e = _stack[-1]
        exists, current = _current(Path(e.path))
        return {"path": e.path, "exists": exists, "current_sha256": _sha(current),
                "expected_sha256": e.after_sha256, "expected_exists": e.after_exists,
                "conflict": e.legacy or _unsafe_target(Path(e.path)) or exists != bool(e.after_exists) or (exists and _sha(current) != e.after_sha256)}

def begin(path: Path, *, workspace_id="default", session_id="default"):
    with _lock:
        _ensure_loaded(); p = Path(path).expanduser().resolve(); exists, before = _current(p)
        if any(e.status != "committed" for e in _stack):
            raise RuntimeError("checkpoint transaction is unresolved; resolve the pending undo transaction first")
        e = Checkpoint(str(p), before, exists, status="prepared", workspace_id=workspace_id, session_id=session_id)
        _stack.append(e)
        try: _save()
        except Exception: _stack.pop(); raise
        return e.id

def finalize(token, path=None):
    with _lock:
        _ensure_loaded(); e = next((x for x in reversed(_stack) if x.id == token), None)
        if e is None: return
        exists, after = _current(Path(path or e.path).expanduser().resolve()); e.after_exists, e.after_sha256, e.status = exists, _sha(after), "committed"
        _write_journal(e, after, "finalize_committed")
        _save()
        _clear_journal()

def abort(token):
    with _lock:
        _ensure_loaded()
        for i in range(len(_stack)-1, -1, -1):
            if _stack[i].id == token:
                _stack.pop(i); _save(); return

def record(path: Path):
    with _lock:
        _ensure_loaded(); p = Path(path).expanduser().resolve(); exists, before = _current(p)
        _stack.append(Checkpoint(str(p), before, exists, legacy=True)); _save()

def _conflict(e, current):
    msg = f"Conflict: {e.path} (current hash={_sha(current) or 'missing'}, expected hash={e.after_sha256 or 'missing'})"
    if isinstance(current, bytes) and isinstance(e.before, bytes) and len(current) < 100000:
        try:
            d = "".join(difflib.unified_diff(e.before.decode().splitlines(True), current.decode().splitlines(True), fromfile="before", tofile="current", n=2))
            if d: msg += "\n" + d[:2000]
        except UnicodeDecodeError: msg += f"; binary file ({len(current)} bytes)"
    return msg

def _restore(e):
    p = Path(e.path)
    if e.before_exists:
        recreated = not p.parent.exists(); p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_replace(p, e.before or b"")
        return f"Restored {e.path}." if not recreated else f"Restored {e.path} (recreated missing parent directories)."
    p.unlink(missing_ok=True); return f"Removed {e.path} (created this session)."

def _atomic_replace(path, data):
    """Write a durable temporary image and atomically replace *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        # Keep the write boundary observable for fault injection while
        # fsyncing the completed temporary image before replace.
        tmp.write_bytes(data)
        with tmp.open("r+b") as fh:
            os.fsync(fh.fileno())
        tmp.replace(path)
    finally:
        try: tmp.unlink(missing_ok=True)
        except OSError: pass

def undo(force=False):
    from .tools.base import FILE_MUTATION_LOCK
    with FILE_MUTATION_LOCK, _lock:
            _ensure_loaded()
            if not _stack: return "Nothing to undo."
            e = _stack[-1]; p = Path(e.path); exists, current = _current(p)
            # Never follow a type replacement or symlink during undo.  A
            # force flag bypasses content conflicts only; it is not permission
            # to mutate an unrelated target through a link.
            if _unsafe_target(p):
                return f"Conflict: {e.path} (target type changed; symlink or non-file)"
            if e.status != "committed": return f"Error: checkpoint {e.path} is not committed; recovery_required; retryable"
            if e.legacy and not force: return f"Conflict: legacy checkpoint cannot be verified for {e.path}; use /undo --force"
            if not force and (exists != bool(e.after_exists) or (exists and _sha(current) != e.after_sha256)): return _conflict(e, current)
            try: _write_journal(e, current, "prepared")
            except Exception as exc:  # noqa: BLE001
                return f"Error: journal_prepare {e.path}: {exc}; retryable"
            try: msg = _restore(e)
            except Exception as exc:  # noqa: BLE001
                return f"Error: restore target {e.path}: {exc}; retryable"
            try:
                _write_journal(e, current, "target_restored")
            except Exception as exc:  # noqa: BLE001
                # The target is already at before, but the transaction is not
                # committed. Compensate to the exact pre-undo post-image so a
                # subsequent write cannot hide the pending journal.
                try:
                    if exists:
                        _atomic_replace(p, current or b"")
                    else:
                        p.unlink(missing_ok=True)
                except Exception as compensation:  # noqa: BLE001
                    e.status = "recovery_required"
                    try: _write_journal(e, current, "compensation_failed")
                    except Exception: pass  # noqa: BLE001, S110
                    try: _save()
                    except Exception: pass  # noqa: BLE001, S110
                    return f"Error: journal_target_restored {e.path}: {exc}; compensation failed: {compensation}; recovery_required"
                return f"Error: journal_target_restored {e.path}: {exc}; retryable"
            old = list(_stack); _stack.pop()
            try: _save()
            except Exception as exc:  # noqa: BLE001
                _stack[:] = old
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if exists:
                        _atomic_replace(p, current or b"")
                    else: p.unlink(missing_ok=True)
                except Exception as compensation:  # noqa: BLE001
                    e.status = "recovery_required"
                    try: _write_journal(e, current, "compensation_failed")
                    except Exception: pass  # noqa: BLE001, S110
                    try: _save()
                    except Exception: pass  # noqa: BLE001, S110
                    return f"Error: checkpoint_commit_failed {e.path}: {exc}; compensation failed: {compensation}; recovery_required"
                return f"Error: checkpoint_commit_failed {e.path}: {exc}; retryable"
            _clear_journal()
            return msg

def pending():
    with _lock: _ensure_loaded(); return len(_stack)

def clear():
    global _loaded
    with _lock:
        _stack.clear(); _loaded = True
        try: CHECKPOINTS_FILE.unlink(missing_ok=True)
        except OSError: pass
