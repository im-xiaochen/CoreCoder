"""Stable structured results for one agent run."""

from dataclasses import dataclass, field

_CODES = {"completed": 0, "max_rounds": 2, "cancelled": 130, "failed": 1}


@dataclass
class RunResult:
    status: str
    text: str = ""
    rounds: int = 0
    tool_errors: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finish_reason: str = "completed"
    error: BaseException | None = None

    @property
    def exit_code(self) -> int:
        return _CODES[self.status]

    def as_dict(self) -> dict:
        return {
            "schema_version": 1,
            "status": self.status,
            "text": self.text,
            "rounds": self.rounds,
            "tool_errors": self.tool_errors,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "exit_code": self.exit_code,
        }


def exit_code(status: str) -> int:
    return _CODES[status]
