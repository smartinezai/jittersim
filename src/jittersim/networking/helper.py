"""Unix-socket boundary for the privileged networking process."""

import argparse
import grp
import hashlib
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
from typing import Any, Callable, Dict, List, Optional, Sequence

from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.models import ValidationError, normalize_domain
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.plan import NetworkPlan

from .applier import MAX_PROTECTED_MACS, ApplyError, NetworkApplier
from .dashboard_access import ACCESS_FILE, AccessError, AccessStore, identify_peer
from .executor import CommandExecutionError, SubprocessExecutor
from .router import (
    ROUTER_CHECK_MESSAGES,
    ROUTER_IMPORT_MESSAGES,
    ROUTER_WIFI_MESSAGES,
    RouterError,
    TG3442DERouter,
)
from .service_addresses import ServiceAddressBook
from .topology import Topology
from .wifi import WifiError
from .wifi_security import LEGACY_MODES, SECURITY_MODES

LOGGER = logging.getLogger(__name__)

HELPER_OUT_OF_DATE_MESSAGE = (
    "The networking helper is out of date and cannot skip protected devices. "
    "Update it before scanning traffic."
)
MAX_MESSAGE_BYTES = 256 * 1024
MAX_HELPER_CONNECTIONS = 8
WIFI_ACTIONS = ("configure-ap", "router-sync-ap", "router-preview-ap", "wifi-reset")
ROUTER_ACTIONS = ("router-sync-ap", "router-preview-ap", "router-wifi", "router-check", "router-status")
MAX_LOG_DETAIL_CHARS = 512
MAX_CONNECTED_CLIENTS = 256
MAX_SERVICE_ADDRESS_LISTS = 15
MAX_SERVICE_ADDRESSES_PER_LIST = 1024
_SERVICE_LIST_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
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

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.reason = reason


class HelperService:
    """Dispatch validated plan and reset requests to one network applier."""

    def __init__(
        self,
        applier: NetworkApplier,
        router_factory: Optional[Callable[[str, str], TG3442DERouter]] = None,
        active_plan_path: Optional[Path] = None,
        access_store: Optional[AccessStore] = None,
        service_address_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._applier = applier
        self._active_plan_path = active_plan_path
        self._active_plan: Optional[NetworkPlan] = None
        self._service_addresses = ServiceAddressBook(clock=service_address_clock)
        self._list_domains: Dict[str, tuple] = {}
        self._network_lock = threading.Lock()
        self._wifi_lock = threading.Lock()
        self._router_lock = threading.Lock()
        self._access_store = access_store
        self._access_lock = threading.Lock()
        self._peer_cache: Dict[str, Any] = {}
        self._router_factory = router_factory or (
            lambda address, password: TG3442DERouter(address=address, password=password)
        )

    def handle_payload(self, payload: Any) -> Dict[str, Any]:
        """Handle one decoded request without exposing command execution details."""

        if isinstance(payload, dict) and payload.get("action") in ROUTER_ACTIONS:
            if not self._router_lock.acquire(blocking=False):
                return {"ok": False, "error": "Another router operation is running. Try again shortly.",
                        "reason": "helper-busy"}
            try:
                return self._handle_payload(payload)
            finally:
                self._router_lock.release()
        return self._handle_payload(payload)

    def _handle_payload(self, payload: Any) -> Dict[str, Any]:

        if not isinstance(payload, dict):
            return {"ok": False, "error": "request must be an object"}
        action = payload.get("action")
        if action == "ping":
            return {"ok": True, "action": "ping"}
        if action == "capabilities":
            return {"ok": True, "general_profile": True, "service_lists": True}
        if action == "dashboard-access":
            if not self._access_lock.acquire(blocking=False):
                return {"ok": False, "error": "Device verification is busy. Try again.",
                        "reason": "helper-busy"}
            try:
                return self._handle_dashboard_access(payload)
            finally:
                self._access_lock.release()
        if action in ("apply", "reset"):
            if not self._network_lock.acquire(blocking=False):
                return {"ok": False, "error": "Pi networking is busy. Try again later.",
                        "reason": "helper-busy"}
            try:
                return (self._handle_reset() if action == "reset"
                        else self._handle_apply(payload.get("plan")))
            finally:
                self._network_lock.release()
        if action == "service-addresses":
            return self._handle_service_addresses(payload.get("addresses"))
        if action == "clients":
            return self._handle_clients()
        if action == "uplink-info":
            return self._handle_uplink_info()
        if action == "observe-dns":
            return self._handle_dns_observation(payload)
        if action == "traffic-counters":
            return self._handle_traffic_counters()
        if action in WIFI_ACTIONS:
            if not self._wifi_lock.acquire(blocking=False):
                return {"ok": False, "error": "Another Pi Wi-Fi change is running. Try again later.",
                        "reason": "wifi-busy"}
            try:
                if action in ("router-sync-ap", "router-preview-ap"):
                    return self._handle_router_sync_ap(payload)
                if not self._network_lock.acquire(blocking=False):
                    return {"ok": False, "error": "Pi networking is busy. Try again later.",
                            "reason": "helper-busy"}
                try:
                    if action == "configure-ap":
                        return self._handle_configure_ap(payload)
                    return self._handle_wifi_reset()
                finally:
                    self._network_lock.release()
            finally:
                self._wifi_lock.release()
        if action == "router-wifi":
            return self._handle_router_wifi(payload)
        if action == "router-check":
            return self._handle_router_check(payload)
        if action == "router-status":
            return self._handle_router_status(payload)
        return {"ok": False, "error": "unknown action"}

    def _handle_dashboard_access(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._access_store
        if store is None:
            return {"ok": False, "error": "Saved dashboard access is not configured."}
        operation = payload.get("operation", "status")
        try:
            if operation == "local-add":
                devices = store.add(payload.get("mac_address"), payload.get("name", ""))
                return {"ok": True, "devices": devices}
            if operation == "local-remove":
                return {"ok": True, "devices": store.remove(payload.get("mac_address"))}
            if operation == "local-list":
                return {"ok": True, "devices": store.list_devices()}
            if operation not in ("status", "register-self", "list", "remove"):
                raise AccessError("Unknown dashboard access action.")
            peer = str(IPv4Address(payload.get("peer")))
            now = time.monotonic()
            cached = self._peer_cache.get(peer)
            if operation == "status" and cached is not None and now - cached[0] < 1:
                identity = dict(cached[1])
            else:
                identity = identify_peer(
                    self._applier, peer, require_reachable=operation == "register-self"
                )
                self._peer_cache = {key: value for key, value in self._peer_cache.items()
                                    if now - value[0] < 1}
                if len(self._peer_cache) < MAX_CONNECTED_CLIENTS:
                    self._peer_cache[peer] = (now, dict(identity))
            devices = ([] if operation == "status" and identity["network"] == "management"
                       else store.list_devices())
            allowed = identity["network"] == "management" or any(
                device["mac_address"] == identity["mac_address"] for device in devices)
            if operation == "register-self":
                if not identity["can_register"]:
                    raise AccessError("This device could not be verified. Connect directly to Pi Wi-Fi "
                                      "and try again or use the Pi recovery command.")
                store.add(identity["mac_address"], payload.get("name", ""))
                allowed = True
            if operation in ("list", "remove"):
                if not allowed:
                    raise AccessError("Allow this device before using dashboard controls.")
                devices = (store.remove(payload.get("mac_address")) if operation == "remove"
                           else devices)
                allowed = identity["network"] == "management" or any(
                    device["mac_address"] == identity["mac_address"] for device in devices)
            result = {"ok": True, **identity, "allowed": allowed}
            if operation in ("list", "remove"):
                result["devices"] = devices
            return result
        except AccessError as error:
            return {"ok": False, "error": str(error)}
        except (ValueError, TypeError, OSError, CommandExecutionError):
            # Do not expose kernel output or saved device records to an unregistered caller.
            return {"ok": False, "error": "Dashboard access could not be verified or saved. "
                    "Check the Pi access file and reconnect directly to its Wi-Fi."}

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

    def _handle_dns_observation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = payload.get("excluded_macs", [])
        try:
            if not isinstance(raw, list) or len(raw) > MAX_PROTECTED_MACS:
                raise ValueError("bad list")
            macs = sorted({normalize_exempt_mac(mac) for mac in raw})
        except ValueError:
            return {"ok": False, "error": "invalid excluded devices"}
        try:
            output = self._applier.capture_dns_queries(macs)
        except CommandExecutionError:
            LOGGER.exception("DNS observation failed")
            return {"ok": False, "error": "DNS observation failed"}
        return {
            "ok": True,
            "action": "observe-dns",
            "excluded_macs": macs,
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
            self._forget_changed_lists(plan)
            self._service_addresses.expire()
            report = self._applier.apply(
                plan=plan,
                service_addresses=self._service_addresses.snapshot(
                    [rule.list_id for rule in plan.service_list_rules]
                ),
            )
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
            if error.cleanup_attempted:
                self._active_plan = None
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
                self._active_plan = None
                return {"ok": False, "error": "network plan state could not be published"}
        self._active_plan = plan
        self._list_domains = {rule.list_id: rule.domains for rule in plan.service_list_rules}
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
        except WifiError as error:
            LOGGER.warning("access point configuration failed")
            return {"ok": False, "error": str(error), "reason": error.reason}
        except CommandExecutionError:
            LOGGER.warning("access point configuration failed")
            return {"ok": False, "error": "access point configuration failed"}
        return {"ok": True, "action": "configure-ap", "commands_run": commands_run}

    def _handle_wifi_reset(self) -> Dict[str, Any]:
        try:
            result = self._applier.reset_wifi_name()
        except WifiError as error:
            LOGGER.warning("Wi-Fi name reset failed: %s", error.reason)
            return {"ok": False, "error": str(error), "reason": error.reason}
        return {"ok": True, "action": "wifi-reset", **result}

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
            if settings.security not in SECURITY_MODES or settings.band is None:
                raise RouterError("unsupported router security", reason="security")
            preview = {"ssid": settings.ssid, "security": settings.security, "band": settings.band}
            fingerprint = hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
            if payload.get("action") == "router-preview-ap":
                return {"ok": True, "action": "router-preview-ap", **preview,
                        "fingerprint": fingerprint, "legacy": settings.security in LEGACY_MODES}
            if payload.get("expected_fingerprint") != fingerprint:
                raise RouterError("stale Wi-Fi preview", reason="changed")
            if settings.security in LEGACY_MODES and payload.get("confirm_legacy") is not True:
                raise RouterError("legacy security not confirmed", reason="legacy")
            if not self._network_lock.acquire(blocking=False):
                return {"ok": False, "error": "Pi networking is busy. Try again later.",
                        "reason": "helper-busy"}
            try:
                commands_run = self._applier.configure_access_point(
                    ssid=settings.ssid,
                    password=settings.password,
                    security=settings.security,
                )
            finally:
                self._network_lock.release()
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "invalid router credentials"}
        except WifiError as error:
            LOGGER.warning("router Wi-Fi import failed")
            message = (str(error) if error.reason in ("wifi-restored", "wifi-restore-failed")
                       else ROUTER_IMPORT_MESSAGES["apply"])
            return {"ok": False, "error": message,
                    "reason": error.reason}
        except RouterError as error:
            LOGGER.warning("router Wi-Fi import failed (%s)", error.reason)
            return {"ok": False, "error": error.import_message, "reason": error.reason}
        except CommandExecutionError:
            LOGGER.warning("router Wi-Fi import failed while updating the Pi access point")
            return {"ok": False, "error": ROUTER_IMPORT_MESSAGES["apply"]}
        finally:
            if router is not None:
                router.close()
        return {
            "ok": True,
            "action": "router-sync-ap",
            "ssid": settings.ssid,
            "security": settings.security,
            "band": settings.band,
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
        except RouterError as error:
            LOGGER.warning("router Wi-Fi change failed (%s)", error.reason)
            return {"ok": False, "error": error.wifi_message}
        except CommandExecutionError:
            LOGGER.warning("router Wi-Fi change failed while inspecting the wired connection")
            return {"ok": False, "error": ROUTER_WIFI_MESSAGES["unreachable"]}
        finally:
            if router is not None:
                router.close()
        return {
            "ok": True,
            "action": "router-wifi",
            "enabled": settings.enabled,
        }

    def _handle_router_status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        router = None
        deadline = time.monotonic() + 35.0
        try:
            password = payload["admin_password"]
            if not isinstance(password, str) or not password:
                raise ValueError("invalid router password")
            gateway = self._applier.uplink_info().get("gateway")
            if not gateway:
                raise RouterError("router gateway is unavailable", reason="unreachable")
            router = self._router_factory(gateway, password)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RouterError("router status deadline expired", reason="unreachable")
            router.limit_operation(min(15.0, remaining))
            router.login(preserve_deadline=True)
            enabled = router.read_wifi_enabled()
            if type(enabled) is not bool:
                raise RouterError("router state is unknown", reason="unsupported")
            return {"ok": True, "action": "router-status", "enabled": enabled}
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "invalid router status request"}
        except RouterError as error:
            return {"ok": False, "error": error.check_message, "reason": error.reason}
        except CommandExecutionError:
            return {"ok": False, "error": ROUTER_CHECK_MESSAGES["unreachable"]}
        finally:
            if router is not None:
                router.close()

    def _handle_router_check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "invalid router credentials"}
        except RouterError as error:
            LOGGER.warning("router password check failed (%s)", error.reason)
            return {"ok": False, "error": error.check_message}
        except CommandExecutionError:
            LOGGER.warning("router password check failed while inspecting the wired connection")
            return {"ok": False, "error": ROUTER_CHECK_MESSAGES["unreachable"]}
        finally:
            if router is not None:
                router.close()
        return {"ok": True, "action": "router-check"}

    def _handle_reset(self) -> Dict[str, Any]:
        self._active_plan = None
        try:
            commands_run = self._applier.reset()
            if self._active_plan_path is not None:
                self._active_plan_path.unlink(missing_ok=True)
        except (CommandExecutionError, OSError):
            LOGGER.exception("network reset failed")
            return {"ok": False, "error": "network reset failed"}
        return {"ok": True, "action": "reset", "commands_run": commands_run}

    def _forget_changed_lists(self, plan: NetworkPlan) -> None:
        """Drop learned addresses of lists that were removed or got other hostnames."""

        wanted = {rule.list_id: rule.domains for rule in plan.service_list_rules}
        for list_id, domains in self._list_domains.items():
            if wanted.get(list_id) != domains:
                self._service_addresses.forget(list_id)

    def _current_plan(self) -> Optional[NetworkPlan]:
        """Return the applied plan, reading the published copy after a helper restart."""

        if self._active_plan is not None:
            return self._active_plan
        if self._active_plan_path is None:
            return None
        try:
            payload = json.loads(self._active_plan_path.read_text(encoding="utf-8"))
            self._active_plan = plan_from_payload(payload)
        except (OSError, ValueError):
            return None
        self._list_domains = {
            rule.list_id: rule.domains for rule in self._active_plan.service_list_rules
        }
        return self._active_plan

    def _handle_service_addresses(self, raw: Any) -> Dict[str, Any]:
        """Add learned destination addresses to the sets of the applied plan's lists."""

        error = {"ok": False, "error": "invalid service addresses"}
        if not isinstance(raw, dict) or len(raw) > MAX_SERVICE_ADDRESS_LISTS:
            return error
        requested: Dict[str, List[IPv4Address]] = {}
        for list_id, values in raw.items():
            if (
                not isinstance(list_id, str)
                or not _SERVICE_LIST_ID_PATTERN.fullmatch(list_id)
                or not isinstance(values, list)
                or len(values) > MAX_SERVICE_ADDRESSES_PER_LIST
                or any(not isinstance(value, str) for value in values)
            ):
                return error
            try:
                requested[list_id] = [IPv4Address(value) for value in values]
            except ValueError:
                return error
        if not self._network_lock.acquire(blocking=False):
            return {"ok": False, "error": "Pi networking is busy. Try again later.",
                    "reason": "helper-busy"}
        try:
            plan = self._current_plan()
            known = {rule.list_id for rule in plan.service_list_rules} if plan else set()
            additions: Dict[str, Sequence[IPv4Address]] = {}
            removals: Dict[str, List[IPv4Address]] = {}
            for list_id, addresses in requested.items():
                if list_id not in known:
                    continue
                fresh, evicted = self._service_addresses.record(
                    list_id, [address for address in addresses if _shapeable_address(address)]
                )
                if fresh:
                    additions[list_id] = fresh
                removals.setdefault(list_id, []).extend(evicted)
            for list_id, expired in self._service_addresses.expire().items():
                removals.setdefault(list_id, []).extend(expired)
            if plan is None or not known:
                return {"ok": True, "action": "service-addresses", "added": 0}
            removals = {
                list_id: [a for a in addresses if a not in additions.get(list_id, ())]
                for list_id, addresses in removals.items()
                if list_id in known
            }
            try:
                self._applier.update_service_addresses(
                    plan=plan, additions=additions, removals=removals
                )
            except CommandExecutionError:
                LOGGER.exception("service address update failed")
                for list_id, fresh in additions.items():
                    self._service_addresses.discard(list_id, fresh)
                return {"ok": False, "error": "service address update failed"}
            return {
                "ok": True,
                "action": "service-addresses",
                "added": sum(len(fresh) for fresh in additions.values()),
            }
        finally:
            self._network_lock.release()


class HelperRequestHandler(socketserver.StreamRequestHandler):
    """Read one newline-delimited JSON request from a Unix socket client."""

    server: "UnixHelperServer"

    def handle(self) -> None:
        self.request.settimeout(3)
        try:
            self._handle_request()
        except (OSError, TimeoutError):
            return

    def _handle_request(self) -> None:
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
        action = payload.get("action") if isinstance(payload, dict) else None
        if action in ("ping", "dashboard-access") or action in WIFI_ACTIONS or action in ROUTER_ACTIONS:
            self._send_response(self.server.service.handle_payload(payload))
            return
        if not self.server.operation_lock.acquire(blocking=False):
            self._send_response({"ok": False, "error": "The networking helper is busy. Try again later.",
                                 "reason": "helper-busy"})
            return
        try:
            self._send_response(self.server.service.handle_payload(payload))
        finally:
            self.server.operation_lock.release()

    def _send_response(self, payload: Dict[str, Any]) -> None:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        self.wfile.write(encoded)


class UnixHelperServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Serve the helper protocol through a filesystem-protected Unix socket."""

    def __init__(self, socket_path: Path, service: HelperService) -> None:
        self.socket_path = socket_path
        self.service = service
        self.operation_lock = threading.Lock()
        self._request_slots = threading.BoundedSemaphore(MAX_HELPER_CONNECTIONS)
        self._prepare_socket_path()
        try:
            super().__init__(str(socket_path), HelperRequestHandler)
            os.chmod(socket_path, 0o660)
        except OSError as error:
            self._remove_socket_path()
            raise HelperError("could not create the helper socket") from error

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            try:
                self._drain_rejected_request(request)
                request.settimeout(1)
                request.sendall(b'{"ok":false,"error":"The networking helper is busy. Try again later.",'
                                b'"reason":"helper-busy"}\n')
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    @staticmethod
    def _drain_rejected_request(request) -> None:
        # Closing with unread input can discard the busy response on Linux.
        deadline = time.monotonic() + 0.25
        remaining = MAX_MESSAGE_BYTES + 1
        while remaining:
            wait = deadline - time.monotonic()
            if wait <= 0:
                return
            request.settimeout(wait)
            chunk = request.recv(min(4096, remaining))
            if not chunk or b"\n" in chunk:
                return
            remaining -= len(chunk)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

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
        if timeout_seconds <= 0 or timeout_seconds > 90:
            raise ValueError("timeout_seconds must be greater than 0 and no more than 90")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def ping(self) -> Dict[str, Any]:
        """Check whether the helper is accepting requests."""

        return self._request({"action": "ping"})

    def dashboard_access(self, operation: str = "status", *, peer: Optional[str] = None,
                         mac_address: Optional[str] = None, name: str = "") -> Dict[str, Any]:
        """Use the saved access list without supplying command arguments."""
        return self._request({"action": "dashboard-access", "operation": operation,
                              "peer": peer, "mac_address": mac_address, "name": name})

    def apply(self, *, plan: NetworkPlan) -> Dict[str, Any]:
        """Ask the helper to apply one validated network plan."""

        if plan.general_profile is not None:
            self.check_general_profile_support()
        if plan.service_list_rules:
            self.check_service_list_support()
        return self._request({"action": "apply", "plan": plan_to_payload(plan)})

    def check_service_list_support(self) -> None:
        """Reject an older running helper before applying service lists."""

        try:
            capabilities = self._request({"action": "capabilities"})
        except HelperClientError as error:
            if error.reason == "helper-busy":
                raise
            raise HelperClientError("Update the Pi helper before applying service lists") from error
        if capabilities.get("service_lists") is not True:
            raise HelperClientError("Update the Pi helper before applying service lists")

    def service_addresses(self, addresses: Dict[str, Sequence[Any]]) -> int:
        """Send learned destination addresses for lists in the applied plan."""

        response = self._request(
            {
                "action": "service-addresses",
                "addresses": {
                    list_id: [str(address) for address in values]
                    for list_id, values in addresses.items()
                },
            }
        )
        added = response.get("added")
        if isinstance(added, bool) or not isinstance(added, int) or added < 0:
            raise HelperClientError("helper returned an invalid service address count")
        return added

    def check_general_profile_support(self) -> None:
        """Reject an older running helper before changing the saved mode."""

        try:
            capabilities = self._request({"action": "capabilities"})
        except HelperClientError as error:
            raise HelperClientError("Update the Pi helper before applying a general profile") from error
        if capabilities.get("general_profile") is not True:
            raise HelperClientError("Update the Pi helper before applying a general profile")

    def reset(self) -> Dict[str, Any]:
        """Ask the helper to remove project-owned network state."""

        return self._request({"action": "reset"})

    def configure_access_point(self, *, ssid: str, password: str) -> Dict[str, Any]:
        """Set the local test AP credentials through the privileged helper."""

        client = HelperClient(socket_path=self.socket_path, timeout_seconds=90)
        return client._request(
            {"action": "configure-ap", "ssid": ssid, "password": password}
        )

    def reset_wifi_name(self) -> Dict[str, Any]:
        """Reset only the Pi Wi-Fi name without dashboard or router credentials."""

        client = HelperClient(socket_path=self.socket_path, timeout_seconds=90)
        return client._request({"action": "wifi-reset"})

    def preview_router_wifi(self, *, admin_password: str) -> Dict[str, Any]:
        """Inspect router security without exposing or applying its password."""
        return self._request({"action": "router-preview-ap", "admin_password": admin_password},
                             timeout_seconds=180.0)

    def sync_access_point_from_router(self, *, admin_password: str,
                                     expected_fingerprint: str, confirm_legacy: bool = False) -> Dict[str, Any]:
        """Copy the router's primary Wi-Fi settings into the local test AP."""

        return self._request(
            {"action": "router-sync-ap", "admin_password": admin_password,
             "expected_fingerprint": expected_fingerprint, "confirm_legacy": confirm_legacy},
            timeout_seconds=180.0,
        )

    def set_router_wifi(self, *, admin_password: str, enabled: bool) -> Dict[str, Any]:
        """Enable or disable the upstream router's primary Wi-Fi radios."""

        return self._request(
            {
                "action": "router-wifi",
                "admin_password": admin_password,
                "enabled": enabled,
            },
            timeout_seconds=150.0,
        )

    def check_router_password(self, *, admin_password: str) -> Dict[str, Any]:
        """Sign in to the upstream router once and sign out again, changing nothing."""

        return self._request(
            {"action": "router-check", "admin_password": admin_password},
            timeout_seconds=90.0,
        )

    def read_router_wifi_status(self, *, admin_password: str) -> Dict[str, Any]:
        """Read current Wi-Fi activity without changing router settings."""
        return self._request({"action": "router-status", "admin_password": admin_password},
                             timeout_seconds=45.0)

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

    def observe_dns_queries(self, excluded_macs: Sequence[str] = ()) -> List[Dict[str, Any]]:
        """Capture one bounded DNS sample that leaves out the given devices."""

        try:
            macs = sorted({normalize_exempt_mac(mac) for mac in excluded_macs})
        except ValueError as error:
            raise HelperClientError("invalid excluded devices") from error
        if len(macs) > MAX_PROTECTED_MACS:
            raise HelperClientError("too many excluded devices")
        response = self._request({"action": "observe-dns", "excluded_macs": macs})
        if macs and response.get("excluded_macs") != macs:
            raise HelperClientError(HELPER_OUT_OF_DATE_MESSAGE)
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

    def _request(
        self, payload: Dict[str, Any], *, timeout_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise HelperClientError("helper request is too large")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds if timeout_seconds is None else timeout_seconds)
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
            reason = decoded.get("reason")
            if reason not in ("wifi-busy", "helper-busy", "wifi-failed",
                              "wifi-restored", "wifi-restore-failed"):
                reason = None
            raise HelperClientError(str(decoded.get("error", "helper request failed")), reason=reason)
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
    applier = NetworkApplier(executor=SubprocessExecutor(timeout_seconds=timeout_seconds),
                             topology=topology)
    try:
        applier.recover_wifi()
    except (WifiError, CommandExecutionError):
        LOGGER.warning("Pending Wi-Fi recovery failed. Repair jittersim-ap through management access.")
    service = HelperService(
        applier,
        router_factory=router_factory,
        active_plan_path=active_plan_path,
        access_store=AccessStore(ACCESS_FILE, group_id=grp.getgrnam("jittersim").gr_gid),
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


def _shapeable_address(address: IPv4Address) -> bool:
    """Keep local, special-purpose and test-network addresses out of service sets."""

    return not (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or any(address in network for network in _PRIVATE_NETWORKS)
    )


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
