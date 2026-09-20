"""Structured commands used by the networking execution boundary."""

from dataclasses import dataclass
from typing import Optional, Tuple


class CommandError(ValueError):
    """Raised when a command is not a valid structured operation."""


@dataclass(frozen=True)
class Command:
    """An executable and argument vector that never relies on a shell."""

    executable: str
    arguments: Tuple[str, ...]
    purpose: str
    stdin: Optional[str] = None
    ignore_failure: bool = False
    timeout_seconds: Optional[float] = None
    timeout_is_success: bool = False

    def __post_init__(self) -> None:
        if not self.executable or "/" in self.executable or " " in self.executable:
            raise CommandError("executable must be a bare command name")
        if not self.arguments or any(not isinstance(argument, str) for argument in self.arguments):
            raise CommandError("arguments must contain at least one string")
        if not self.purpose.strip():
            raise CommandError("purpose must not be empty")
        if self.timeout_seconds is not None and (
            self.timeout_seconds <= 0 or self.timeout_seconds > 60
        ):
            raise CommandError("timeout_seconds must be greater than 0 and no more than 60")
        if self.timeout_is_success and self.timeout_seconds is None:
            raise CommandError("timeout_is_success requires timeout_seconds")

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
