"""Explicitly authorized project checks with persistent results."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .command_runner import run_command


class CheckError(ValueError):
    pass


class ProjectChecks:
    def __init__(self, workspace, store):
        self.workspace = Path(workspace).resolve()
        self.store = Path(store).absolute()
        self._lock = threading.RLock()
        self._recovered = False
        self._active = set()

    def _path(self, relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise CheckError("Invalid working directory")
        candidate = self.workspace / relative
        if not candidate.resolve().is_relative_to(self.workspace):
            raise CheckError("Working directory leaves workspace")
        current = self.workspace
        for part in Path(relative).parts:
            if part == "..":
                raise CheckError("Invalid working directory")
            current = current / part
            if current.is_symlink():
                raise CheckError("Linked working directory is not allowed")
        if not candidate.is_dir():
            raise CheckError("Working directory does not exist")
        return candidate.resolve()

    def inspect(self):
        config = self.workspace / ".corecoder" / "checks.json"
        if config.is_symlink() or config.parent.is_symlink() or not config.resolve().is_relative_to(self.workspace):
            raise CheckError("Linked configuration is not allowed")
        if not config.exists():
            return {"configured": False, "checks": [], "token": None}
        with config.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise CheckError("Configuration exceeds size limit")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise CheckError("Invalid JSON configuration") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("checks"), list)
            or len(data["checks"]) > 100
        ):
            raise CheckError("Invalid checks configuration")
        checks = []
        ids = set()
        for item in data["checks"]:
            if not isinstance(item, dict):
                raise CheckError("Invalid check")
            ident, name, command = item.get("id"), item.get("name"), item.get("command")
            if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident) or ident in ids:
                raise CheckError("Invalid or duplicate check id")
            if (
                not isinstance(name, str)
                or not 0 < len(name) <= 200
                or not isinstance(command, str)
                or not 0 < len(command) <= 8192
            ):
                raise CheckError("Invalid name or command")
            timeout = item.get("timeout", 120)
            if type(timeout) is not int or not 1 <= timeout <= 3600:
                raise CheckError("Invalid timeout")
            cwd = item.get("cwd", ".")
            self._path(cwd)
            patterns = item.get("files", [])
            if (
                not isinstance(patterns, list)
                or len(patterns) > 100
                or any(not isinstance(p, str) or not 0 < len(p) <= 256 for p in patterns)
            ):
                raise CheckError("Invalid file patterns")
            ids.add(ident)
            checks.append({"id": ident, "name": name, "command": command, "cwd": cwd, "timeout": timeout, "files": patterns})
        return {"configured": True, "checks": checks, "token": hashlib.sha256(raw).hexdigest()}

    def select(self, ids=None, files=None):
        config = self.inspect()
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)):
            raise CheckError("Invalid check selection")
        if files is not None and (
            not isinstance(files, list) or len(files) > 1000 or any(not isinstance(p, str) or len(p) > 4096 for p in files)
        ):
            raise CheckError("Invalid file selection")
        chosen = set(ids) if ids is not None else None
        if chosen is not None and chosen - {item["id"] for item in config["checks"]}:
            raise CheckError("Unknown check id")
        selected = [
            item
            for item in config["checks"]
            if (chosen is None or item["id"] in chosen)
            and (
                files is None
                or bool(files)
                and (not item["files"] or any(fnmatch.fnmatchcase(path, pattern) for path in files for pattern in item["files"]))
            )
        ]
        return {**config, "checks": selected}

    def run(self, check_id, token, *, authorized=False, files=None, run_id=None, session_id=None, cancel=None, on_progress=None):
        if authorized is not True:
            raise CheckError("Explicit authorization required")
        selection = self.select([check_id], files)
        if token != selection["token"]:
            raise CheckError("Configuration changed; review again")
        if not selection["checks"]:
            raise CheckError("No matching check")
        check = selection["checks"][0]
        cwd = self._path(check["cwd"])
        if self.inspect()["token"] != token:
            raise CheckError("Configuration changed; review again")
        ident = uuid.uuid4().hex
        record = {
            "id": ident,
            "check_id": check_id,
            "run_id": run_id,
            "session_id": session_id,
            "files": files,
            "command": check["command"],
            "cwd": check["cwd"],
            "started_at": time.time(),
            "ended_at": None,
            "status": "running",
            "command_status": None,
            "exit_code": None,
            "truncated": False,
            "stdout": "",
            "stderr": "",
            "repair_attempts": 0,
        }
        with self._lock:
            self._save(record)
            self._active.add(ident)
        interrupted = None
        progress_lock = threading.Lock()
        progress_open = True
        progress_error = []
        pending_output = []
        last_flush = 0.0
        output_bytes = 0
        flush_timer = None

        def progress(stream, text, sequence):
            nonlocal progress_open, last_flush, output_bytes, flush_timer
            with progress_lock:
                if not progress_open or (not text and not pending_output):
                    return
                if text:
                    remaining = max(0, 2 * 1024 * 1024 - output_bytes)
                    kept = text.encode("utf-8")[:remaining].decode("utf-8", "ignore")
                    record[stream] += kept
                    output_bytes += len(kept.encode("utf-8"))
                    pending_output.append(kept)
                    record["truncated"] |= len(kept.encode("utf-8")) < len(text.encode("utf-8"))
                    record["output_sequence"] = sequence
                if text and last_flush and sum(map(len, pending_output)) < 65536 and time.monotonic() - last_flush < 0.25:
                    if flush_timer is None:
                        flush_timer = threading.Timer(0.25, lambda: progress("mixed", "", sequence))
                        flush_timer.daemon = True
                        flush_timer.start()
                    return
                if flush_timer is not None:
                    flush_timer.cancel()
                    flush_timer = None
                try:
                    self._save(record)
                except Exception as exc:  # noqa: BLE001 - persist failure must be visible
                    progress_error.append(str(exc)[:1000])
                    return
                event = {
                    "id": ident,
                    "check_id": check_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "stream": "mixed",
                    "text": "".join(pending_output),
                    "output_sequence": record["output_sequence"],
                    "truncated": record["truncated"],
                }
                pending_output.clear()
                last_flush = time.monotonic()
            if on_progress:
                on_progress(event)

        def mark_truncated():
            with progress_lock:
                if not progress_open:
                    return
                record["truncated"] = True
                try:
                    self._save(record)
                except Exception as exc:  # noqa: BLE001 - persist failure must be visible
                    progress_error.append(str(exc)[:1000])
                    return
                event = {
                    "id": ident,
                    "check_id": check_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "stream": "mixed",
                    "text": "",
                    "output_sequence": record.get("output_sequence", 0),
                    "truncated": True,
                }
            if on_progress:
                on_progress(event)

        try:
            if cancel is not None and cancel.is_set():
                record.update(status="cancelled", command_status="cancelled")
            else:
                result = run_command(
                    check["command"],
                    str(cwd),
                    timeout=check["timeout"],
                    cancel=cancel,
                    on_output=progress,
                    on_truncated=mark_truncated,
                )
                with progress_lock:
                    progress_open = False
                status = ("passed" if result.exit_code == 0 else "failed") if result.status == "exited" else result.status
                record.update(
                    status=status,
                    command_status=result.status,
                    exit_code=result.exit_code,
                    truncated=result.truncated,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
        except KeyboardInterrupt as exc:
            status = getattr(exc, "command_status", "unknown")
            record.update(status=status, command_status=status)
            interrupted = exc
        except Exception as exc:  # noqa: BLE001 - persist unexpected runner failures
            record.update(status="unknown", command_status="unknown", error=str(exc)[:8192])
        with progress_lock:
            progress_open = False
            if flush_timer is not None:
                flush_timer.cancel()
            if progress_error:
                record["status"] = "unknown"
                record["persistence_error"] = progress_error[-1]
        record["ended_at"] = time.time()
        with self._lock:
            try:
                self._save(record)
            finally:
                self._active.discard(ident)
        if interrupted is not None:
            raise interrupted
        return record

    def _check_store(self):
        if self.store.resolve() != self.store:
            raise CheckError("Linked result storage is not allowed")
        current = self.store
        while current != current.parent:
            if current.is_symlink():
                raise CheckError("Linked result storage is not allowed")
            current = current.parent

    def _save(self, record):
        self._check_store()
        self.store.mkdir(parents=True, exist_ok=True)
        target = self.store / (record["id"] + ".json")
        fd, name = tempfile.mkstemp(prefix=".check-", dir=self.store)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False)
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    os.replace(name, target)
                    break
                except OSError as exc:
                    if getattr(exc, "winerror", None) not in {5, 32, 33} or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def recover(self):
        with self._lock:
            if self._recovered:
                return
            self._check_store()
            if self.store.exists():
                for path in self.store.glob("*.json"):
                    if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name) or path.stem in self._active:
                        continue
                    try:
                        record = self.get(path.stem)
                        if record.get("status") == "running":
                            record.update(status="interrupted", command_status="interrupted", ended_at=time.time())
                            self._save(record)
                    except CheckError:
                        continue
            self._recovered = True

    def get(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident):
            raise CheckError("Invalid result id")
        self._check_store()
        path = self.store / (ident + ".json")
        try:
            if path.is_symlink():
                raise CheckError("Linked result is not allowed")
            with path.open("rb") as stream:
                raw = stream.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise CheckError("Result exceeds size limit")
            record = json.loads(raw)
            if not isinstance(record, dict) or record.get("id") != ident:
                raise CheckError("Invalid result")
            return record
        except (OSError, ValueError) as exc:
            raise CheckError("Result not found or invalid") from exc

    def history(self, session_id):
        self._check_store()
        if not self.store.exists():
            return []
        records = []
        for path in self.store.glob("*.json"):
            if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
                continue
            try:
                record = self.get(path.stem)
                if record.get("session_id") == session_id:
                    records.append({k: v for k, v in record.items() if k not in ("stdout", "stderr")})
            except CheckError:
                continue
        return sorted(records, key=lambda r: r.get("started_at", 0), reverse=True)[:200]
