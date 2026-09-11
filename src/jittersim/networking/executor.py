"""Command execution adapters used by simulation and hardware modes."""

from typing import List, Optional, Protocol

from .commands import Command, CommandResult


class CommandExecutionError(RuntimeError):
    """Raised when a network operation cannot be completed."""


class CommandExecutor(Protocol):
    """The narrow execution interface used by the engine."""

    def run(self, command: Command) -> CommandResult:
        """Execute one structured command and return its captured result."""


class FakeExecutor:
    """Record commands for tests without changing the host network."""

    def __init__(self, fail_on_index: Optional[int] = None) -> None:
        self.commands: List[Command] = []
        self.fail_on_index = fail_on_index

    def run(self, command: Command) -> CommandResult:
        """Record a command and optionally simulate a command failure."""

        command_index = len(self.commands)
        self.commands.append(command)
        if command_index == self.fail_on_index:
            raise CommandExecutionError(
                f"simulated failure while executing command {command_index}: {command.purpose}"
            )
        return CommandResult(return_code=0)
