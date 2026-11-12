"""Load the installed wheel outside the source tree and run its real CLI entry."""
import importlib.metadata
import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


installed = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(installed))

import corecoder
from corecoder import cli, team
from corecoder.tools.agent import AgentTool


for module in (corecoder, cli, team):
    assert Path(module.__file__).resolve().is_relative_to(installed), module.__file__
dist = importlib.metadata.distribution("corecoder")
entry = next(point for point in dist.entry_points if point.group == "console_scripts" and point.name == "corecoder")
assert entry.value == "corecoder.cli:main"
assert "tasks" in AgentTool.parameters["properties"]
assert team.validate_tasks([{"name": "member", "task": "packaged"}])

# Isolate extensions while keeping config, CLI parsing, Agent, real SDK, and
# the request budget gate. No model call is authorized by the zero budget.
cli.load_mcp_tools = list
cli.load_hooks = lambda: None
sys.argv[:] = ["corecoder", "--api-key", "offline-only", "--base-url", "https://fixture.invalid/v1",
               "-p", "wheel smoke", "--json", "--budget-requests", "0"]
with redirect_stdout(StringIO()) as output:
    try:
        entry.load()()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("CLI did not return the budget-exceeded exit code")
result = json.loads(output.getvalue())
assert result["status"] == "failed" and result["finish_reason"] == "budget_exceeded"
assert result["budget"]["attempts"] == {}
print(json.dumps({"version": dist.version, "source": str(Path(corecoder.__file__).resolve()),
                  "entry": entry.value, "status": result["finish_reason"], "attempts": 0},
                 ensure_ascii=False))
