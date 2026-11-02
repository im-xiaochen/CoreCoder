"""Bounded project rule discovery for the current workspace ancestry."""
from pathlib import Path

MAX_FILE = 32 * 1024
MAX_TOTAL = 64 * 1024


def discover_rules(cwd, workspace_root=None, mode="code"):
    # This guard must precede every filesystem operation, including resolve.
    if mode == "chat":
        return []
    cwd = Path(cwd).resolve()
    if workspace_root is None:
        root = next((p for p in (cwd, *cwd.parents) if (p / ".git").exists()), cwd)
    else:
        root = Path(workspace_root).resolve()
    if not cwd.is_relative_to(root):
        return [{"path": str(cwd), "status": "skipped", "reason": "cwd outside workspace"}]
    chain = list(reversed((cwd, *cwd.parents)))
    chain = chain[chain.index(root):]
    results = []
    total = 0
    for directory in chain:
        path = directory / "AGENTS.md"
        try:
            if not path.exists():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                results.append({"path": str(path), "status": "skipped", "reason": "outside workspace"})
                continue
            if not resolved.is_file():
                results.append({"path": str(path), "status": "skipped", "reason": "not a file"})
                continue
            with resolved.open("rb") as file:
                raw = file.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE:
                results.append({"path": str(path), "status": "skipped", "reason": "file exceeds 32 KiB"})
                continue
            if total + len(raw) > MAX_TOTAL:
                results.append({"path": str(path), "status": "skipped", "reason": "total exceeds 64 KiB"})
                continue
            content = raw.decode("utf-8")
            total += len(raw)
            results.append({"path": str(path), "status": "loaded", "content": content})
        except (OSError, UnicodeError, RuntimeError) as exc:
            results.append({"path": str(path), "status": "skipped", "reason": type(exc).__name__})
    return results
