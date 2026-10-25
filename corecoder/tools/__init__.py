"""Tool registry."""

from .agent import AgentTool
from .bash import BashTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .todo import TodoWriteTool
from .write import WriteFileTool


def create_tools(state=None):
    """Construct a fresh built-in tool set owned by one session."""
    return [
        BashTool(state=state), ReadFileTool(state=state),
        WriteFileTool(state=state), EditFileTool(state=state),
        GlobTool(state=state), GrepTool(state=state),
        TodoWriteTool(state=state), AgentTool(),
    ]


# Compatibility export.  Production Agents call create_tools and never reuse
# this list; older embedders may still import it for schemas/introspection.
ALL_TOOLS = create_tools()

