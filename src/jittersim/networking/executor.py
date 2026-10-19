"""Command execution adapters used by simulation and hardware modes."""

import os
import selectors
import subprocess
import time
from typing import List, Optional, Protocol

from .commands import Command, CommandResult
from .deadlines import command_timeout


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
        "systemctl", "dig", "hostapd_cli", "wpa_cli", "conntrack", "ping",
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

        if command.sensitive:
            return self._run_sensitive(command)

        # Only a command that asks for it gets its own environment. All others inherit.
        # C.UTF-8 (glibc 2.35 and later, Debian) is untranslated and still UTF-8: plain "C"
        # would print a name like "FRITZ!Box Müller" with "?". gettext takes LANGUAGE before
        # LC_ALL unless the locale is exactly "C", so it goes away too.
        extra = {}
        if command.c_locale:
            environment = {**os.environ, "LC_ALL": "C.UTF-8"}
            environment.pop("LANGUAGE", None)
            extra = {"env": environment}
        try:
            result = subprocess.run(
                command.argv,
                input=command.stdin,
                text=True,
                capture_output=True,
                # A Panic deadline shortens it (see deadlines.py).
                timeout=command_timeout(command.timeout_seconds or self.timeout_seconds),
                check=False,
                **extra,
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

    def _run_sensitive(self, command: Command) -> CommandResult:
        """Keep private input and checked output inside the process boundary."""

        timeout = command_timeout(command.timeout_seconds or self.timeout_seconds)
        deadline = time.monotonic() + timeout
        expected = command.expected_stdout.encode() if command.expected_stdout is not None else None
        process = None
        failed = False
        timed_out = False
        return_code = None
        try:
            process = subprocess.Popen(
                command.argv,
                stdin=subprocess.PIPE if command.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE if expected is not None else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "HOME": "/dev/null", "XDG_CACHE_HOME": "/dev/null"},
            )
            if expected is None:
                process.communicate(
                    input=command.stdin.encode() if command.stdin is not None else None,
                    timeout=max(0.001, deadline - time.monotonic()),
                )
            else:
                # Read at most the expected response plus one excess byte. A
                # noisy or stalled child cannot grow this private buffer.
                actual = bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise subprocess.TimeoutExpired(command.argv, timeout)
                        chunk = os.read(process.stdout.fileno(), len(expected) + 1 - len(actual))
                        if not chunk:
                            break
                        actual.extend(chunk)
                        if len(actual) > len(expected):
                            failed = True
                            break
                failed = failed or actual != expected
                if not failed:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
            return_code = process.poll()
            failed = failed or return_code != 0
        except subprocess.TimeoutExpired:
            failed = True
            timed_out = True
        except OSError:
            failed = True
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                except OSError:
                    failed = True
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    failed = True
                    timed_out = True
                except OSError:
                    failed = True
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            failed = True
        # Raise outside exception handlers so a child exception cannot retain
        # private output through an attached exception context.
        if failed:
            raise CommandExecutionError(
                "private networking command failed",
                return_code=return_code,
                timed_out=timed_out,
            )
        return CommandResult(return_code=0)
