"""Bounded, explicit Git review and index operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path


class GitWorkspaceError(RuntimeError):
    pass


class GitWorkspace:
    def __init__(self, root, max_files=1000, max_diff=50000):
        self.root = Path(root).resolve()
        self.max_files = max_files
        self.max_diff = max_diff
        self.lock = threading.RLock()
        top = self._git("rev-parse", "--show-toplevel").decode().strip()
        if Path(top).resolve() != self.root:
            raise GitWorkspaceError("Workspace must be the repository root")
        index = self._git("rev-parse", "--git-path", "index").decode().strip()
        self.index = (self.root / index).resolve()

    def _git(self, *args, check=True, input=None, index_file=None, identity=None, identity_query=False):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_PAGER="cat",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS="0",
            GIT_LITERAL_PATHSPECS="1",
        )
        if identity_query:
            if "GIT_CONFIG_GLOBAL" in os.environ:
                env["GIT_CONFIG_GLOBAL"] = os.environ["GIT_CONFIG_GLOBAL"]
            else:
                env.pop("GIT_CONFIG_GLOBAL", None)
        if args and args[0] in {"status", "diff", "add"}:
            probe = subprocess.run(
                ["git", "config", "--get-regexp", r"^filter\..*\.(clean|process)$"],
                cwd=self.root,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if probe.returncode == 0:
                raise GitWorkspaceError("Configured Git clean filters require separate authorization")
            if probe.returncode != 1:
                raise GitWorkspaceError("Unable to inspect Git filter configuration")
        if index_file is not None:
            env["GIT_INDEX_FILE"] = str(index_file)
        if identity is not None:
            env.update(identity)
        command = [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "diff.external=",
            "-c",
            "commit.gpgsign=false",
            *args,
        ]
        p = subprocess.Popen(
            command,
            cwd=self.root,
            env=env,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output, errors = bytearray(), bytearray()
        overflow = threading.Event()

        def drain(stream, buffer, limit):
            while chunk := stream.read(65536):
                remaining = max(0, limit - len(buffer))
                buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    if identity_query or args[0] in {"status", "diff", "ls-files"}:
                        p.kill()

        readers = [
            threading.Thread(target=drain, args=(p.stdout, output, 4096 if identity_query else 8_000_000)),
            threading.Thread(target=drain, args=(p.stderr, errors, 8192)),
        ]
        for reader in readers:
            reader.start()
        if input is not None:
            p.stdin.write(input)
            p.stdin.close()
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:
            p.kill()
            p.wait()
            raise GitWorkspaceError("Git command timed out") from exc
        finally:
            for reader in readers:
                reader.join()
            p.stdout.close()
            p.stderr.close()
        if overflow.is_set() and (identity_query or args[0] in {"status", "diff", "ls-files"}):
            raise GitWorkspaceError("Git output exceeds review limit")
        if check and p.returncode:
            raise GitWorkspaceError(errors.decode("utf-8", "replace").strip())
        return bytes(output)

    def _head(self):
        return self._git("rev-parse", "--verify", "HEAD", check=False).decode().strip() or None

    def _fingerprint(self, path):
        p = self.root / path
        try:
            if (
                p.name == ".env"
                or (p.name.startswith(".env.") and p.name not in {".env.example", ".env.sample", ".env.template"})
                or ".git" in p.parts
            ):
                return "unsafe"
            if p.is_symlink() or not p.resolve().is_relative_to(self.root):
                return "unsafe"
            if not p.exists():
                return "missing"
            if not p.is_file() or p.stat().st_size > 10_000_000:
                return "unsafe"
            return hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            return "unsafe"

    def status(self, baseline=None):
        with self.lock:
            raw = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none")
            parts = raw.split(b"\0")
            entries = []
            i = 0
            while i < len(parts) and parts[i]:
                item = parts[i]
                i += 1
                xy = item[:2].decode("ascii")
                path = os.fsdecode(item[3:])
                old = None
                if "R" in xy or "C" in xy:
                    old = os.fsdecode(parts[i])
                    i += 1
                entries.append({"path": path, "old_path": old, "index": xy[0], "worktree": xy[1], "untracked": xy == "??"})
            complete = len(entries) <= self.max_files
            entries = entries[: self.max_files]
            fingerprints = {p: self._fingerprint(p) for e in entries for p in (e["path"], e["old_path"]) if p}
            index_entries = self._index_entries()
            for e in entries:
                p = e["path"]
                prior = baseline.get("files", {}).get(p) if baseline else None
                unknown = (
                    baseline is None
                    or not baseline.get("complete")
                    or fingerprints[p] == "unsafe"
                    or (prior and prior["worktree"] == "unsafe")
                )
                e["preexisting"] = None if unknown else p in baseline["dirty"]
                current = {"worktree": fingerprints[p], "index": index_entries.get(p)}
                e["changed_during_run"] = None if unknown else current != (prior or {"worktree": "missing", "index": None})
            index_hash = hashlib.sha256(self.index.read_bytes() if self.index.exists() else b"").hexdigest()
            ref = self._git("symbolic-ref", "-q", "HEAD", check=False).decode().strip()
            payload = {
                "root": str(self.root),
                "head": self._head(),
                "ref": ref,
                "index_hash": index_hash,
                "status": raw.hex(),
                "fingerprints": fingerprints,
                "complete": complete,
            }
            token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            return {
                "root": str(self.root),
                "head": payload["head"],
                "ref": ref,
                "branch": self._git("branch", "--show-current").decode().strip(),
                "entries": entries,
                "complete": complete,
                "review_token": token,
                "fingerprints": fingerprints,
            }

    def _index_entries(self):
        result = {}
        for record in self._git("ls-files", "--stage", "-z").split(b"\0"):
            if record:
                meta, path = record.split(b"\t", 1)
                result.setdefault(os.fsdecode(path), []).append(meta.decode("ascii"))
        return result

    def baseline(self):
        with self.lock:
            state = self.status()
            index = self._index_entries()
            paths = set(index) | {e["path"] for e in state["entries"]}
            complete = state["complete"] and len(paths) <= self.max_files
            files = {p: {"worktree": self._fingerprint(p), "index": index.get(p)} for p in sorted(paths)[: self.max_files]}
            return {"complete": complete, "files": files, "dirty": [e["path"] for e in state["entries"]]}

    def diff(self, path, staged=False):
        state = self.status()
        entry = next((e for e in state["entries"] if e["path"] == path), None)
        if not entry:
            raise GitWorkspaceError("Path is not in review")
        if any(self._fingerprint(p) == "unsafe" for p in (path, entry["old_path"]) if p):
            raise GitWorkspaceError("Unsafe path")
        if entry["untracked"] and not staged:
            data = (self.root / path).read_bytes()
            data = b"Binary file" if b"\0" in data else data
        else:
            args = ["diff", "--no-ext-diff", "--no-textconv"]
            if staged:
                args.append("--cached")
            data = self._git(*args, "--", *([entry["old_path"]] if entry["old_path"] else []), path)
        return {
            "text": data[: self.max_diff].decode("utf-8", "replace"),
            "truncated": len(data) > self.max_diff,
            "binary": b"Binary file" in data or b"GIT binary patch" in data,
        }

    def _validate(self, paths, token):
        state = self.status()
        if not state["complete"] or state["review_token"] != token:
            raise GitWorkspaceError("Review is stale or incomplete")
        if not paths or len(paths) != len(set(paths)):
            raise GitWorkspaceError("Select paths explicitly")
        entries = {e["path"]: e for e in state["entries"]}
        entries.update({e["old_path"]: e for e in state["entries"] if e["old_path"]})
        if any(p not in entries for p in paths):
            raise GitWorkspaceError("Path is not in review")
        for p in paths:
            e = entries[p]
            if e["old_path"] and not {e["path"], e["old_path"]}.issubset(paths):
                raise GitWorkspaceError("Select both rename paths")
            if "U" in e["index"] + e["worktree"] or e["index"] in "DA" and e["worktree"] in "AU":
                raise GitWorkspaceError("Conflict requires manual handling")
            if self._fingerprint(p) == "unsafe" or p.startswith(".git/") or "\n" in p or "\r" in p:
                raise GitWorkspaceError("Unsafe path")
        return state

    def stage(self, paths, token):
        with self.lock:
            lock_path = Path(str(self.index) + ".lock")
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                raise GitWorkspaceError("Git index is locked") from exc
            try:
                self._validate(paths, token)
                for p in paths:
                    attr = self._git("check-attr", "filter", "--", p).decode("utf-8", "replace").strip()
                    if not attr.endswith(": unspecified") and not attr.endswith(": unset"):
                        raise GitWorkspaceError("Filtered files require separate authorization")
                with tempfile.TemporaryDirectory() as temp:
                    private_index = Path(temp) / "index"
                    if self.index.exists():
                        shutil.copyfile(self.index, private_index)
                    self._git("add", "--", *paths, index_file=private_index)
                    self._validate(paths, token)
                    os.close(fd)
                    fd = None
                    shutil.copyfile(private_index, lock_path)
                    os.replace(lock_path, self.index)
                    lock_path = None
                return self.status()
            finally:
                if fd is not None:
                    os.close(fd)
                if lock_path is not None:
                    lock_path.unlink(missing_ok=True)

    def commit(self, paths, token, message):
        if not message.strip():
            raise GitWorkspaceError("Commit message required")
        with self.lock:
            for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
                marker_path = self._git("rev-parse", "--git-path", marker).decode().strip()
                if (self.root / marker_path).exists():
                    raise GitWorkspaceError("Git operation in progress")
            lock_path = Path(str(self.index) + ".lock")
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                raise GitWorkspaceError("Git index is locked") from exc
            try:
                state = self._validate(paths, token)
                staged = {p for e in state["entries"] if e["index"] not in (" ", "?") for p in (e["path"], e["old_path"]) if p}
                if staged != set(paths):
                    raise GitWorkspaceError("Select every staged file and no unstaged files")
                with tempfile.TemporaryDirectory() as temp:
                    private_index = Path(temp) / "index"
                    shutil.copyfile(self.index, private_index)
                    tree = self._git("write-tree", index_file=private_index).decode().strip()
                args = ["commit-tree", tree]
                if state["head"]:
                    args += ["-p", state["head"]]
                oid = self._git(*args, input=message.encode("utf-8"), identity=self._identity()).decode().strip()
                ref = state["ref"] or "HEAD"
                if self._git("symbolic-ref", "-q", "HEAD", check=False).decode().strip() != state["ref"]:
                    raise GitWorkspaceError("Branch changed; review again")
                self._git("update-ref", ref, oid, state["head"] or "0" * 40)
                return {"commit": oid, "state": self.status()}
            finally:
                os.close(fd)
                lock_path.unlink(missing_ok=True)

    def _identity(self):
        values = {}
        for field in ("name", "email"):
            if all(os.environ.get(f"GIT_{role}_{field.upper()}") for role in ("AUTHOR", "COMMITTER")):
                values[field] = None
            else:
                values[field] = (
                    self._git("config", "--includes", "--get", f"user.{field}", check=False, identity_query=True)
                    .decode("utf-8", "strict")
                    .strip()
                )
        identity = {}
        for role in ("AUTHOR", "COMMITTER"):
            for field in ("NAME", "EMAIL"):
                key = f"GIT_{role}_{field}"
                value = os.environ.get(key) or values[field.lower()]
                if not value or "\n" in value or "\r" in value or "\0" in value:
                    raise GitWorkspaceError("Configure Git user.name and user.email before committing")
                identity[key] = value
        return identity
