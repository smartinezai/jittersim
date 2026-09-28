"""Command execution adapters used by simulation and hardware modes."""

import subprocess
from typing import List, Optional, Protocol

from .commands import Command, CommandResult


class CommandExecutionError(RuntimeError):
    """Raised when a network operation cannot be completed."""

    def __init__(
        self,
        message: str,
        *,
        return_code: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


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
            if command.ignore_failure:
                return CommandResult(return_code=1, stderr="simulated failure")
            raise CommandExecutionError(
                f"simulated failure while executing command {command_index}: {command.purpose}"
            )
        return CommandResult(return_code=0)


class SubprocessExecutor:
    """Execute only supported Linux networking commands with a bounded timeout."""

    _ALLOWED_EXECUTABLES = frozenset((
        "ip", "iw", "nft", "tc", "tcpdump", "nmcli", "unbound-checkconf",
        "systemctl", "dig", "hostapd_cli", "conntrack", "ping",
    ))

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("timeout_seconds must be greater than 0 and no more than 60")
        self.timeout_seconds = timeout_seconds

    def run(self, command: Command) -> CommandResult:
        """Execute one allowlisted command without invoking a shell."""

        if command.executable not in self._ALLOWED_EXECUTABLES:
            raise CommandExecutionError(
                f"unsupported networking executable: {command.executable}"
            )

        try:
            result = subprocess.run(
                command.argv,
                input=command.stdin,
                text=True,
                capture_output=True,
                timeout=command.timeout_seconds or self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            if command.timeout_is_success:
                return CommandResult(return_code=0, stdout=stdout, stderr=stderr)
            raise CommandExecutionError(
                f"networking command timed out: {command.purpose}",
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            ) from error
        except OSError as error:
            raise CommandExecutionError(
                f"could not start networking command: {command.purpose}",
                stderr=str(error),
            ) from error

        command_result = CommandResult(
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        if result.returncode != 0 and not command.ignore_failure:
            raise CommandExecutionError(
                f"networking command failed with exit code {result.returncode}: {command.purpose}",
                return_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return command_result
