"""Apply and reset operations built on the structured networking boundary."""

from dataclasses import dataclass
from typing import Optional

from jittersim.engine.plan import NetworkPlan

from .commands import Command
from .executor import CommandExecutionError, CommandExecutor
from .renderer import render_command_plan, render_reset_commands
from .topology import Topology


@dataclass(frozen=True)
class ApplyReport:
    """Describe a completed apply operation."""

    commands_run: int


class ApplyError(RuntimeError):
    """Raised when applying a plan fails or cannot clean up after failure."""

    def __init__(
        self,
        message: str,
        *,
        failed_command_index: int,
        cause: CommandExecutionError,
        reset_error: Optional[CommandExecutionError] = None,
    ) -> None:
        super().__init__(message)
        self.failed_command_index = failed_command_index
        self.cause = cause
        self.reset_error = reset_error


class NetworkApplier:
    """Apply one plan and recover with project-owned cleanup when it fails."""

    def __init__(self, *, executor: CommandExecutor, topology: Topology) -> None:
        self._executor = executor
        self._topology = topology

    def apply(self, *, plan: NetworkPlan) -> ApplyReport:
        """Apply a plan and attempt a bounded reset if any command fails."""

        commands = render_command_plan(plan=plan, topology=self._topology)
        for command_index, command in enumerate(commands):
            try:
                self._executor.run(command)
            except CommandExecutionError as error:
                reset_error = self._try_reset()
                if reset_error is not None:
                    raise ApplyError(
                        "network plan failed and cleanup also failed",
                        failed_command_index=command_index,
                        cause=error,
                        reset_error=reset_error,
                    ) from error
                raise ApplyError(
                    "network plan failed and cleanup completed",
                    failed_command_index=command_index,
                    cause=error,
                ) from error
        return ApplyReport(commands_run=len(commands))

    def reset(self) -> int:
        """Remove only the nftables table and traffic queues owned by jittersim."""

        commands = render_reset_commands(topology=self._topology)
        for command in commands:
            self._executor.run(command)
        return len(commands)

    def list_connected_devices(self) -> str:
        """Read IPv4 neighbors visible on the test interface without changing network state."""

        result = self._executor.run(
            Command(
                executable="ip",
                arguments=("-4", "neigh", "show", "dev", self._topology.test_interface),
                purpose=f"inspect clients on {self._topology.test_interface}",
            )
        )
        return result.stdout

    def capture_dns_queries(self) -> str:
        """Capture a bounded sample of DNS packets arriving from the test network."""

        result = self._executor.run(
            Command(
                executable="tcpdump",
                arguments=(
                    "-i",
                    self._topology.test_interface,
                    "-n",
                    "-l",
                    "-s",
                    "256",
                    "-c",
                    "128",
                    "udp",
                    "port",
                    "53",
                    "or",
                    "tcp",
                    "port",
                    "53",
                ),
                purpose=f"sample DNS queries on {self._topology.test_interface}",
                timeout_seconds=5.0,
                timeout_is_success=True,
            )
        )
        return result.stdout

    def _try_reset(self) -> Optional[CommandExecutionError]:
        try:
            self.reset()
        except CommandExecutionError as error:
            return error
        return None
