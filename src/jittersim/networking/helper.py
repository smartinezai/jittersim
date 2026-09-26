"""Unix-socket boundary for the privileged networking process."""

import argparse
import json
import logging
import os
import re
import socket
import socketserver
import stat
import threading
import time
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from jittersim.config.models import ValidationError, normalize_domain
from jittersim.engine.access import AccessPlan, access_from_payload
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.plan import EVERYONE_ELSE_QUEUE_ID, NetworkPlan
from jittersim.updates import UpdateError, request_update, update_status

from .access import AccessManager
from .applier import ApplyError, NetworkApplier
from .executor import CommandExecutionError, SubprocessExecutor
from .renderer import band_class
from .router import RouterError, TG3442DERouter
from .topology import Topology

LOGGER = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 256 * 1024
MAX_LOG_DETAIL_CHARS = 512
MAX_CONNECTED_CLIENTS = 256
_MAC_ADDRESS_PATTERN = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_STATION_HEADER_PATTERN = re.compile(
    r"^Station\s+(?P<mac>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\b"
)
_STATION_SIGNAL_PATTERN = re.compile(r"^signal:\s*(-?\d+)\s+dBm$")
_STATION_CONNECTED_TIME_PATTERN = re.compile(r"^connected time:\s*(\d+)\s+seconds$")
_DNS_QUERY_PATTERN = re.compile(
    r"\bIP\s+(?P<source>\d{1,3}(?:\.\d{1,3}){3})\.\d+\s+>\s+"
    r"\d{1,3}(?:\.\d{1,3}){3}\.53:.*?\b"
    r"(?:A|AAAA|HTTPS|SVCB|MX|TXT)\?\s+(?P<domain>[A-Za-z0-9][A-Za-z0-9.-]*)"
)
_TRAFFIC_COUNTER_COMMENT_PATTERN = re.compile(
    r"^usage-(?P<kind>device|service) (?P<identifier>[a-z0-9][a-z0-9_-]{0,63}) "
    r"(?P<direction>upload|download)$"
)
_EVERYONE_ELSE_COMMENT_PATTERN = re.compile(r"^everyone else (?P<direction>upload|download)$")
_SERVICE_QUOTA_MARK_COMMENT_PATTERN = re.compile(
    r"^apply-service-quota [a-z0-9][a-z0-9_-]{0,63} (?P<direction>upload|download)$"
)
_PRIVATE_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)


def _log_detail(value: str) -> str:
    """Keep helper error details on one bounded log line."""

    normalized = " ".join(value[:MAX_LOG_DETAIL_CHARS].split())
    return normalized or "none"


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
        access_manager: Optional[AccessManager] = None,
    ) -> None:
        self._applier = applier
        self._active_plan_path = active_plan_path
        self._access_manager = access_manager
        self._router_factory = router_factory or (
            lambda address, password: TG3442DERouter(address=address, password=password)
        )

    def handle_payload(self, payload: Any) -> Dict[str, Any]:
        """Handle one decoded request without exposing command execution details."""

        if not isinstance(payload, dict):
            return {"ok": False, "error": "request must be an object"}
        action = payload.get("action")
        if action == "normal-access":
            reset = self._handle_reset()
            access_ok = False
            try:
                if self._access_manager is None:
                    raise ValueError("persistent access controls are unavailable")
                self._access_manager.disable()
                access_ok = True
            except (OSError, ValueError, CommandExecutionError):
                LOGGER.exception("could not disable household restrictions")
            if not reset["ok"] or not access_ok:
                return {"ok": False, "error": "normal access was not fully restored",
                        "experiments_stopped": reset["ok"], "access_disabled": access_ok}
            return {"ok": True, "action": action, "experiments_stopped": True, "access_disabled": True}
        if action in ("access-status", "access-apply"):
            if self._access_manager is None:
                return {"ok": False, "error": "persistent access controls are unavailable"}
            try:
                result = (self._access_manager.status() if action == "access-status" else
                          self._access_manager.apply(access_from_payload(payload.get("policy"))))
                return {"ok": True, **result}
            except (OSError, ValueError, CommandExecutionError):
                LOGGER.exception("persistent access operation failed")
                return {"ok": False, "error": "access rules could not be applied"}
        if action in ("update-status", "update-install", "update-rollback"):
            try:
                if action != "update-status":
                    request_update(payload.get("version"), rollback=action == "update-rollback")
                return {"ok": True, **update_status()}
            except (OSError, ValueError, UpdateError):
                LOGGER.exception("release operation failed")
                return {"ok": False, "error": "release operation could not be completed"}
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
        if action == "router-status":
            return self._handle_router_wifi(payload, read_only=True)
        if action == "router-wifi":
            return self._handle_router_wifi(payload)
        if action in ("apply", "apply-experiment"):
            return self._handle_apply(payload.get("plan"), update_access=action == "apply")
        return {"ok": False, "error": "unknown action"}

    def _handle_clients(self) -> Dict[str, Any]:
        station_info_available = True
        try:
            station_output = self._applier.list_connected_stations()
        except CommandExecutionError:
            station_info_available = False
            station_output = ""
        neighbor_info_available = True
        try:
            neighbor_output = self._applier.list_connected_devices()
        except CommandExecutionError:
            neighbor_info_available = False
            neighbor_output = ""
        lease_output = self._applier.list_connected_leases()
        if not station_info_available and not neighbor_info_available:
            return {"ok": False, "error": "connected-device inspection failed"}
        devices = (
            _merge_connected_device_output(
                station_output,
                neighbor_output,
                lease_output,
                observed_at=int(time.time()),
            )
            if station_info_available
            else _fallback_neighbor_devices(neighbor_output)
        )
        return {
            "ok": True,
            "action": "clients",
            "association_status_available": station_info_available,
            "devices": devices,
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
        marks = counters.pop("marks")
        try:
            counters["queues"] = _parse_queue_statistics(
                self._applier.queue_statistics(), marks=marks
            )
        except (CommandExecutionError, ValueError):
            # The byte counters are still useful without queue figures.
            LOGGER.warning("queue statistics inspection failed", exc_info=True)
            counters["queues"] = {"devices": {}}
        return {"ok": True, "action": "traffic-counters", "counters": counters}

    def _handle_apply(self, payload: Any, *, update_access: bool = True) -> Dict[str, Any]:
        try:
            plan = plan_from_payload(payload)
        except PlanCodecError:
            return {"ok": False, "error": "invalid network plan"}
        if plan.access is not None and update_access:
            if self._access_manager is None:
                return {"ok": False, "error": "persistent access controls are unavailable"}
            try:
                self._access_manager.apply(plan.access)
            except (OSError, ValueError, CommandExecutionError):
                LOGGER.exception("persistent access apply failed")
                return {"ok": False, "error": "access rules could not be applied"}
        try:
            report = self._applier.apply(plan=plan)
        except ApplyError as error:
            cause = error.cause
            reset_error = error.reset_error
            state_cleanup_failed = False
            if error.cleanup_attempted and reset_error is None and self._active_plan_path is not None:
                try:
                    self._active_plan_path.unlink(missing_ok=True)
                except OSError:
                    state_cleanup_failed = True
                    LOGGER.exception("could not clear the active plan after apply failure")
            cleanup_result = "completed" if error.cleanup_attempted else "not started"
            if reset_error is not None:
                cleanup_result = (
                    f"failed: {_log_detail(str(reset_error))}; "
                    f"error output: {_log_detail(reset_error.stderr)}"
                )
            LOGGER.exception(
                "network plan apply failed at step %d: %s; exit code %s; "
                "timed out %s; error output: %s; cleanup: %s",
                error.failed_command_index + 1,
                _log_detail(str(cause)),
                cause.return_code if cause.return_code is not None else "unknown",
                cause.timed_out,
                _log_detail(cause.stderr),
                cleanup_result,
            )
            return {
                "ok": False,
                "error": "network plan apply failed",
                "cleanup_failed": reset_error is not None or state_cleanup_failed,
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

    def _handle_router_wifi(self, payload: Dict[str, Any], *, read_only: bool = False) -> Dict[str, Any]:
        router = None
        try:
            admin_password = payload["admin_password"]
            enabled = False if read_only else payload["enabled"]
            if not isinstance(admin_password, str) or not admin_password:
                raise ValueError("router admin password is invalid")
            if not isinstance(enabled, bool):
                raise ValueError("router Wi-Fi state is invalid")
            gateway = self._applier.uplink_info().get("gateway")
            if not gateway:
                raise RouterError("the router gateway could not be determined")
            router = self._router_factory(gateway, admin_password)
            router.login()
            settings = router.read_wifi_settings() if read_only else router.set_wifi_enabled(enabled=enabled)
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


def _validated_flows(raw_flows: Any) -> Dict[str, int]:
    """Check one set of directional byte and packet counts from a helper response."""

    if not isinstance(raw_flows, dict):
        raise HelperClientError("helper returned invalid traffic directions")
    flows: Dict[str, int] = {}
    for field in ("upload_bytes", "download_bytes", "upload_packets", "download_packets"):
        value = raw_flows.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > 2**63 - 1
        ):
            raise HelperClientError("helper returned invalid traffic directions")
        flows[field] = value
    return flows


def _validated_queues(raw_queues: Any) -> Dict[str, Any]:
    """Keep only well-formed per-device queue figures from a helper response."""

    queues: Dict[str, Any] = {"devices": {}}
    everyone_else = raw_queues.get("everyone_else") if isinstance(raw_queues, dict) else None
    if isinstance(everyone_else, dict):
        queues["everyone_else"] = {
            field: _queue_counter(everyone_else.get(field))
            for field in ("upload_dropped", "download_dropped", "upload_queued", "download_queued")
        }
    devices = raw_queues.get("devices") if isinstance(raw_queues, dict) else None
    if not isinstance(devices, dict):
        return queues
    for identifier, values in devices.items():
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{0,63}$", identifier)
            or not isinstance(values, dict)
        ):
            raise HelperClientError("helper returned invalid queue statistics")
        queues["devices"][identifier] = {
            field: _queue_counter(values.get(field))
            for field in ("upload_dropped", "download_dropped", "upload_queued", "download_queued")
        }
    return queues


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

        client = HelperClient(socket_path=self.socket_path, timeout_seconds=60)
        return client._request({"action": "apply", "plan": plan_to_payload(plan)})

    def reset(self) -> Dict[str, Any]:
        """Ask the helper to remove project-owned network state."""

        return self._request({"action": "reset"})

    def apply_experiment(self, *, plan: NetworkPlan) -> Dict[str, Any]:
        """Change scheduled effects without activating pending access-rule edits."""

        client = HelperClient(socket_path=self.socket_path, timeout_seconds=60)
        return client._request({"action": "apply-experiment", "plan": plan_to_payload(plan)})

    def normal_access(self) -> Dict[str, Any]:
        client = HelperClient(socket_path=self.socket_path, timeout_seconds=60)
        return client._request({"action": "normal-access"})

    def access_status(self) -> Dict[str, Any]:
        return self._request({"action": "access-status"})

    def apply_access(self, policy: AccessPlan) -> Dict[str, Any]:
        # DNS restart and recovery can take longer than a status read.
        client = HelperClient(socket_path=self.socket_path, timeout_seconds=60)
        return client._request({"action": "access-apply", "policy": policy.payload()})

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

    def release_status(self) -> Dict[str, Any]:
        return self._request({"action": "update-status"})

    def install_release(self, *, version: str, rollback: bool = False) -> Dict[str, Any]:
        return self._request({"action": "update-rollback" if rollback else "update-install", "version": version})

    def router_wifi_status(self, *, admin_password: str) -> Dict[str, Any]:
        """Read radio state without returning the router Wi-Fi password."""

        return self._request({"action": "router-status", "admin_password": admin_password})

    def set_router_wifi(self, *, admin_password: str, enabled: bool) -> Dict[str, Any]:
        """Enable or disable the upstream router's primary Wi-Fi radios."""

        return self._request(
            {
                "action": "router-wifi",
                "admin_password": admin_password,
                "enabled": enabled,
            }
        )

    def connected_device_snapshot(self) -> Dict[str, Any]:
        """Return current client details and whether association status is known."""

        response = self._request({"action": "clients"})
        devices = response.get("devices")
        association_status_available = response.get("association_status_available")
        if (
            not isinstance(association_status_available, bool)
            or not isinstance(devices, list)
            or len(devices) > MAX_CONNECTED_CLIENTS
        ):
            raise HelperClientError("helper returned invalid connected-device data")
        for device in devices:
            if not isinstance(device, dict):
                raise HelperClientError("helper returned invalid connected-device data")
            mac_address = device.get("mac_address")
            ipv4_address = device.get("ipv4_address")
            hostname = device.get("hostname")
            expected_state = (
                "connected" if device.get("association_verified") else "unknown"
            )
            if (
                not isinstance(mac_address, str)
                or not _MAC_ADDRESS_PATTERN.fullmatch(mac_address)
                or int(mac_address[:2], 16) & 1
                or (ipv4_address is not None and not _valid_ipv4(ipv4_address))
                or device.get("state") != expected_state
                or device.get("association_verified") is not association_status_available
                or (hostname is not None and (not isinstance(hostname, str) or len(hostname) > 64))
                or not _optional_bounded_integer(device.get("signal_dbm"), minimum=-127, maximum=0)
                or not _optional_bounded_integer(
                    device.get("connected_seconds"), minimum=0, maximum=31_536_000
                )
            ):
                raise HelperClientError("helper returned invalid connected-device data")
        return {
            "devices": devices,
            "association_status_available": association_status_available,
        }

    def connected_devices(self) -> List[Dict[str, Any]]:
        """Return clients currently associated with the test access point."""

        return self.connected_device_snapshot()["devices"]

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
        # Helpers from before everyone else existed don't send it.
        raw_everyone_else = counters.get("everyone_else")
        result["everyone_else"] = (
            None if raw_everyone_else is None else _validated_flows(raw_everyone_else)
        )
        result["queues"] = _validated_queues(counters.get("queues"))
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
    access = AccessManager(executor=SubprocessExecutor(timeout_seconds=20), topology=topology,
                           runtime_path=socket_path.parent / "access-policy.json")
    access.restore()
    service = HelperService(
        NetworkApplier(
            executor=SubprocessExecutor(timeout_seconds=timeout_seconds),
            topology=topology,
        ),
        router_factory=router_factory,
        active_plan_path=active_plan_path,
        access_manager=access,
    )
    stopped = threading.Event()

    def maintain_access():
        while not stopped.wait(15):
            try:
                access.check_health()
                if access.applied is not None and access.applied.has_dns_rules:
                    clients = service._handle_clients()
                    if clients.get("ok"):
                        access.refresh_addresses(clients["devices"])
            except Exception:
                LOGGER.exception("could not refresh access device addresses")

    worker = threading.Thread(target=maintain_access, daemon=True, name="access-addresses")
    worker.start()
    with UnixHelperServer(socket_path, service) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stopped.set()
            worker.join(timeout=5)


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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
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


def _valid_ipv4(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        IPv4Address(value)
    except ValueError:
        return False
    return True


def _optional_bounded_integer(
    value: object, *, minimum: int, maximum: int
) -> bool:
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum
    )


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


def _parse_station_output(output: str) -> List[Dict[str, Any]]:
    """Convert `iw` station output into bounded associated-client records."""

    stations: Dict[str, Dict[str, Any]] = {}
    current: Optional[Dict[str, Any]] = None
    for line in output.splitlines():
        match = _STATION_HEADER_PATTERN.match(line.strip())
        if match is not None:
            mac_address = match.group("mac").lower()
            if not _MAC_ADDRESS_PATTERN.fullmatch(mac_address):
                current = None
                continue
            if mac_address not in stations and len(stations) >= MAX_CONNECTED_CLIENTS:
                current = None
                continue
            current = stations.setdefault(
                mac_address,
                {"mac_address": mac_address},
            )
            continue
        if current is None:
            continue
        stripped = line.strip()
        signal_match = _STATION_SIGNAL_PATTERN.fullmatch(stripped)
        if signal_match is not None:
            signal = int(signal_match.group(1))
            if -127 <= signal <= 0:
                current["signal_dbm"] = signal
            continue
        connected_match = _STATION_CONNECTED_TIME_PATTERN.fullmatch(stripped)
        if connected_match is not None:
            connected_seconds = int(connected_match.group(1))
            if connected_seconds <= 31_536_000:
                current["connected_seconds"] = connected_seconds
    return [stations[mac] for mac in sorted(stations)[:MAX_CONNECTED_CLIENTS]]


def _parse_dhcp_lease_output(
    output: str, *, observed_at: int
) -> Dict[str, Dict[str, Any]]:
    """Read valid current leases without retaining expired or malformed rows."""

    leases: Dict[str, Dict[str, Any]] = {}
    for line in output.splitlines()[:MAX_CONNECTED_CLIENTS * 4]:
        fields = line.split()
        if len(fields) < 5:
            continue
        try:
            expiry = int(fields[0])
            address = str(IPv4Address(fields[2]))
        except (ValueError, OverflowError):
            continue
        mac_address = fields[1].lower()
        if (
            not _MAC_ADDRESS_PATTERN.fullmatch(mac_address)
            or int(mac_address[:2], 16) & 1
            or (expiry != 0 and expiry <= observed_at)
        ):
            continue
        hostname = fields[3]
        if hostname == "*" or len(hostname) > 64 or any(ord(char) < 32 for char in hostname):
            hostname = ""
        previous = leases.get(mac_address)
        expiry_rank = float("inf") if expiry == 0 else expiry
        previous_expiry_rank = (
            float("inf") if previous is not None and previous["expiry"] == 0
            else previous["expiry"] if previous is not None else -1
        )
        if previous is None or expiry_rank > previous_expiry_rank:
            leases[mac_address] = {
                "ipv4_address": address,
                "hostname": hostname,
                "expiry": expiry,
            }
    return leases


def _merge_connected_device_output(
    station_output: str,
    neighbor_output: str,
    lease_output: str,
    *,
    observed_at: int,
) -> List[Dict[str, Any]]:
    """Join active Wi-Fi stations to local IPv4 and DHCP details by MAC."""

    neighbors_by_mac: Dict[str, List[Dict[str, str]]] = {}
    for neighbor in _parse_neighbor_output(neighbor_output):
        neighbors_by_mac.setdefault(neighbor["mac_address"], []).append(neighbor)
    leases_by_mac = _parse_dhcp_lease_output(lease_output, observed_at=observed_at)
    devices = []
    for station in _parse_station_output(station_output):
        mac_address = station["mac_address"]
        neighbors = sorted(
            neighbors_by_mac.get(mac_address, []),
            key=lambda neighbor: (
                neighbor["state"] != "reachable",
                int(IPv4Address(neighbor["ipv4_address"])),
            ),
        )
        lease = leases_by_mac.get(mac_address, {})
        reachable_neighbor = next(
            (neighbor for neighbor in neighbors if neighbor["state"] == "reachable"),
            None,
        )
        neighbor = reachable_neighbor or (neighbors[0] if neighbors else None)
        ipv4_address = (
            reachable_neighbor["ipv4_address"]
            if reachable_neighbor is not None
            else lease.get("ipv4_address")
            or (neighbor["ipv4_address"] if neighbor is not None else None)
        )
        device: Dict[str, Any] = {
            **station,
            "ipv4_address": ipv4_address,
            "state": "connected",
            "association_verified": True,
            "neighbor_state": neighbor["state"] if neighbor is not None else None,
            "hostname": lease.get("hostname") or None,
        }
        devices.append(device)
    return devices


def _fallback_neighbor_devices(output: str) -> List[Dict[str, Any]]:
    """Show available IP details without claiming stale neighbors are connected."""

    return [
        {
            "mac_address": neighbor["mac_address"],
            "ipv4_address": neighbor["ipv4_address"],
            "state": "unknown",
            "association_verified": False,
            "neighbor_state": neighbor["state"],
            "hostname": None,
        }
        for neighbor in _parse_neighbor_output(output)[:MAX_CONNECTED_CLIENTS]
    ]


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


def _rule_marks(rule: Dict[str, Any]) -> Set[int]:
    """Return the packet marks an nftables rule sets or matches."""

    marks = set()
    for expression in rule.get("expr", []):
        if not isinstance(expression, dict):
            continue
        mangle = expression.get("mangle")
        match = expression.get("match")
        if isinstance(mangle, dict) and mangle.get("key") == {"meta": {"key": "mark"}}:
            value = mangle.get("value")
        elif (
            isinstance(match, dict)
            and match.get("op") == "=="
            and match.get("left") == {"meta": {"key": "mark"}}
        ):
            value = match.get("right")
        else:
            continue
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**32:
            marks.add(value)
    return marks


def _rule_address(rule: Dict[str, Any], field: str) -> Optional[str]:
    """Return the single IPv4 address an nftables rule matches in one field."""

    for expression in rule.get("expr", []):
        match = expression.get("match") if isinstance(expression, dict) else None
        if (
            isinstance(match, dict)
            and match.get("op") == "=="
            and match.get("left") == {"payload": {"protocol": "ip", "field": field}}
            and isinstance(match.get("right"), str)
        ):
            return match["right"]
    return None


def _queue_counter(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1:
        return value
    return 0


def _parse_queue_statistics(
    outputs: Dict[str, str], *, marks: Dict[str, Set[int]]
) -> Dict[str, Any]:
    """Report what each device's queues dropped or hold, by direction."""

    # A device's traffic can use its own band and its browser service profiles' bands.
    devices_by_class = {
        band_class(mark): device_id
        for device_id, device_marks in marks.items()
        for mark in device_marks
    }
    devices: Dict[str, Dict[str, int]] = {}
    for direction, output in outputs.items():
        try:
            qdiscs = json.loads(output)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("tc statistics output is not valid JSON") from error
        if not isinstance(qdiscs, list):
            raise ValueError("tc statistics output has an invalid shape")
        for qdisc in qdiscs:
            if not isinstance(qdisc, dict):
                continue
            # A device's queue is the child of its band, netem or fq_codel.
            device_id = devices_by_class.get(qdisc.get("parent"))
            if device_id is None:
                continue
            statistics = devices.setdefault(
                device_id,
                {
                    "upload_dropped": 0,
                    "download_dropped": 0,
                    "upload_queued": 0,
                    "download_queued": 0,
                },
            )
            statistics[f"{direction}_dropped"] += _queue_counter(qdisc.get("drops"))
            statistics[f"{direction}_queued"] += _queue_counter(qdisc.get("qlen"))
    queues: Dict[str, Any] = {"devices": devices}
    everyone_else = devices.pop(EVERYONE_ELSE_QUEUE_ID, None)
    if everyone_else is not None:
        queues["everyone_else"] = everyone_else
    return queues


def _rule_counter(rule: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Return a rule's byte and packet counts, when it has a valid counter."""

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
            packets = (
                candidate_packets
                if isinstance(candidate_packets, int)
                and not isinstance(candidate_packets, bool)
                and 0 <= candidate_packets <= 2**63 - 1
                else 0
            )
            return candidate_bytes, packets
    return None


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
        "marks": {},
    }
    device_addresses: Dict[str, str] = {}
    service_quota_marks = []
    for item in payload["nftables"]:
        if not isinstance(item, dict) or not isinstance(item.get("rule"), dict):
            continue
        rule = item["rule"]
        comment = rule.get("comment")
        quota = _SERVICE_QUOTA_MARK_COMMENT_PATTERN.fullmatch(comment or "")
        if quota is not None:
            # An active service limit's rule matches its device by address.
            field = "saddr" if quota.group("direction") == "upload" else "daddr"
            service_quota_marks.append((_rule_address(rule, field), _rule_marks(rule)))
            continue
        everyone_else = _EVERYONE_ELSE_COMMENT_PATTERN.fullmatch(comment or "")
        if everyone_else is not None:
            # Queue figures find everyone else's band by its mark, like a device's.
            counters["marks"].setdefault(EVERYONE_ELSE_QUEUE_ID, set()).update(_rule_marks(rule))
            counts = _rule_counter(rule)
            if counts is not None:
                totals = counters.setdefault(
                    "everyone_else",
                    {
                        "upload_bytes": 0,
                        "download_bytes": 0,
                        "upload_packets": 0,
                        "download_packets": 0,
                    },
                )
                direction = everyone_else.group("direction")
                totals[f"{direction}_bytes"] += counts[0]
                totals[f"{direction}_packets"] += counts[1]
            continue
        match = _TRAFFIC_COUNTER_COMMENT_PATTERN.fullmatch(comment or "")
        if match is None:
            continue
        counts = _rule_counter(rule)
        if counts is None:
            continue
        byte_count, packet_count = counts
        kind = "devices" if match.group("kind") == "device" else "services"
        identifier = match.group("identifier")
        direction = match.group("direction")
        if kind == "devices":
            counters["marks"].setdefault(identifier, set()).update(_rule_marks(rule))
            address = _rule_address(rule, "saddr" if direction == "upload" else "daddr")
            if address is not None:
                device_addresses[address] = identifier
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
    for address, marks in service_quota_marks:
        device_id = device_addresses.get(address)
        if device_id is not None:
            counters["marks"].setdefault(device_id, set()).update(marks)
    return counters


if __name__ == "__main__":
    raise SystemExit(main())
