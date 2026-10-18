"""Structured commands used by the networking execution boundary."""

from dataclasses import dataclass, field
from typing import Optional, Tuple


class CommandError(ValueError):
    """Raised when a command is not a valid structured operation."""


@dataclass(frozen=True)
class Command:
    """An executable and argument vector that never relies on a shell."""

    executable: str
    arguments: Tuple[str, ...]
    purpose: str
    stdin: Optional[str] = field(default=None, repr=False)
    ignore_failure: bool = False
    timeout_seconds: Optional[float] = None
    timeout_is_success: bool = False
    sensitive: bool = False
    expected_stdout: Optional[str] = field(default=None, repr=False)
    # Run with LC_ALL=C so the output and the error text are English and a program that
    # reads them does not depend on the system language (nmcli follows it).
    c_locale: bool = False

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
        if self.sensitive and (self.timeout_is_success or self.ignore_failure):
            raise CommandError("sensitive commands must fail on errors or timeout")
        if self.expected_stdout is not None and (
            not self.sensitive or self.stdin is not None
            or not 1 <= len(self.expected_stdout.encode()) <= 4096
        ):
            raise CommandError("expected output requires a bounded sensitive command")

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
