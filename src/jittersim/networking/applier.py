"""Apply and reset operations built on the structured networking boundary."""

import json
import re
from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from jittersim.config.exemptions import MAX_UNAFFECTED_DEVICES, normalize_exempt_mac
from jittersim.config.models import ValidationError
from jittersim.engine.plan import NetworkPlan

from .commands import Command, CommandResult
from .executor import CommandExecutionError, CommandExecutor
from .renderer import (
    DOWNLOAD_TABLE,
    INGRESS_HANDLE,
    PROJECT_TABLE,
    ROOT_HANDLE,
    UPLOAD_DEVICE,
    corrupting_upload_queues,
    render_command_plan,
    render_live_queue_changes,
    render_reset_commands,
    render_service_address_commands,
)
from .topology import Topology
from .wifi import WifiController


# Every protected device must be skippable so the two limits are one number.
MAX_PROTECTED_MACS = MAX_UNAFFECTED_DEVICES


@dataclass(frozen=True)
class ApplyReport:
    """Describe a completed apply operation."""

    commands_run: int
    counters_preserved: bool = False
    # Upload corruption ran before the Pi forwards (on the ifb device). False
    # means the plan's upload corruption, if any, was left out.
    upload_corruption_applied: bool = False


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
        self._installed_plan: Optional[NetworkPlan] = None
        self._installed_service_addresses: Dict[str, Set[IPv4Address]] = {}
        # The ifb device the installed plan's upload corruption runs on, or None.
        self._installed_upload_device: Optional[str] = None
        # Cleared for this process once the ifb path failed, so later applies
        # go straight to the fallback instead of failing and resetting again.
        self._upload_device_usable = True
        self._last_commands: Sequence[Command] = ()

    def apply(
        self,
        *,
        plan: NetworkPlan,
        service_addresses: Optional[Mapping[str, Sequence[IPv4Address]]] = None,
    ) -> ApplyReport:
        """Apply a plan and attempt a bounded reset if any command fails.

        `service_addresses` seeds each service list's set with the addresses
        already learned for it, so the new rules never start empty.

        Upload corruption runs on an ifb device fed from the test interface's
        ingress. When that device or its tc actions are not available, the plan
        is applied again without upload corruption, never with damage toward
        the router.
        """

        upload_device = None
        if self._upload_device_usable and corrupting_upload_queues(plan):
            upload_device = self._prepare_upload_device()
        try:
            return self._apply(plan, service_addresses, upload_device)
        except ApplyError as error:
            if upload_device is None or not self._failed_on_upload_device(error):
                raise
            self._upload_device_usable = False
        return self._apply(plan, service_addresses, None)

    def _failed_on_upload_device(self, error: "ApplyError") -> bool:
        commands = self._last_commands
        index = error.failed_command_index
        if not 0 <= index < len(commands):
            return False
        command = commands[index]
        return UPLOAD_DEVICE in command.arguments or INGRESS_HANDLE in command.arguments

    def _prepare_upload_device(self) -> Optional[str]:
        """Create and raise the ifb device, or return None when the kernel has none."""

        if not self._link_exists(UPLOAD_DEVICE):
            created = self._executor.run(Command(
                executable="ip",
                arguments=("link", "add", "name", UPLOAD_DEVICE, "type", "ifb"),
                purpose=f"create {UPLOAD_DEVICE} for upload queues",
                ignore_failure=True,
            ))
            if created.return_code != 0:
                self._upload_device_usable = False
                return None
        raised = self._executor.run(Command(
            executable="ip",
            arguments=("link", "set", "dev", UPLOAD_DEVICE, "up"),
            purpose=f"start {UPLOAD_DEVICE}",
            ignore_failure=True,
        ))
        if raised.return_code != 0:
            self._upload_device_usable = False
            return None
        return UPLOAD_DEVICE

    def _link_exists(self, name: str) -> bool:
        result = self._executor.run(Command(
            executable="ip",
            arguments=("-j", "link", "show", "dev", name),
            purpose=f"check whether {name} exists",
            ignore_failure=True,
        ))
        return result.return_code == 0 and bool(result.stdout.strip())

    def _ingress_present(self) -> bool:
        result = self._executor.run(Command(
            executable="tc",
            arguments=("-j", "qdisc", "show", "dev", self._topology.test_interface, "ingress"),
            purpose=f"inspect the upload intake on {self._topology.test_interface}",
            ignore_failure=True,
        ))
        if result.return_code != 0 or not result.stdout.strip():
            return False
        try:
            queues = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        return isinstance(queues, list) and any(
            isinstance(queue, dict) and queue.get("kind") == "ingress" for queue in queues
        )

    def _remove_upload_redirect(self) -> int:
        """Remove the test interface's intake and the ifb device when present."""

        removed = 0
        if self._ingress_present():
            self._run_checked(Command(
                executable="tc",
                arguments=("qdisc", "del", "dev", self._topology.test_interface,
                           "handle", INGRESS_HANDLE, "ingress"),
                purpose=f"remove the upload intake from {self._topology.test_interface}",
            ))
            removed += 1
        if self._link_exists(UPLOAD_DEVICE):
            self._run_checked(Command(
                executable="ip",
                arguments=("link", "del", "dev", UPLOAD_DEVICE),
                purpose=f"remove {UPLOAD_DEVICE}",
            ))
            removed += 1
        return removed

    def _apply(
        self,
        plan: NetworkPlan,
        service_addresses: Optional[Mapping[str, Sequence[IPv4Address]]],
        upload_device: Optional[str],
    ) -> ApplyReport:
        self._last_commands = ()
        roots_present = True
        try:
            interfaces = (self._topology.uplink_interface, self._topology.test_interface)
            if upload_device is not None:
                interfaces += (upload_device,)
            project_roots = {
                interface: self._root_queue(interface, for_apply=True) for interface in interfaces
            }
            roots_present = (
                all(project_roots.values())
                and self._installed_upload_device == upload_device
                and (upload_device is None or self._ingress_present())
            )
            if not plan.queues:
                # prio needs at least two bands, so remove the project roots.
                # The nft script replaces the tables in one transaction, so their
                # deny and block rules stay in place meanwhile.
                for command in render_reset_commands(topology=self._topology):
                    if command.executable == "tc" and self._root_queue(
                        command.arguments[3], for_apply=False
                    ):
                        self._run_checked(command)
            if upload_device is None:
                self._remove_upload_redirect()
            elif not roots_present and self._ingress_present():
                # A full apply rebuilds the intake with the plan's classes.
                self._run_checked(Command(
                    executable="tc",
                    arguments=("qdisc", "del", "dev", self._topology.test_interface,
                               "handle", INGRESS_HANDLE, "ingress"),
                    purpose=f"replace the upload intake on {self._topology.test_interface}",
                ))
        except CommandExecutionError as error:
            raise ApplyError(
                "network plan could not safely change the root queues",
                failed_command_index=0,
                cause=error,
                cleanup_attempted=False,
            ) from error
        commands = None
        if roots_present and self._installed_plan is not None:
            commands = render_live_queue_changes(
                previous=self._installed_plan, plan=plan, topology=self._topology,
                upload_device=upload_device,
            )
        counters_preserved = commands is not None
        wanted_addresses = {
            rule.list_id: set((service_addresses or {}).get(rule.list_id, ()))
            for rule in plan.service_list_rules
        }
        if commands is not None:
            if service_addresses is None:
                wanted_addresses = {
                    key: set(values) for key, values in self._installed_service_addresses.items()
                }
            commands = tuple(commands) + tuple(render_service_address_commands(
                plan=plan,
                additions={
                    key: tuple(values - self._installed_service_addresses.get(key, set()))
                    for key, values in wanted_addresses.items()
                },
                removals={
                    key: tuple(values - wanted_addresses.get(key, set()))
                    for key, values in self._installed_service_addresses.items()
                },
            ))
        if commands is None:
            commands = render_command_plan(
                plan=plan, topology=self._topology, service_addresses=service_addresses,
                upload_device=upload_device,
            )
        self._last_commands = tuple(commands)
        for command_index, command in enumerate(commands):
            try:
                self._run_checked(command)
            except CommandExecutionError as error:
                self._installed_plan = None
                self._installed_service_addresses = {}
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
        self._installed_plan = plan
        self._installed_service_addresses = wanted_addresses
        self._installed_upload_device = upload_device
        return ApplyReport(
            commands_run=len(commands),
            counters_preserved=counters_preserved,
            upload_corruption_applied=upload_device is not None,
        )

    def update_service_addresses(
        self,
        *,
        plan: NetworkPlan,
        additions: Mapping[str, Sequence[IPv4Address]],
        removals: Mapping[str, Sequence[IPv4Address]],
    ) -> int:
        """Change the live address sets of an applied plan without rebuilding it."""

        commands = render_service_address_commands(
            plan=plan, additions=additions, removals=removals
        )
        try:
            for command in commands:
                self._run_checked(command)
        except CommandExecutionError:
            # A partial update leaves the live sets uncertain. Rebuild on the next apply.
            self._installed_plan = None
            self._installed_service_addresses = {}
            raise
        if self._installed_plan == plan:
            for rule in plan.service_list_rules:
                values = self._installed_service_addresses.setdefault(rule.list_id, set())
                values.update(additions.get(rule.list_id, ()))
                values.difference_update(removals.get(rule.list_id, ()))
        else:
            self._installed_plan = None
            self._installed_service_addresses = {}
        return len(commands)

    def reset(self) -> int:
        """Remove only the nftables table and traffic queues owned by jittersim."""

        self._installed_plan = None
        self._installed_service_addresses = {}
        self._installed_upload_device = None
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
        return commands_run + self._remove_upload_redirect()

    def _root_queue(self, interface: str, *, for_apply: bool) -> bool:
        result = self._executor.run(
            Command(
                executable="tc",
                arguments=("-j", "qdisc", "show", "dev", interface, "root"),
                purpose=f"inspect the root queue on {interface}",
                ignore_failure=not for_apply,
            )
        )
        if result.return_code != 0:
            # A reset (Normal access, Panic, router outage) must not fail because
            # the test Wi-Fi interface is gone: it has no queue left to remove.
            if "cannot find device" in result.stderr.lower():
                return False
            raise CommandExecutionError(
                f"could not inspect the root queue on {interface}",
                return_code=result.return_code,
                stdout=result.stdout,
                stderr=result.stderr,
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

    @property
    def test_interface(self) -> str:
        return self._topology.test_interface

    @property
    def uplink_interface(self) -> str:
        return self._topology.uplink_interface

    def dashboard_peer_route(self, peer: str) -> Dict[str, object]:
        """Inspect a validated peer's return route without accepting shell input."""
        from ipaddress import IPv4Address

        address = str(IPv4Address(peer))
        result = self._run_checked(Command(
            executable="ip", arguments=("-j", "-4", "route", "get", address),
            purpose="verify dashboard peer route", timeout_seconds=2,
        ))
        if len(result.stdout) > 32 * 1024:
            raise ValueError("peer route is too large")
        routes = json.loads(result.stdout)
        if not isinstance(routes, list) or len(routes) != 1 or not isinstance(routes[0], dict):
            raise ValueError("invalid peer route")
        return routes[0]

    def dashboard_peer_neighbors(self, peer: str, interface: str) -> List[Dict[str, object]]:
        """Inspect the exact peer on one configured interface."""
        from ipaddress import IPv4Address

        if interface not in (self.test_interface, self.uplink_interface):
            raise ValueError("invalid peer interface")
        # A device filter makes ip omit its name from the returned entries.
        # Keep that name in the output and select the verified interface below.
        result = self._run_checked(Command(
            executable="ip", arguments=("-j", "-4", "neigh", "show", "to",
                                       str(IPv4Address(peer))),
            purpose="verify dashboard peer address", timeout_seconds=2,
        ))
        if len(result.stdout) > 32 * 1024:
            raise ValueError("peer neighbors are too large")
        neighbors = json.loads(result.stdout)
        if not isinstance(neighbors, list) or len(neighbors) > 256:
            raise ValueError("invalid peer neighbors")
        if any(not isinstance(item, dict) for item in neighbors):
            raise ValueError("invalid peer neighbor")
        return [item for item in neighbors if item.get("dev") == interface]

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

    def probe_default_gateway(self) -> Optional[bool]:
        """Return gateway reachability, or None when a safe probe is unavailable."""

        uplink = self.uplink_info()
        gateway = uplink["gateway"]
        if not gateway:
            return None
        result = self._executor.run(
            Command(
                executable="ping",
                arguments=(
                    "-n",
                    "-c",
                    "1",
                    "-W",
                    "1",
                    "-I",
                    self._topology.uplink_interface,
                    gateway,
                ),
                purpose="check reachability of the configured router gateway",
                ignore_failure=True,
                timeout_seconds=2,
            )
        )
        if result.return_code == 0:
            return True
        if result.return_code == 1:
            return False
        raise CommandExecutionError(
            "the configured router gateway could not be checked",
            return_code=result.return_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def capture_dns_queries(self, excluded_macs: Sequence[str] = ()) -> str:
        """Capture a bounded sample of DNS packets arriving from the test network.

        Frames to or from an excluded hardware address are never captured.
        """

        macs = sorted({normalize_exempt_mac(mac) for mac in excluded_macs})
        if len(macs) > MAX_PROTECTED_MACS:
            raise ValueError("too many excluded devices")
        expression = ["udp", "port", "53", "or", "tcp", "port", "53"]
        if macs:
            # libpcap reads and/or from left to right with equal precedence so the group
            # changes nothing today. It keeps the meaning clear and stops a later edit
            # from attaching the exclusions to one protocol only.
            expression = ["("] + expression + [")"]
            for mac in macs:
                expression += ["and", "not", "ether", "host", mac]
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
                    *expression,
                ),
                purpose=f"sample DNS queries on {self._topology.test_interface}",
                timeout_seconds=5.0,
                timeout_is_success=True,
            )
        )
        return result.stdout

    def traffic_counters(self) -> str:
        """Read project-owned nftables byte counters as JSON."""

        entries = []
        for family, table in (("inet", PROJECT_TABLE), ("netdev", DOWNLOAD_TABLE)):
            result = self._executor.run(
                Command(
                    executable="nft",
                    arguments=("-j", "list", "table", family, table),
                    purpose=f"inspect JitterSim {family} traffic counters",
                )
            )
            if len(result.stdout) > 512 * 1024:
                raise CommandExecutionError("traffic counter response is too large")
            entries.extend(json.loads(result.stdout).get("nftables", []))
        return json.dumps({"nftables": entries})

    def queue_statistics(self) -> Dict[str, str]:
        """Read traffic-control queue statistics as JSON for each direction."""

        outputs = {}
        for direction, interface in (
            ("upload", self._topology.uplink_interface),
            ("download", self._topology.test_interface),
        ):
            result = self._executor.run(
                Command(
                    executable="tc",
                    arguments=("-s", "-j", "qdisc", "show", "dev", interface),
                    purpose=f"inspect JitterSim queues on {interface}",
                )
            )
            outputs[direction] = result.stdout
        return outputs

    def queue_stats(self) -> Dict[str, List[Dict[str, Any]]]:
        """Read kernel queue counters on both project interfaces without changing anything."""

        stats: Dict[str, List[Dict[str, Any]]] = {}
        for interface in (self._topology.uplink_interface, self._topology.test_interface):
            result = self._executor.run(
                Command(
                    executable="tc",
                    arguments=("-s", "-j", "qdisc", "show", "dev", interface),
                    purpose=f"read queue counters on {interface}",
                )
            )
            if len(result.stdout) > 512 * 1024:
                raise CommandExecutionError("queue counter response is too large")
            try:
                decoded = json.loads(result.stdout or "[]")
            except json.JSONDecodeError as error:
                raise CommandExecutionError(f"queue counters on {interface} are not JSON") from error
            if not isinstance(decoded, list):
                raise CommandExecutionError(f"unexpected queue counters on {interface}")
            stats[interface] = decoded
        return stats

    def configure_access_point(self, *, ssid: str, password: str, security: str = "WPA3") -> int:
        """Set the local test AP credentials without touching the upstream router."""

        _validate_access_point_settings(ssid=ssid, password=password)
        return WifiController(self._executor, self._topology.test_interface).configure(ssid, password, security)

    def recover_wifi(self) -> bool:
        """Restore a Wi-Fi import interrupted before its recovery copy was removed."""
        return WifiController(self._executor, self._topology.test_interface).recover_pending()

    def reset_wifi_name(self) -> Dict[str, str]:
        """Restore the Pi Wi-Fi name without reading or changing its password."""

        return WifiController(self._executor, self._topology.test_interface).reset_name()

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
    if not password.isascii() or any(ord(character) < 32 or ord(character) == 127
                                   for character in password):
        raise ValidationError("password must contain printable ASCII characters")
