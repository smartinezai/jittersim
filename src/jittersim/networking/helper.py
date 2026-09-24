"""Unix-socket boundary for the privileged networking process."""

import argparse
import json
import logging
import os
import re
import socket
import socketserver
import stat
import time
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from jittersim.config.models import ValidationError, normalize_domain
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.plan import NetworkPlan

from .applier import ApplyError, NetworkApplier
from .executor import CommandExecutionError, SubprocessExecutor
from .router import RouterError, TG3442DERouter
from .topology import Topology

LOGGER = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 256 * 1024
_MAC_ADDRESS_PATTERN = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_DNS_QUERY_PATTERN = re.compile(
    r"\bIP\s+(?P<source>\d{1,3}(?:\.\d{1,3}){3})\.\d+\s+>\s+"
    r"\d{1,3}(?:\.\d{1,3}){3}\.53:.*?\b"
    r"(?:A|AAAA|HTTPS|SVCB|MX|TXT)\?\s+(?P<domain>[A-Za-z0-9][A-Za-z0-9.-]*)"
)
_TRAFFIC_COUNTER_COMMENT_PATTERN = re.compile(
    r"^usage-(?P<kind>device|service) (?P<identifier>[a-z0-9][a-z0-9_-]{0,63}) "
    r"(?P<direction>upload|download)$"
)
_PRIVATE_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)


class HelperError(RuntimeError):
    """Raised when the helper boundary cannot be started or used safely."""


class HelperClientError(RuntimeError):
    """Raised when a helper request cannot be completed."""


class HelperService:
    """Dispatch validated plan and reset requests to one network applier."""

    def __init__(
        self,
        applier: NetworkApplier,
        router_factory: Optional[Callable[[str, str], TG3442DERouter]] = None,
        active_plan_path: Optional[Path] = None,
    ) -> None:
        self._applier = applier
        self._active_plan_path = active_plan_path
        self._router_factory = router_factory or (
            lambda address, password: TG3442DERouter(address=address, password=password)
        )

    def handle_payload(self, payload: Any) -> Dict[str, Any]:
        """Handle one decoded request without exposing command execution details."""

        if not isinstance(payload, dict):
            return {"ok": False, "error": "request must be an object"}
        action = payload.get("action")
        if action == "ping":
            return {"ok": True, "action": "ping"}
        if action == "reset":
            return self._handle_reset()
        if action == "clients":
            return self._handle_clients()
        if action == "uplink-info":
            return self._handle_uplink_info()
        if action == "observe-dns":
            return self._handle_dns_observation()
        if action == "traffic-counters":
            return self._handle_traffic_counters()
        if action == "configure-ap":
            return self._handle_configure_ap(payload)
        if action == "router-sync-ap":
            return self._handle_router_sync_ap(payload)
        if action == "router-wifi":
            return self._handle_router_wifi(payload)
        if action == "apply":
            return self._handle_apply(payload.get("plan"))
        return {"ok": False, "error": "unknown action"}

    def _handle_clients(self) -> Dict[str, Any]:
        try:
            output = self._applier.list_connected_devices()
        except CommandExecutionError:
            LOGGER.exception("connected-device inspection failed")
            return {"ok": False, "error": "connected-device inspection failed"}
        return {
            "ok": True,
            "action": "clients",
            "devices": _parse_neighbor_output(output),
        }

    def _handle_uplink_info(self) -> Dict[str, Any]:
        try:
            uplink = self._applier.uplink_info()
        except CommandExecutionError:
            LOGGER.exception("upstream connection inspection failed")
            return {"ok": False, "error": "upstream connection inspection failed"}
        return {"ok": True, "action": "uplink-info", "uplink": uplink}

    def _handle_dns_observation(self) -> Dict[str, Any]:
        try:
            output = self._applier.capture_dns_queries()
        except CommandExecutionError:
            LOGGER.exception("DNS observation failed")
            return {"ok": False, "error": "DNS observation failed"}
        return {
            "ok": True,
            "action": "observe-dns",
            "observations": _parse_dns_query_output(output, observed_at=int(time.time())),
        }

    def _handle_traffic_counters(self) -> Dict[str, Any]:
        try:
            output = self._applier.traffic_counters()
            counters = _parse_traffic_counter_output(output)
        except (CommandExecutionError, ValueError):
            LOGGER.exception("traffic counter inspection failed")
            return {"ok": False, "error": "traffic counter inspection failed"}
        return {"ok": True, "action": "traffic-counters", "counters": counters}

    def _handle_apply(self, payload: Any) -> Dict[str, Any]:
        try:
            plan = plan_from_payload(payload)
        except PlanCodecError:
            return {"ok": False, "error": "invalid network plan"}
        try:
            report = self._applier.apply(plan=plan)
        except ApplyError as error:
            state_cleanup_failed = False
            if error.cleanup_attempted and error.reset_error is None and self._active_plan_path is not None:
                try:
                    self._active_plan_path.unlink(missing_ok=True)
                except OSError:
                    state_cleanup_failed = True
                    LOGGER.exception("could not clear the active plan after apply failure")
            LOGGER.exception("network plan apply failed")
            return {
                "ok": False,
                "error": "network plan apply failed",
                "cleanup_failed": error.reset_error is not None or state_cleanup_failed,
            }
        if self._active_plan_path is not None:
            try:
                self._write_active_plan(plan)
            except OSError:
                LOGGER.exception("could not publish the active network plan")
                try:
                    self._applier.reset()
                    self._active_plan_path.unlink(missing_ok=True)
                except (CommandExecutionError, OSError):
                    LOGGER.exception("network cleanup failed after plan publication error")
                return {"ok": False, "error": "network plan state could not be published"}
        return {"ok": True, "action": "apply", "commands_run": report.commands_run}

    def _write_active_plan(self, plan: NetworkPlan) -> None:
        path = self._active_plan_path
        if path is None:
            raise RuntimeError("active plan path is not configured")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(plan_to_payload(plan), output, sort_keys=True)
                output.write("\n")
            os.chmod(temporary, 0o640)
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _handle_configure_ap(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            commands_run = self._applier.configure_access_point(
                ssid=payload["ssid"],
                password=payload["password"],
            )
        except (KeyError, TypeError, ValidationError):
            return {"ok": False, "error": "invalid access point settings"}
        except CommandExecutionError:
            LOGGER.exception("access point configuration failed")
            return {"ok": False, "error": "access point configuration failed"}
        return {"ok": True, "action": "configure-ap", "commands_run": commands_run}

    def _handle_router_sync_ap(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        router = None
        try:
            admin_password = payload["admin_password"]
            if not isinstance(admin_password, str) or not admin_password:
                raise ValueError("router admin password is invalid")
            gateway = self._applier.uplink_info().get("gateway")
            if not gateway:
                raise RouterError("the router gateway could not be determined")
            router = self._router_factory(gateway, admin_password)
            router.login()
            settings = router.read_wifi_settings()
            commands_run = self._applier.configure_access_point(
                ssid=settings.ssid,
                password=settings.password,
            )
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "invalid router credentials"}
        except (CommandExecutionError, RouterError):
            LOGGER.exception("router Wi-Fi import failed")
            return {"ok": False, "error": "router Wi-Fi import failed"}
        finally:
            if router is not None:
                router.close()
        return {
            "ok": True,
            "action": "router-sync-ap",
            "ssid": settings.ssid,
            "router_wifi_enabled": settings.enabled,
            "commands_run": commands_run,
        }

    def _handle_router_wifi(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        router = None
        try:
            admin_password = payload["admin_password"]
            enabled = payload["enabled"]
            if not isinstance(admin_password, str) or not admin_password:
                raise ValueError("router admin password is invalid")
            if not isinstance(enabled, bool):
                raise ValueError("router Wi-Fi state is invalid")
            gateway = self._applier.uplink_info().get("gateway")
            if not gateway:
                raise RouterError("the router gateway could not be determined")
            router = self._router_factory(gateway, admin_password)
            router.login()
            settings = router.set_wifi_enabled(enabled=enabled)
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "invalid router Wi-Fi request"}
        except (CommandExecutionError, RouterError):
            LOGGER.exception("router Wi-Fi change failed")
            return {"ok": False, "error": "router Wi-Fi change failed"}
        finally:
            if router is not None:
                router.close()
        return {
            "ok": True,
            "action": "router-wifi",
            "enabled": settings.enabled,
        }

    def _handle_reset(self) -> Dict[str, Any]:
        try:
            commands_run = self._applier.reset()
            if self._active_plan_path is not None:
                self._active_plan_path.unlink(missing_ok=True)
        except (CommandExecutionError, OSError):
            LOGGER.exception("network reset failed")
            return {"ok": False, "error": "network reset failed"}
        return {"ok": True, "action": "reset", "commands_run": commands_run}


class HelperRequestHandler(socketserver.StreamRequestHandler):
    """Read one newline-delimited JSON request from a Unix socket client."""

    server: "UnixHelperServer"

    def handle(self) -> None:
        line = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_MESSAGE_BYTES:
            self._send_response({"ok": False, "error": "request is too large"})
            return
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_response({"ok": False, "error": "request must be valid JSON"})
            return
        self._send_response(self.server.service.handle_payload(payload))

    def _send_response(self, payload: Dict[str, Any]) -> None:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        self.wfile.write(encoded)


class UnixHelperServer(socketserver.UnixStreamServer):
    """Serve the helper protocol through a filesystem-protected Unix socket."""

    def __init__(self, socket_path: Path, service: HelperService) -> None:
        self.socket_path = socket_path
        self.service = service
        self._prepare_socket_path()
        try:
            super().__init__(str(socket_path), HelperRequestHandler)
            os.chmod(socket_path, 0o660)
        except OSError as error:
            self._remove_socket_path()
            raise HelperError("could not create the helper socket") from error

    def server_close(self) -> None:
        """Close the socket and remove it only when it is still a socket."""

        super().server_close()
        self._remove_socket_path()

    def _prepare_socket_path(self) -> None:
        if not self.socket_path.exists() and not self.socket_path.is_symlink():
            return
        try:
            mode = self.socket_path.lstat().st_mode
        except OSError as error:
            raise HelperError("could not inspect the helper socket path") from error
        if not stat.S_ISSOCK(mode):
            raise HelperError("helper socket path already exists and is not a socket")
        try:
            self.socket_path.unlink()
        except OSError as error:
            raise HelperError("could not remove the stale helper socket") from error

    def _remove_socket_path(self) -> None:
        try:
            if stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                self.socket_path.unlink()
        except FileNotFoundError:
            pass


class HelperClient:
    """Send bounded requests to a local privileged helper."""

    def __init__(self, *, socket_path: Path, timeout_seconds: float = 8.0) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("timeout_seconds must be greater than 0 and no more than 60")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def ping(self) -> Dict[str, Any]:
        """Check whether the helper is accepting requests."""

        return self._request({"action": "ping"})

    def apply(self, *, plan: NetworkPlan) -> Dict[str, Any]:
        """Ask the helper to apply one validated network plan."""

        return self._request({"action": "apply", "plan": plan_to_payload(plan)})

    def reset(self) -> Dict[str, Any]:
        """Ask the helper to remove project-owned network state."""

        return self._request({"action": "reset"})

    def configure_access_point(self, *, ssid: str, password: str) -> Dict[str, Any]:
        """Set the local test AP credentials through the privileged helper."""

        return self._request(
            {"action": "configure-ap", "ssid": ssid, "password": password}
        )

    def sync_access_point_from_router(self, *, admin_password: str) -> Dict[str, Any]:
        """Copy the router's primary Wi-Fi settings into the local test AP."""

        return self._request(
            {"action": "router-sync-ap", "admin_password": admin_password}
        )

    def set_router_wifi(self, *, admin_password: str, enabled: bool) -> Dict[str, Any]:
        """Enable or disable the upstream router's primary Wi-Fi radios."""

        return self._request(
            {
                "action": "router-wifi",
                "admin_password": admin_password,
                "enabled": enabled,
            }
        )

    def connected_devices(self) -> List[Dict[str, str]]:
        """Return IPv4 clients currently visible on the test interface."""

        response = self._request({"action": "clients"})
        devices = response.get("devices")
        if not isinstance(devices, list) or any(
            not isinstance(device, dict) for device in devices
        ):
            raise HelperClientError("helper returned invalid connected-device data")
        return devices

    def uplink_info(self) -> Dict[str, Optional[str]]:
        """Return the Pi's Ethernet connection details."""

        response = self._request({"action": "uplink-info"})
        uplink = response.get("uplink")
        if not isinstance(uplink, dict):
            raise HelperClientError("helper returned invalid upstream connection data")
        interface = uplink.get("interface")
        connection_name = uplink.get("connection_name")
        gateway = uplink.get("gateway")
        if not isinstance(interface, str) or any(
            value is not None and not isinstance(value, str)
            for value in (connection_name, gateway)
        ):
            raise HelperClientError("helper returned invalid upstream connection data")
        return {
            "interface": interface,
            "connection_name": connection_name,
            "gateway": gateway,
        }

    def observe_dns_queries(self) -> List[Dict[str, Any]]:
        """Capture one bounded DNS sample from the test interface."""

        response = self._request({"action": "observe-dns"})
        observations = response.get("observations")
        if not isinstance(observations, list) or any(
            not isinstance(observation, dict) for observation in observations
        ):
            raise HelperClientError("helper returned invalid DNS observations")
        return observations

    def traffic_counters(self) -> Dict[str, Any]:
        """Return total and per-direction traffic counters."""

        response = self._request({"action": "traffic-counters"})
        counters = response.get("counters")
        if not isinstance(counters, dict):
            raise HelperClientError("helper returned invalid traffic counters")
        result: Dict[str, Any] = {}
        for kind in ("devices", "services"):
            values = counters.get(kind)
            if not isinstance(values, dict):
                raise HelperClientError("helper returned invalid traffic counters")
            result[kind] = {}
            for identifier, value in values.items():
                if (
                    not isinstance(identifier, str)
                    or not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{0,63}$", identifier)
                    or isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                    or value > 2**63 - 1
                ):
                    raise HelperClientError("helper returned invalid traffic counters")
                result[kind][identifier] = value
        raw_directions = counters.get("directions")
        if not isinstance(raw_directions, dict):
            raise HelperClientError("helper returned invalid traffic directions")
        directions: Dict[str, Dict[str, Dict[str, int]]] = {}
        for kind in ("devices", "services"):
            values = raw_directions.get(kind)
            if not isinstance(values, dict):
                raise HelperClientError("helper returned invalid traffic directions")
            directions[kind] = {}
            for identifier, counts in values.items():
                if (
                    not isinstance(identifier, str)
                    or not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{0,63}$", identifier)
                    or not isinstance(counts, dict)
                ):
                    raise HelperClientError("helper returned invalid traffic directions")
                directions[kind][identifier] = {}
                for field in (
                    "upload_bytes",
                    "download_bytes",
                    "upload_packets",
                    "download_packets",
                ):
                    value = counts.get(field)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or value < 0
                        or value > 2**63 - 1
                    ):
                        raise HelperClientError("helper returned invalid traffic directions")
                    directions[kind][identifier][field] = value
        result["directions"] = directions
        return result

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise HelperClientError("helper request is too large")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                with connection.makefile("rwb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    response = stream.readline(MAX_MESSAGE_BYTES + 1)
        except OSError as error:
            raise HelperClientError("could not contact the networking helper") from error
        if not response or len(response) > MAX_MESSAGE_BYTES:
            raise HelperClientError("helper returned an invalid response")
        try:
            decoded = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HelperClientError("helper returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise HelperClientError("helper returned an invalid response object")
        if not decoded.get("ok", False):
            raise HelperClientError(str(decoded.get("error", "helper request failed")))
        return decoded


def run_helper(
    *,
    socket_path: Path,
    topology: Topology,
    timeout_seconds: float = 10.0,
    enable_hardware: bool = False,
    router_factory: Optional[Callable[[str, str], TG3442DERouter]] = None,
) -> None:
    """Run the helper only after an explicit hardware-mode opt-in."""

    if not enable_hardware:
        raise HelperError("hardware mode requires --enable-hardware")
    active_plan_path = socket_path.parent / "active-plan.json"
    service = HelperService(
        NetworkApplier(
            executor=SubprocessExecutor(timeout_seconds=timeout_seconds),
            topology=topology,
        ),
        router_factory=router_factory,
        active_plan_path=active_plan_path,
    )
    with UnixHelperServer(socket_path, service) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the jittersim privileged helper.")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--uplink", required=True)
    parser.add_argument("--test-interface", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--enable-hardware", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse helper options and start the explicit hardware process."""

    arguments = _build_parser().parse_args(argv)
    run_helper(
        socket_path=arguments.socket,
        topology=Topology(
            uplink_interface=arguments.uplink,
            test_interface=arguments.test_interface,
        ),
        timeout_seconds=arguments.timeout_seconds,
        enable_hardware=arguments.enable_hardware,
    )
    return 0


def _parse_neighbor_output(output: str) -> List[Dict[str, str]]:
    """Convert Linux neighbor output into safe dashboard records."""

    devices: List[Dict[str, str]] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[1] != "dev" or fields[3] != "lladdr":
            continue
        try:
            ipv4_address = str(IPv4Address(fields[0]))
        except ValueError:
            continue
        mac_address = fields[4].lower()
        if not _MAC_ADDRESS_PATTERN.fullmatch(mac_address):
            continue
        state = fields[5].lower() if len(fields) > 5 else "unknown"
        if state in {"failed", "incomplete"}:
            continue
        devices.append(
            {
                "ipv4_address": ipv4_address,
                "mac_address": mac_address,
                "state": state,
            }
        )
    return sorted(devices, key=lambda device: int(IPv4Address(device["ipv4_address"])))


def _parse_dns_query_output(output: str, *, observed_at: int) -> List[Dict[str, Any]]:
    """Convert bounded tcpdump DNS output into per-device domain aggregates."""

    counts: Dict[tuple, int] = {}
    for line in output.splitlines():
        match = _DNS_QUERY_PATTERN.search(line)
        if match is None:
            continue
        try:
            source = IPv4Address(match.group("source"))
            domain = normalize_domain(match.group("domain"))
        except (ValueError, ValidationError):
            continue
        if not any(source in network for network in _PRIVATE_NETWORKS):
            continue
        key = (str(source), domain)
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "ipv4_address": ipv4_address,
            "domain": domain,
            "query_count": query_count,
            "first_seen": observed_at,
            "last_seen": observed_at,
        }
        for (ipv4_address, domain), query_count in sorted(counts.items())
    ]


def _parse_traffic_counter_output(output: str) -> Dict[str, Any]:
    """Extract project-owned byte and packet counters from nftables JSON."""

    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("nftables counter output is not valid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("nftables"), list):
        raise ValueError("nftables counter output has an invalid shape")

    counters: Dict[str, Any] = {
        "devices": {},
        "services": {},
        "directions": {"devices": {}, "services": {}},
    }
    for item in payload["nftables"]:
        if not isinstance(item, dict) or not isinstance(item.get("rule"), dict):
            continue
        rule = item["rule"]
        comment = rule.get("comment")
        match = _TRAFFIC_COUNTER_COMMENT_PATTERN.fullmatch(comment or "")
        if match is None:
            continue
        byte_count = None
        packet_count = None
        for expression in rule.get("expr", []):
            if not isinstance(expression, dict) or not isinstance(expression.get("counter"), dict):
                continue
            counter = expression["counter"]
            candidate_bytes = counter.get("bytes")
            candidate_packets = counter.get("packets")
            if (
                isinstance(candidate_bytes, int)
                and not isinstance(candidate_bytes, bool)
                and 0 <= candidate_bytes <= 2**63 - 1
            ):
                byte_count = candidate_bytes
                packet_count = (
                    candidate_packets
                    if isinstance(candidate_packets, int)
                    and not isinstance(candidate_packets, bool)
                    and 0 <= candidate_packets <= 2**63 - 1
                    else 0
                )
                break
        if byte_count is None or packet_count is None:
            continue
        kind = "devices" if match.group("kind") == "device" else "services"
        identifier = match.group("identifier")
        direction = match.group("direction")
        counters[kind][identifier] = counters[kind].get(identifier, 0) + byte_count
        directional = counters["directions"][kind].setdefault(
            identifier,
            {
                "upload_bytes": 0,
                "download_bytes": 0,
                "upload_packets": 0,
                "download_packets": 0,
            },
        )
        directional[f"{direction}_bytes"] += byte_count
        directional[f"{direction}_packets"] += packet_count
    return counters


if __name__ == "__main__":
    raise SystemExit(main())
