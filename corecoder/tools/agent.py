"""Sub-agent spawning (inspired by Claude Code's AgentTool, 1397 lines).

The idea: for complex sub-tasks, spawn an independent agent with its own
conversation history and tool access. This lets the main agent delegate
work like "go research this codebase and report back" without polluting
its own context window.

The sub-agent runs to completion and returns a text summary.
"""

from typing import ClassVar

from ..state import SessionState
from .base import Tool


class AgentTool(Tool):
    name = "agent"
    description = (
        "Spawn a sub-agent to handle a complex sub-task independently. "
        "The sub-agent has its own context and tool access. Use this for: "
        "researching a codebase, implementing a multi-step change in isolation, "
        "or any task that would benefit from a fresh context window."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What the sub-agent should accomplish",
            },
        },
        "required": ["task"],
    }

    def __init__(self):
        self._parent_agent = None

    def bind_parent(self, parent):
        if self._parent_agent is not None and self._parent_agent is not parent:
            raise ValueError("agent tool is already bound to another parent Agent")
        self._parent_agent = parent

    def clone_for_state(self, state: SessionState):
        return type(self)()

    def execute(self, task: str) -> str:
        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        # import here to avoid circular dep
        from ..agent import Agent
        parent = self._parent_agent
        child_state = parent.state.fork()
        child_tools = []
        for tool in parent.tools:
            if tool.name == "agent":
                continue
            # Clone the actual exposed instance.  Looking up a replacement by
            # name would let a restricted wrapper be replaced by an unrestricted
            # built-in in the child.
            clone = getattr(tool, "clone_for_state", None)
            if clone is not None:
                child_tools.append(clone(child_state))
            elif getattr(tool, "stateless", False):
                child_tools.append(tool)
            elif getattr(tool, "state", None) is not None:
                clone = getattr(tool, "clone_for_state", None)
                if clone is None:
                    return f"Sub-agent error: stateful tool {tool.name!r} cannot be cloned"
            else:
                return f"Sub-agent error: custom tool {tool.name!r} must declare stateless=True or implement clone_for_state(state)"
        sub = Agent(
            llm=parent.llm,
            state=child_state,
            tools=child_tools,
            max_context_tokens=parent.context.max_tokens,
            max_rounds=20,
            # the sub-agent answers to the same consent layer and hooks as the parent
            permission=parent.permission,
            hooks=parent.hooks,
        )

        # Keep the legacy thread-local adapter stable for embedders that still
        # inspect it; actual path decisions use SessionState above.
        from .bash import get_tracked_cwd, set_tracked_cwd
        parent_tracked_cwd = get_tracked_cwd()
        try:
            result = sub.chat(task)
            # trim long results to avoid blowing up parent's context
            if len(result) > 5000:
                result = result[:4500] + "\n... (sub-agent output truncated)"
            return f"[Sub-agent completed]\n{result}"
        except Exception as e:  # noqa: BLE001
            return f"Sub-agent error: {e}"
        finally:
            if get_tracked_cwd() != parent_tracked_cwd:
                set_tracked_cwd(parent_tracked_cwd)
