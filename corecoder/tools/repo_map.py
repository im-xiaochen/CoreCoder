"""On-demand, bounded Python symbol and file map."""

import ast
from collections import Counter
from typing import ClassVar

from ..repository_search import MAX_FILE_BYTES, MAX_TOTAL_BYTES, inventory
from ..state import SessionState
from .base import Tool


class RepoMapTool(Tool):
    name = "repo_map"
    description = "List workspace files, directories and Python definitions/imports; rescans on demand."
    parameters: ClassVar[dict] = {
        "type": "object",
        "required": [],
        "properties": {
            "path": {"type": "string"},
            "query": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    }

    def __init__(self, state=None):
        self.state = state or SessionState()

    def clone_for_state(self, state):
        return type(self)(state=state)

    def execute(self, path=".", query="", offset=0, limit=100):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 200:
            return "Error: invalid pagination"
        try:
            files, backend, truncated = inventory(self.state.workspace_root, (self.state.cwd / path).absolute())
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"
        root = self.state.workspace_root
        rows = []
        dirs = Counter()
        total = 0
        incomplete = []
        for fp in files:
            relative = fp.relative_to(root).as_posix()
            dirs[str(fp.relative_to(root).parent)] += 1
            rows.append(f"{relative}: file")
            if fp.suffix != ".py":
                continue
            try:
                with fp.open("rb") as stream:
                    source = stream.read(min(MAX_FILE_BYTES + 1, MAX_TOTAL_BYTES - total + 1))
                total += len(source)
                if total > MAX_TOTAL_BYTES:
                    incomplete.append("byte scan limit")
                    break
                if len(source) > MAX_FILE_BYTES:
                    incomplete.append("oversized files skipped")
                    continue
                if source.count(b"\n") > 10000:
                    incomplete.append("Python line limit")
                    continue
                tree = ast.parse(source, filename=relative)
                for index, node in enumerate(ast.walk(tree)):
                    if index >= 20000 or len(rows) >= 20000:
                        incomplete.append("symbol limit")
                        break
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        rows.append(f"{relative}:{node.lineno}: {type(node).__name__} {node.name}")
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        names = ",".join(alias.name for alias in node.names)
                        module = ('.' * node.level + (node.module or '')) if isinstance(node, ast.ImportFrom) else ''
                        rows.append(f"{relative}:{node.lineno}: import {module} {names}")
            except (OSError, SyntaxError, ValueError, UnicodeError):
                incomplete.append("unreadable or invalid Python skipped")
        rows = [f"{name}/: {count} files" for name, count in sorted(dirs.items())] + rows
        if query:
            rows = [r for r in rows if query.casefold() in r.casefold()]
        selected = []
        output_bytes = 0
        for row in rows[offset : offset + limit]:
            size = len(row.encode("utf-8")) + 1
            if size > 10000 or output_bytes + size > 100000:
                incomplete.append("output limit")
                break
            selected.append(row)
            output_bytes += size
        result = "\n".join(selected) or "No entries found."
        if len(rows) > offset + len(selected) and selected:
            result += f"\n... next_offset={offset + len(selected)}"
        if truncated:
            incomplete.append("file scan limit")
        if incomplete:
            result += "\n... incomplete: " + ", ".join(sorted(set(incomplete)))
        return result + f"\n[backend: {backend}; live scan; Python symbols only]"
