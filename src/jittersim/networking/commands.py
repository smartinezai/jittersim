"""Structured commands used by the networking execution boundary."""

from dataclasses import dataclass
from typing import Tuple


class CommandError(ValueError):
    """Raised when a command is not a valid structured operation."""


@dataclass(frozen=True)
class Command:
    """An executable and argument vector that never relies on a shell."""

    executable: str
    arguments: Tuple[str, ...]
    purpose: str

    def __post_init__(self) -> None:
        if not self.executable or "/" in self.executable or " " in self.executable:
            raise CommandError("executable must be a bare command name")
        if not self.arguments or any(not isinstance(argument, str) for argument in self.arguments):
            raise CommandError("arguments must contain at least one string")
        if not self.purpose.strip():
            raise CommandError("purpose must not be empty")

    @property
    def argv(self) -> Tuple[str, ...]:
        """Return the argument vector passed to an executor."""

        return (self.executable,) + self.arguments


@dataclass(frozen=True)
class CommandResult:
    """The captured result of one structured command."""

    return_code: int
    stdout: str = ""
    stderr: str = ""
