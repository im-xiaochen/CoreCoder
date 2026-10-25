"""Session persistence - save and resume conversations.

Claude Code maintains session state via QueryEngine (1295 lines).
CoreCoder distills this to: JSON dump of messages + model config.
"""

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .validation import validate_session

log = logging.getLogger(__name__)

SESSIONS_DIR = Path.home() / ".corecoder" / "sessions"
_SAFE_SESSION_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_SESSION_ID_LEN = 100  # keep filenames comfortably under the OS limit
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _session_lock(sid):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(sid, threading.Lock())


def _normalize_session_id(session_id: str | None) -> str:
    if not session_id:
        return _new_session_id()

    name = session_id.strip().replace("\\", "/").split("/")[-1]
    name = _SAFE_SESSION_RE.sub("-", name).strip(".-_")
    if len(name) > _MAX_SESSION_ID_LEN:
        name = name[:_MAX_SESSION_ID_LEN].strip(".-_")
    return name or _new_session_id()


def _new_session_id() -> str:
    return f"session_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _session_path(session_id: str) -> Path:
    path = (SESSIONS_DIR / f"{_normalize_session_id(session_id)}.json").resolve()
    root = SESSIONS_DIR.resolve()
    if root != path.parent:
        raise ValueError("Invalid session id")
    return path


def save_session(messages: list[dict], model: str, session_id: str | None = None) -> str:
    """Save conversation to disk. Returns the session ID."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    session_id = _normalize_session_id(session_id)

    data = {
        "schema_version": 2,
        "id": session_id,
        "model": model,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "messages": messages,
    }

    if not isinstance(model, str) or not model.strip() or not validate_session(data):
        raise ValueError("invalid session structure")
    path = _session_path(session_id)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    with _session_lock(session_id):
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as tmp:
                tmp_name = tmp.name
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            Path(tmp_name).replace(path)
            try:
                fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
        finally:
            if tmp_name:
                try:
                    Path(tmp_name).unlink()
                except FileNotFoundError:
                    pass
    return session_id


def load_session(session_id: str) -> tuple[list[dict], str] | None:
    """Load a saved session. Returns (messages, model) or None."""
    path = _session_path(session_id)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not validate_session(data, str(path.resolve())):
            return None
        return data["messages"], data["model"]
    except (json.JSONDecodeError, KeyError, OSError, UnicodeError):
        log.warning("invalid session %s: unreadable archive", path.resolve())
        # a corrupt or truncated session file shouldn't crash resume
        return None


def list_sessions() -> list[dict]:
    """List available sessions, newest first."""
    if not SESSIONS_DIR.exists():
        return []

    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not validate_session(data, str(f.resolve())):
                continue
            # grab first user message as preview
            preview = ""
            for m in data.get("messages", []):
                if m.get("role") == "user" and m.get("content"):
                    preview = m["content"][:80]
                    break
            sessions.append(
                {
                    "id": data.get("id", f.stem),
                    "model": data.get("model", "?"),
                    "saved_at": data.get("saved_at", "?"),
                    "preview": preview,
                }
            )
        except (json.JSONDecodeError, KeyError, OSError, UnicodeError):
            log.warning("invalid session %s: unreadable archive", f.resolve())
            continue

    return sessions[:20]  # cap at 20
