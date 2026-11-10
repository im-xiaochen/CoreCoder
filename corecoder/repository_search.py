"""Bounded on-demand workspace inventory for search and repository maps."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

MAX_FILES = 5000
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 20_000_000
_MAX_OUTPUT = 4_000_000
_EXCLUDED = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".ssh", ".aws"}


def _redirect(path):
    try:
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or bool(
            getattr(path.lstat(), "st_file_attributes", 0) & 0x400
        )
    except OSError:
        return True


def safe_file(root, path):
    try:
        rel = path.relative_to(root)
        if any(p.casefold() in _EXCLUDED for p in rel.parts):
            return False
        name = path.name.casefold()
        if (name == ".env" or name.startswith(".env.") and name not in {
            ".env.example", ".env.sample", ".env.template"
        } or name in {"credentials", "credentials.json", "id_rsa", "id_ed25519"}
                or name.endswith((".pem", ".key", ".p12", ".pfx"))):
            return False
        current = root
        for part in rel.parts:
            current = current / part
            if _redirect(current):
                return False
        return path.resolve().is_relative_to(root) and path.is_file()
    except (OSError, ValueError):
        return False


def _run(command, root, env, *, allow_partial=False):
    proc = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timed_out = threading.Event()

    def stop_on_timeout():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(5, stop_on_timeout)
    timer.daemon = True
    timer.start()
    data = bytearray()
    truncated = False
    try:
        while len(data) <= _MAX_OUTPUT:
            chunk = proc.stdout.read(min(65536, _MAX_OUTPUT + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_OUTPUT:
            truncated = True
            proc.kill()
        rc = proc.wait(timeout=5)
        if timed_out.is_set():
            truncated = True
        elif rc and not truncated:
            if allow_partial and rc == 2:
                truncated = True
            elif not (allow_partial and rc == 1 and not data):
                raise OSError("inventory backend failed")
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    # Never interpret a partial pathname at the output boundary.
    end = data.rfind(b"\0")
    if end < 0:
        return [], truncated or bool(data)
    if end != len(data) - 1:
        truncated = True
    return [os.fsdecode(bytes(item)) for item in data[:end].split(b"\0") if item], truncated


def _walk(root, reject_gitignore):
    """Inspect the whole workspace before returning any fallback entries."""
    started = time.monotonic()
    entries = 0
    paths = []
    errors = []
    for current, dirs, files in os.walk(root, followlinks=False, onerror=errors.append):
        if errors or time.monotonic() - started > 5:
            raise ValueError("Fallback inventory could not safely inspect the workspace")
        dirs[:] = sorted(d for d in dirs if d.casefold() not in _EXCLUDED and not _redirect(Path(current) / d))
        entries += len(dirs) + len(files)
        if entries > 20000:
            raise ValueError("Fallback inventory exceeded the inspection limit")
        for name in files:
            if name.casefold() in ({".ignore", ".rgignore", ".gitignore"} if reject_gitignore else {".ignore", ".rgignore"}):
                raise ValueError("Fallback inventory cannot safely apply workspace ignore rules")
            candidate = Path(current) / name
            if safe_file(root, candidate):
                paths.append(candidate)
                if len(paths) > MAX_FILES:
                    raise ValueError("Fallback inventory exceeded the file limit")
    if errors:
        raise ValueError("Fallback inventory could not safely inspect the workspace")
    return paths


def _git_inventory(root, git, env):
    command = [git, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull]
    try:
        result = subprocess.run(command + ["rev-parse", "--show-toplevel"], cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
        if Path(os.fsdecode(result.stdout.strip())).resolve() != root:
            raise ValueError("Git fallback requires the workspace to be the repository root")
        _walk(root, False)
        names, truncated = _run(command + ["ls-files", "--cached", "--others", "--exclude-standard", "-z"], root, env)
        payload = b"".join(os.fsencode(name) + b"\0" for name in names)
        result = subprocess.run(command + ["check-ignore", "--no-index", "-z", "--stdin"], cwd=root,
                                env=env, input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
        if result.returncode not in (0, 1) or len(result.stdout) > _MAX_OUTPUT:
            raise ValueError("Git fallback could not verify ignore rules")
        ignored = set(result.stdout.split(b"\0"))
        return [name for name in names if os.fsencode(name) not in ignored], truncated
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Git fallback could not safely enumerate the workspace") from exc


def inventory(root, base):
    root = Path(root).resolve()
    base = Path(base).expanduser()
    if not base.is_absolute():
        base = root / base
    # Reject parent traversal before normalization so a link/.. is never hidden.
    if ".." in base.parts:
        raise ValueError("Search path cannot contain parent traversal (..)")
    base = Path(os.path.abspath(base))
    try:
        rel = base.relative_to(root)
    except ValueError as exc:
        raise ValueError("Search path outside workspace") from exc
    current = root
    for part in rel.parts:
        current = current / part
        if _redirect(current):
            raise ValueError("Search path contains a link or does not exist")
    if not base.resolve().is_relative_to(root):
        raise ValueError("Search path outside workspace")
    if not base.is_file() and not base.is_dir():
        raise ValueError("Search path not found")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_") and k != "RIPGREP_CONFIG_PATH"}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    rg = shutil.which("rg")
    if rg:
        names, truncated = _run([rg, "--files", "-0", "--no-config", "--hidden", "--no-require-git"], root, env, allow_partial=True)
        label = "rg (incomplete: backend error, timeout, or output limit)" if truncated else "rg"
    else:
        git = shutil.which("git")
        if git:
            probe_env = dict(env, LC_ALL="C", LANG="C")
            try:
                probe = subprocess.run([git, "-c", "core.fsmonitor=false", "rev-parse", "--show-toplevel"],
                                       cwd=root, env=probe_env, capture_output=True,
                                       timeout=5, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError("Git fallback probe failed") from exc
            if probe.returncode == 0:
                names, truncated = _git_inventory(root, git, env)
                label = "git"
            elif probe.returncode == 128 and b"not a git repository" in probe.stderr.lower():
                names = [str(path.relative_to(root)) for path in _walk(root, True)]
                truncated = False
                label = "walk"
            else:
                raise ValueError("Git fallback probe failed")
        else:
            names = [str(path.relative_to(root)) for path in _walk(root, True)]
            truncated = False
            label = "walk"
    paths = []
    for name in names:
        candidate = root / name
        if (candidate == base or (base.is_dir() and candidate.is_relative_to(base))) and safe_file(root, candidate):
            paths.append(candidate)
    paths = sorted(set(paths), key=lambda p: p.as_posix())
    return paths[:MAX_FILES], label, truncated or len(paths) > MAX_FILES


