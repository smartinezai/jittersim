"""Apply and reset operations built on the structured networking boundary."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from jittersim.config.models import ValidationError
from jittersim.engine.plan import NetworkPlan

from .commands import Command, CommandResult
from .executor import CommandExecutionError, CommandExecutor
from .renderer import ROOT_HANDLE, render_command_plan, render_reset_commands
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
        cleanup_attempted: bool = True,
    ) -> None:
        super().__init__(message)
        self.failed_command_index = failed_command_index
        self.cause = cause
        self.reset_error = reset_error
        self.cleanup_attempted = cleanup_attempted


class NetworkApplier:
    """Apply one plan and recover with project-owned cleanup when it fails."""

    def __init__(
        self,
        *,
        executor: CommandExecutor,
        topology: Topology,
        lease_directory: Path = Path("/var/lib/NetworkManager"),
    ) -> None:
        self._executor = executor
        self._topology = topology
        self._lease_directory = lease_directory

    def apply(self, *, plan: NetworkPlan) -> ApplyReport:
        """Apply a plan and attempt a bounded reset if any command fails."""

        try:
            project_roots = {
                interface: self._root_queue(interface, for_apply=True)
                for interface in (self._topology.uplink_interface, self._topology.test_interface)
            }
            if not plan.queues:
                # prio needs at least two bands, so remove the project roots.
                # The nft script replaces the table in one transaction, so its
                # deny and block rules stay in place meanwhile.
                for command in render_reset_commands(topology=self._topology):
                    if command.executable == "tc" and project_roots[command.arguments[3]]:
                        self._run_checked(command)
        except CommandExecutionError as error:
            raise ApplyError(
                "network plan could not safely change the root queues",
                failed_command_index=0,
                cause=error,
                cleanup_attempted=False,
            ) from error
        commands = render_command_plan(plan=plan, topology=self._topology)
        for command_index, command in enumerate(commands):
            try:
                self._run_checked(command)
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
        project_roots = {
            interface: self._root_queue(interface, for_apply=False)
            for interface in (self._topology.uplink_interface, self._topology.test_interface)
        }
        commands_run = 0
        for command in commands:
            if command.executable == "tc" and not project_roots[command.arguments[3]]:
                continue
            self._run_checked(command)
            commands_run += 1
        return commands_run

    def _root_queue(self, interface: str, *, for_apply: bool) -> bool:
        result = self._executor.run(
            Command(
                executable="tc",
                arguments=("-j", "qdisc", "show", "dev", interface, "root"),
                purpose=f"inspect the root queue on {interface}",
            )
        )
        if not result.stdout.strip():
            return False
        try:
            queues = json.loads(result.stdout)
            if not isinstance(queues, list) or len(queues) > 1:
                raise ValueError("unexpected root queue response")
            if not queues:
                return False
            queue = queues[0]
            handle = queue["handle"]
            kind = queue["kind"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise CommandExecutionError(f"could not inspect the root queue on {interface}") from error
        if handle == ROOT_HANDLE and kind == "prio":
            return True
        if for_apply and handle in {"0:", "0:0"} and kind in {
            "fq_codel", "pfifo_fast", "noqueue", "mq"
        }:
            return False
        if for_apply:
            raise CommandExecutionError(
                f"root queue on {interface} belongs to another network tool"
            )
        return False

    def _run_checked(self, command: Command) -> CommandResult:
        result = self._executor.run(command)
        message = result.stderr.lower()
        absent = command.ignore_failure and (
            "no such file or directory" in message
            or (
                command.executable == "tc"
                and command.arguments[:2] == ("qdisc", "del")
                and "invalid handle" in message
            )
        )
        if result.return_code != 0 and not absent:
            raise CommandExecutionError(
                f"networking command failed: {command.purpose}",
                return_code=result.return_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

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

    def list_connected_stations(self) -> str:
        """Read Wi-Fi stations associated with the test access point."""

        result = self._executor.run(
            Command(
                executable="iw",
                arguments=("dev", self._topology.test_interface, "station", "dump"),
                purpose=f"inspect Wi-Fi clients on {self._topology.test_interface}",
                timeout_seconds=5,
            )
        )
        return result.stdout

    def list_connected_leases(self) -> str:
        """Read bounded DHCP names from this interface's NetworkManager leases."""

        lease_path = self._lease_directory / (
            f"dnsmasq-{self._topology.test_interface}.leases"
        )
        try:
            with lease_path.open("rb") as lease_file:
                contents = lease_file.read(64 * 1024 + 1)
        except OSError:
            return ""
        if len(contents) > 64 * 1024:
            return ""
        return contents.decode("utf-8", errors="replace")

    def uplink_info(self) -> Dict[str, Optional[str]]:
        """Read the Pi's Ethernet connection name and default gateway."""

        interface = self._topology.uplink_interface
        connection_result = self._executor.run(
            Command(
                executable="nmcli",
                arguments=("-g", "GENERAL.CONNECTION", "device", "show", interface),
                purpose=f"inspect the NetworkManager connection on {interface}",
            )
        )
        route_result = self._executor.run(
            Command(
                executable="ip",
                arguments=("-4", "route", "show", "default", "dev", interface),
                purpose=f"inspect the default gateway on {interface}",
            )
        )
        gateway_match = re.search(r"\bvia\s+(\d{1,3}(?:\.\d{1,3}){3})\b", route_result.stdout)
        return {
            "interface": interface,
            "connection_name": connection_result.stdout.strip() or None,
            "gateway": gateway_match.group(1) if gateway_match else None,
        }

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

    def traffic_counters(self) -> str:
        """Read project-owned nftables byte counters as JSON."""

        result = self._executor.run(
            Command(
                executable="nft",
                arguments=("-j", "list", "table", "inet", "jittersim"),
                purpose="inspect JitterSim traffic counters",
            )
        )
        return result.stdout

    def configure_access_point(self, *, ssid: str, password: str) -> int:
        """Set the local test AP credentials without touching the upstream router."""

        _validate_access_point_settings(ssid=ssid, password=password)
        commands = (
            Command(
                executable="nmcli",
                arguments=(
                    "connection",
                    "modify",
                    "jittersim-ap",
                    "802-11-wireless.ssid",
                    ssid,
                    "802-11-wireless-security.key-mgmt",
                    "wpa-psk",
                    "802-11-wireless-security.psk",
                    password,
                ),
                purpose="update the local JitterSim access point credentials",
            ),
            Command(
                executable="nmcli",
                arguments=("connection", "down", "jittersim-ap"),
                purpose="restart the local JitterSim access point",
                ignore_failure=True,
            ),
            Command(
                executable="nmcli",
                arguments=("connection", "up", "jittersim-ap"),
                purpose="start the local JitterSim access point",
            ),
        )
        for command in commands:
            self._executor.run(command)
        return len(commands)

    def _try_reset(self) -> Optional[CommandExecutionError]:
        try:
            self.reset()
        except CommandExecutionError as error:
            return error
        return None


def _validate_access_point_settings(*, ssid: str, password: str) -> None:
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValidationError("ssid must contain between 1 and 32 UTF-8 bytes")
    if any(ord(character) < 32 for character in ssid):
        raise ValidationError("ssid contains a control character")
    if not isinstance(password, str) or not 8 <= len(password) <= 63:
        raise ValidationError("password must contain between 8 and 63 characters")
