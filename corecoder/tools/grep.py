"""Content search with bounded workspace enumeration."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

from ..repository_search import inventory
from ..state import SessionState
from .base import Tool


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with a Python regular expression."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "include": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["pattern"],
    }

    def __init__(self, state=None):
        self.state = state or SessionState()

    def clone_for_state(self, state):
        return type(self)(state=state)

    def execute(self, pattern, path=".", include=None, offset=0, limit=200):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 200:
            return "Error: invalid pagination"
        if len(pattern) > 1000:
            return "Error: oversized regular expression"
        try:
            files, backend, truncated = inventory(self.state.workspace_root, (self.state.cwd / path).absolute())
            request = {"pattern": pattern, "files": [str(fp) for fp in files], "include": include,
                       "offset": offset, "limit": limit, "backend": backend, "truncated": truncated}
            executable = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
            proc = subprocess.Popen(
                [executable, "-I", "-S", str(Path(__file__).resolve().parent.parent / "search_worker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            )
            job = None
            if os.name == "nt":
                from ..windows_job import attach_process, close_job, terminate_job
                job = attach_process(proc)
                if job is None:
                    proc.kill()
                    proc.communicate()
                    return "Error: cannot supervise search worker"
            try:
                try:
                    stdout, _ = proc.communicate(json.dumps(request), timeout=5)
                except subprocess.TimeoutExpired:
                    if job is not None:
                        terminate_job(job)
                    else:
                        proc.kill()
                    proc.communicate()
                    return "Error: search timed out; results incomplete"
            finally:
                if job is not None:
                    close_job(job)
            if proc.returncode:
                return "Error: search worker failed"
            return json.loads(stdout)
        except subprocess.TimeoutExpired:
            return "Error: search timed out; results incomplete"
        except (ValueError, OSError) as exc:
            return f"Error: {exc}"
