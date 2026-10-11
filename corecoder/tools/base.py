"""Base class for all tools."""

import copy
import threading
from abc import ABC, abstractmethod

from jsonschema import validators

# File-mutating tools (write_file/edit_file) serialize on this, so a parallel
# batch can't interleave read-modify-write on the same file and lose an edit.
# CheckpointStore methods acquire their own lock while a mutation is in
# progress.  Reentrancy is required for the single, consistent lock order
# (file mutation lock -> checkpoint lock) used by write/edit/undo.
FILE_MUTATION_LOCK = threading.RLock()


class Tool(ABC):
    """Minimal tool interface. Subclass this to add new capabilities."""

    name: str
    description: str
    parameters: dict  # JSON Schema for the function args
    # Custom tools must opt in before an Agent child may reuse the same
    # instance.  Built-ins that carry session state implement clone_for_state.
    stateless: bool = False
    # MCP tools retain the server's exact additionalProperties policy.
    remote_schema: bool = False

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Run the tool and return a text result."""
        ...

    def schema(self) -> dict:
        """OpenAI function-calling schema."""
        parameters = copy.deepcopy(self.parameters)
        if not self.remote_schema and parameters.get("type") == "object":
            parameters.setdefault("additionalProperties", False)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }

    def validate_schema(self) -> None:
        """Reject malformed schemas while registering a tool."""
        schema = self.schema()["function"]["parameters"]
        validators.validator_for(schema).check_schema(schema)

    def validate_arguments(self, arguments):
        """Return the first JSON Schema validation error, if any."""
        schema = self.schema()["function"]["parameters"]
        if not isinstance(arguments, dict):
            return "arguments must be an object"
        validator = validators.validator_for(schema)(schema)
        error = next(iter(validator.iter_errors(arguments)), None)
        if error is None:
            return None
        path = ".".join(str(p) for p in error.absolute_path)
        return f"{path + ': ' if path else ''}{error.message}"


class ToolOutcome:
    """Structured local outcome while the model receives only text."""

    __slots__ = ("call_id", "content", "message", "name", "stage", "status", "warning")

    def __init__(self, call_id, name, status, content, stage="execute", warning=None, message=None):
        self.call_id, self.name, self.status = call_id, name, status
        self.content, self.stage, self.warning = str(content), stage, warning
        self.message = message or self.content

    def as_error(self):
        return self.status in {"failed", "refused", "cancelled", "unknown"}
