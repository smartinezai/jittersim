"""Unix-socket boundary for the privileged networking process."""

import argparse
import copy
import io
import grp
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import socketserver
import stat
import threading
import time
from dataclasses import asdict
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from jittersim.diagnostic_privacy import SafeServerErrors, configure_logging
from jittersim.diagnostic_records import recorded_payload

from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.models import ValidationError, normalize_domain
from jittersim.engine.access import AccessPlan, access_from_payload
from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.plan import EVERYONE_ELSE_QUEUE_ID, NetworkPlan
from jittersim.release_uploads import UploadBusy, prepare_lock
from jittersim.updates import (
    UpdateError,
    online_overview,
    request_online_check,
    request_update,
    scheduled_online_check,
    set_online_settings,
    stage_upload,
    update_status,
)
from jittersim.panic_guard import PANIC_GUARD_FORMAT, PanicGuard
from jittersim.request_workers import RequestWorkers, WorkersStillRunning
from jittersim.socket_deadlines import DeadlineReader, read_line

from .adblocking import AdRouting, AdStore
from .access import AccessManager
from .applier import MAX_PROTECTED_MACS, ApplyError, NetworkApplier
from .dashboard_access import (
    ACCESS_FILE, AccessError, AccessStore, identify_peer, is_active, normalize_mac,
)
from .executor import CommandExecutionError, SubprocessExecutor
from .renderer import queue_handle, queue_kind, queue_parent
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
    "Restart the JitterSim services or reinstall the release."
)
WIFI_DETAILS_MISSING_MESSAGE = "The router did not return a Wi-Fi name or password."
MAX_MESSAGE_BYTES = 256 * 1024
MAX_HELPER_CONNECTIONS = 8
# Apply and reset wait this long for a read to finish before they answer helper-busy.
# Reads never wait so a dashboard poll cannot make Panic fail. Normal access is the
# Panic action and waits like Reset. Access apply carries Block globally, Unblock,
# Apply access rules and the device quick action so a poll must not refuse it either.
WRITE_ACTIONS = ("apply", "apply-experiment", "reset", "recover-reset", "normal-access", "access-apply", "ad-blocking-configure")
WRITE_LOCK_WAIT_SECONDS = 3.0
ACCESS_STATUS_LOCK_WAIT_SECONDS = 0.5
# Each waiting status read holds one of the MAX_HELPER_CONNECTIONS request slots.
# At most this many wait at once, so the access lock holder and its waiters never
# take more than half the slots and Panic (normal-access) and Reset always find one.
# Further status reads answer helper-busy at once, as before the short wait existed.
ACCESS_STATUS_MAX_WAITERS = 3
WIFI_ACTIONS = ("configure-ap", "router-sync-ap", "router-preview-ap", "wifi-reset")
ROUTER_ACTIONS = (
    "router-sync-ap",
    "router-preview-ap",
    "router-wifi",
    "router-check",
    "router-status",
    "router-wifi-credentials",
)
MAX_LOG_DETAIL_CHARS = 512
MAX_CONNECTED_CLIENTS = 256
MAX_SERVICE_ADDRESS_LISTS = 15
MAX_SERVICE_ADDRESSES_PER_LIST = 1024
MAX_QUEUE_STATS_ENTRIES = 600
QUEUE_STATS_INVALID_MESSAGE = "helper returned invalid queue statistics"
QUEUE_STATS_COUNTERS = (
    "packets", "bytes", "drops", "overlimits", "requeues", "backlog_bytes", "qlen"
)
_QUEUE_DEVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,127}$")
_QUEUE_HANDLE_PATTERN = re.compile(r"^[0-9a-f]{1,4}:$")
_QUEUE_GENERATION_PATTERN = re.compile(r"^[0-9a-f-]{1,24}$")
_QUEUE_IMPAIRMENT_LIMITS = {
    "delay_ms": 60_000,
    "jitter_ms": 60_000,
    "loss_percent": 100,
    "reorder_percent": 100,
    "duplicate_percent": 100,
    "corrupt_percent": 100,
}
_SERVICE_LIST_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
GATEWAY_CHECK_INTERVAL_SECONDS = 5.0
# How often the helper looks whether the daily online release check is due.
ONLINE_CHECK_INTERVAL_SECONDS = 15 * 60
GATEWAY_FAILURES_TO_DOWN = 3
GATEWAY_SUCCESSES_TO_UP = 2
GATEWAY_RESET_RETRY_SECONDS = 30.0
MAX_GATEWAY_STATE_BYTES = 4096
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
        gateway_state_path: Optional[Path] = None,
        access_manager: Optional[AccessManager] = None,
        panic_guard: Optional[PanicGuard] = None,
        homepage_database: Optional[Path] = None,
        ad_blocking_store: Optional[AdStore] = None,
    ) -> None:
        self._applier = applier
        self._homepage_database = homepage_database
        self._ad_blocking_store = ad_blocking_store
        self._active_plan_path = active_plan_path
        self._active_plan: Optional[NetworkPlan] = None
        # A new boot nonce and a count of applied plans tell the dashboard when the
        # queues were replaced even if the settings stayed the same.
        self._boot_nonce = secrets.token_hex(4)
        self._homepage_epoch = secrets.token_bytes(16).hex()
        self._plan_count = 0
        self._queue_read_error: Optional[str] = None
        self._service_addresses = ServiceAddressBook(clock=service_address_clock)
        self._list_domains: Dict[str, tuple] = {}
        # Reentrant: normal access resets under the lock and the gateway
        # monitor retries a reset while it holds it.
        self._network_lock = threading.Lock()
        self._wifi_lock = threading.Lock()
        self._router_lock = threading.Lock()
        self._access_store = access_store
        self._access_lock = threading.Lock()
        self._access_status_waiters = threading.BoundedSemaphore(ACCESS_STATUS_MAX_WAITERS)
        self._peer_cache: Dict[str, Any] = {}
        self._gateway_state_path = gateway_state_path
        self._access_manager = access_manager
        self._persistence_enabled = False
        self._persistence_mode_initialized = False
        self._gateway_state = "unknown"
        self._outage_observed = False
        self._outage_stop_latched = False
        self._panic_stop_latched = False
        self._household_stop_latched = False
        self._panic_recovery_failed = False
        self._control_revision = secrets.token_bytes(16).hex()
        self._revision_lock = threading.Lock()
        self._control_revision_required = False
        self._reset_pending = False
        self._recovery_pending = False
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._next_reset_retry_at = 0.0
        self._gateway_state_error = False
        self._load_gateway_state()
        self._panic_guard = panic_guard
        self._panic_guard_error = False
        if panic_guard is not None:
            try:
                guard = panic_guard.provision(gateway_state_path)
                self._panic_stop_latched = guard["panic_stop_latched"]
                self._household_stop_latched = guard["household_stop_latched"]
                self._panic_recovery_failed = guard["panic_recovery_failed"]
                self._control_revision_required = guard["control_revision_required"]
                if (self._panic_stop_latched and self._active_plan_path is not None
                        and self._active_plan_path.exists()):
                    self._reset_pending = True
                    self._next_reset_retry_at = 0.0
            except OSError:
                LOGGER.exception("persistent Panic stop state could not be read")
                self._panic_stop_latched = True
                self._household_stop_latched = True
                self._panic_recovery_failed = True
                self._control_revision_required = True
                self._panic_guard_error = True
        self._router_factory = router_factory or (
            lambda address, password: TG3442DERouter(address=address, password=password)
        )

    @recorded_payload
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
        if action == "control-revision":
            with self._revision_lock:
                return {"ok": True, "control_revision": self._control_revision,
                        "recovery_required": self._panic_recovery_failed}
        if action == "normal-access":
            if not self._network_lock.acquire(timeout=WRITE_LOCK_WAIT_SECONDS):
                return {"ok": False, "error": "Pi networking is busy. Try again later.",
                        "reason": "helper-busy"}
            try:
                with self._revision_lock:
                    self._control_revision = secrets.token_bytes(16).hex()
                    self._control_revision_required = True
                    self._panic_stop_latched = True
                    self._household_stop_latched = True
                    self._panic_recovery_failed = True
                self._write_panic_guard()
                self._write_gateway_state(durable=True)
                reset = self._handle_reset()
                access_ok = False
                try:
                    if self._access_manager is None:
                        raise ValueError("persistent access controls are unavailable")
                    self._access_manager.disable()
                    access_ok = True
                except (OSError, ValueError, CommandExecutionError):
                    LOGGER.exception("could not disable household restrictions")
                failed = not reset["ok"] or not access_ok
                saved = self._write_gateway_state(durable=True, panic_recovery_failed=failed)
                saved = self._write_panic_guard(panic_recovery_failed=failed or not saved) and saved
                with self._revision_lock:
                    self._panic_recovery_failed = failed or not saved
                if not saved:
                    self._write_panic_guard()
            finally:
                self._network_lock.release()
            if not saved:
                return {"ok": False, "error": "normal access recovery state could not be saved",
                        "experiments_stopped": reset["ok"], "access_disabled": access_ok}
            if not reset["ok"] or not access_ok:
                return {"ok": False, "error": "normal access was not fully restored",
                        "experiments_stopped": reset["ok"], "access_disabled": access_ok}
            return {"ok": True, "action": action, "experiments_stopped": True, "access_disabled": True}
        if action in ("access-status", "access-apply"):
            if self._access_manager is None:
                return {"ok": False, "error": "persistent access controls are unavailable"}
            acquired = False
            try:
                if action == "access-status":
                    result = self._access_manager.status()
                else:
                    acquired = self._network_lock.acquire(timeout=WRITE_LOCK_WAIT_SECONDS)
                    if not acquired:
                        return {"ok": False, "error": "Pi networking is busy. Try again later.", "reason": "helper-busy"}
                    refused = self._control_refusal(payload)
                    if refused is not None:
                        return refused
                    if self._panic_recovery_failed:
                        return {"ok": False, "error": "Retry Panic before applying access rules."}
                    ad_was_running = False
                    if self._ad_blocking_store is not None:
                        try:
                            ad_was_running = self._ad_blocking_store.read()["running"]
                        except (OSError, ValueError):
                            LOGGER.warning("Ad permission could not be confirmed before household changes.")
                    result = self._access_manager.apply(access_from_payload(payload.get("policy")))
                    self._household_stop_latched = False
                    saved = self._write_gateway_state(durable=True)
                    saved = self._write_panic_guard() and saved
                    if not saved:
                        self._block_unconfirmed_intent()
                        self._handle_reset()
                        try:
                            self._access_manager.disable()
                        except (OSError, ValueError, CommandExecutionError):
                            LOGGER.exception("household cleanup failed after stop state save failure")
                        self._write_panic_guard()
                        self._write_gateway_state(durable=True)
                        return {"ok": False, "error": "Household recovery state could not be saved. Retry Panic."}
                    if ad_was_running:
                        try:
                            self._ad_blocking_store.resume()
                        except (OSError, ValueError, CommandExecutionError):
                            LOGGER.warning("Household rules applied but ad permission could not be renewed.")
                return {"ok": True, **result}
            except (OSError, ValueError, CommandExecutionError):
                LOGGER.exception("persistent access operation failed")
                return {"ok": False, "error": self._access_failure_message()}
            finally:
                if acquired:
                    self._network_lock.release()
        if action in ("update-status", "update-install", "update-rollback"):
            try:
                if action != "update-status":
                    request_update(payload.get("version"), rollback=action == "update-rollback")
                return {"ok": True, **update_status()}
            except (OSError, ValueError, UpdateError):
                LOGGER.exception("release operation failed")
                return {"ok": False, "error": "release operation could not be completed"}
        if action == "update-retry":
            identifier = payload.get("retry_id")
            if (set(payload) != {"action", "retry_id"} or not isinstance(identifier, str)
                    or re.fullmatch(r"[0-9a-f]{64}", identifier) is None):
                return {"ok": False, "error": "Check the failed update again before retrying.",
                        "reason": "update-retry-refused"}
            try:
                # A replaced helper with an older backend must refuse this action.
                from jittersim.updates import retry_update
            except ImportError:
                return {"ok": False, "error": "The Pi network helper needs an update before retrying."}
            try:
                return {"ok": True, **retry_update(identifier)}
            except UpdateError as error:
                return {"ok": False, "error": str(error), "reason": "update-retry-refused"}
            except (OSError, ValueError):
                LOGGER.exception("update retry could not be confirmed")
                return {"ok": False, "error": "The update retry could not be confirmed. Check update status."}
        if action in ("update-stage-upload", "update-stage-identified-upload"):
            # The dashboard has streamed an uploaded release into its spool folder. This
            # root helper copies it away, checks the signature and stages it. The refusal
            # text is one of the update manager's plain sentences, never a path or a trace.
            try:
                if action == "update-stage-identified-upload":
                    identifier = payload.get("upload_id")
                    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
                        raise UpdateError("the uploaded release identifier is invalid")
                    record = stage_upload(identifier)
                else:
                    record = stage_upload()
                return {"ok": True, "staged_version": record["version"], **update_status()}
            except UploadBusy as error:
                return {"ok": False, "error": str(error), "reason": "helper-busy"}
            except UpdateError as error:
                LOGGER.warning("uploaded release refused: %s", error)
                return {"ok": False, "error": str(error), "reason": "release-refused"}
            except (OSError, ValueError):
                LOGGER.exception("uploaded release could not be staged")
                return {"ok": False, "error": "the uploaded release could not be staged"}
        if action in ("update-online-check", "update-online-status", "update-online-settings"):
            # The root fetch unit holds the token and does the download. The answer here is only
            # state: a job, a result and two settings.
            try:
                if action == "update-online-check":
                    request_online_check(force=payload.get("force") is True)
                elif action == "update-online-settings" and payload.get("changes") is not None:
                    set_online_settings(payload.get("changes"))
                job = payload.get("job")
                return {"ok": True, **online_overview(job if isinstance(job, str) else None)}
            except UpdateError as error:
                return {"ok": False, "error": str(error)}
            except (OSError, ValueError):
                LOGGER.exception("online release check could not be handled")
                return {"ok": False, "error": "online release check could not be completed"}
        if action in ("homepage-snapshot", "homepage-confirm"):
            from .homepage import helper_confirm, helper_snapshot
            return helper_confirm(self, payload) if action == "homepage-confirm" else helper_snapshot(self, payload)
        if action == "ping":
            return {"ok": True, "action": "ping"}
        if action == "capabilities":
            # Each flag guards a check in HelperClient. Main's features need no flag
            # because their helper actions answer "unknown action" on an older helper.
            return {
                "ok": True,
                "general_profile": True,
                "service_lists": True,
                "service_list_outages": True,
                "protected_dns_capture": True,
                "identified_release_uploads": True,
                "homepage_policy_version": 1,
            }
        if action in ("ad-blocking", "ad-blocking-configure"):
            if self._ad_blocking_store is None:
                return {"ok": False, "error": "Install the current Pi release to use ad blocking."}
            acquired = False
            try:
                if action == "ad-blocking-configure":
                    acquired = self._network_lock.acquire(timeout=WRITE_LOCK_WAIT_SECONDS)
                    if not acquired:
                        return {"ok": False, "error": "Pi networking is busy. Try again later.",
                                "reason": "helper-busy"}
                    refused = self._control_refusal(payload)
                    if refused is not None:
                        return refused
                    if payload.get("resume") and self._panic_recovery_failed:
                        return {"ok": False, "error": "Retry Panic before resuming ad blocking."}
                    if self._panic_stop_latched and not self._ad_blocking_store.intent_path.exists():
                        self._ad_blocking_store.pause()
                status = (
                    self._ad_blocking_store.configure(
                        payload.get("mac_addresses"), payload.get("allowed_domains"),
                        update=payload.get("update", False), resume=payload.get("resume", False),
                    ) if action == "ad-blocking-configure" else self._ad_blocking_store.status()
                )
                return {"ok": True, "ad_blocking": status}
            except (OSError, ValueError, CommandExecutionError):
                return {"ok": False, "error": "Ad-blocking settings could not be applied. Try again."}
            finally:
                if acquired:
                    self._network_lock.release()
        if action == "dashboard-access":
            operation = payload.get("operation", "status")
            if operation == "status":
                # Allow a short burst of dashboard refreshes to take turns while
                # keeping access changes serialized with each authorization check.
                # Only a few may wait so waiters cannot fill the helper request slots.
                acquired = self._access_lock.acquire(blocking=False)
                if not acquired and self._access_status_waiters.acquire(blocking=False):
                    try:
                        acquired = self._access_lock.acquire(timeout=ACCESS_STATUS_LOCK_WAIT_SECONDS)
                    finally:
                        self._access_status_waiters.release()
            else:
                acquired = self._access_lock.acquire(blocking=False)
            if not acquired:
                return {"ok": False, "error": "Device verification is busy. Try again.",
                        "reason": "helper-busy"}
            try:
                return self._handle_dashboard_access(payload)
            finally:
                self._access_lock.release()
        if action in WRITE_ACTIONS:
            explicit_apply = payload.get("explicit_apply", False)
            if not isinstance(explicit_apply, bool):
                return {"ok": False, "error": "explicit_apply must be true or false"}
            if not self._network_lock.acquire(timeout=WRITE_LOCK_WAIT_SECONDS):
                return {"ok": False, "error": "Pi networking is busy. Try again later.",
                        "reason": "helper-busy"}
            try:
                if action in ("reset", "recover-reset"):
                    return self._handle_reset(pause_ad_blocking=action == "reset")
                refused = self._control_refusal(payload)
                if refused is not None:
                    return refused
                return self._handle_apply(
                    payload.get("plan"),
                    update_access=action == "apply",
                    explicit_apply=explicit_apply,
                )
            finally:
                self._network_lock.release()
        if action == "gateway-status":
            with self._network_lock:
                return {"ok": True, "action": action, **self._gateway_status()}
        if action == "gateway-persistence":
            return self._handle_gateway_persistence(payload)
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
        if action == "queue-stats":
            return self._handle_queue_stats()
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
        if action == "router-wifi-credentials":
            return self._handle_router_wifi_credentials(payload)
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
            if operation not in (
                "status",
                "register-self",
                "list",
                "remove",
                "move-address",
                "undo-move",
                "move-address-owned",
                "undo-move-owned",
            ):
                raise AccessError("Unknown dashboard access action.")
            owned = operation in ("move-address-owned", "undo-move-owned")
            if owned:
                move_id = payload.get("move_id")
                if not isinstance(move_id, str) or not re.fullmatch(r"[0-9a-f]{32}", move_id):
                    raise AccessError("An ownership-aware move requires its exact identifier.")
                if operation == "move-address-owned":
                    normalize_mac(payload.get("expected_mac"))
                    if type(payload.get("move_access")) is not bool:
                        raise AccessError("Choose explicitly whether this move transfers access.")
            peer = str(IPv4Address(payload.get("peer")))
            now = time.monotonic()
            cached = self._peer_cache.get(peer)
            if operation == "status" and cached is not None and now - cached[0] < 1:
                identity = dict(cached[1])
            else:
                identity = identify_peer(
                    self._applier,
                    peer,
                    require_reachable=operation
                    in ("register-self", "move-address", "move-address-owned"),
                )
                self._peer_cache = {
                    key: value for key, value in self._peer_cache.items() if now - value[0] < 1
                }
                if len(self._peer_cache) < MAX_CONNECTED_CLIENTS:
                    self._peer_cache[peer] = (now, dict(identity))
            devices = (
                []
                if operation == "status" and identity["network"] == "management"
                else store.list_devices()
            )
            allowed = identity["network"] == "management" or any(
                device["mac_address"] == identity["mac_address"] and is_active(device)
                for device in devices
            )
            moved = False
            if operation in ("move-address", "move-address-owned"):
                # The new address is the verified requester. Nothing from the browser names it.
                if not identity["can_register"]:
                    raise AccessError(
                        "This device could not be verified. Connect directly to Pi Wi-Fi "
                        "and try again."
                    )
                expected = payload.get("expected_mac")
                if expected is not None and identity["mac_address"] != normalize_mac(expected):
                    raise AccessError("The connection changed. Refresh before moving a device.")
                old = payload.get("mac_address")
                if (
                    not allowed
                    and (not owned or payload["move_access"])
                    and any(
                        device["mac_address"] == normalize_mac(old) and is_active(device)
                        for device in devices
                    )
                ):
                    devices = store.move(
                        old, identity["mac_address"], move_id=payload.get("move_id")
                    )
                    moved = allowed = True
            if operation in ("undo-move", "undo-move-owned"):
                if not allowed:
                    raise AccessError("Allow this device before using dashboard controls.")
                devices = store.undo_move(
                    payload.get("mac_address"), move_id=payload.get("move_id")
                )
                allowed = identity["network"] == "management" or any(
                    device["mac_address"] == identity["mac_address"] and is_active(device)
                    for device in devices
                )
            if operation == "register-self":
                if not identity["can_register"]:
                    raise AccessError(
                        "This device could not be verified. Connect directly to Pi Wi-Fi "
                        "and try again or use the Pi recovery command."
                    )
                store.add(identity["mac_address"], payload.get("name", ""))
                allowed = True
            if operation in ("list", "remove"):
                if not allowed:
                    raise AccessError("Allow this device before using dashboard controls.")
                devices = (
                    store.remove(payload.get("mac_address")) if operation == "remove" else devices
                )
                allowed = identity["network"] == "management" or any(
                    device["mac_address"] == identity["mac_address"] and is_active(device)
                    for device in devices
                )
            result = {"ok": True, **identity, "allowed": allowed}
            if operation in ("move-address", "move-address-owned"):
                result["moved"] = moved
            if operation in ("list", "remove", "undo-move", "undo-move-owned"):
                result["devices"] = devices
            return result
        except AccessError as error:
            return {"ok": False, "error": str(error)}
        except (ValueError, TypeError, OSError, CommandExecutionError):
            # Do not expose kernel output or saved device records to an unregistered caller.
            return {
                "ok": False,
                "error": "Dashboard access could not be verified or saved. "
                "Check the Pi access file and reconnect directly to its Wi-Fi.",
            }

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

    def _load_gateway_state(self) -> None:
        path = self._gateway_state_path
        if path is None or not path.exists():
            return
        try:
            with path.open("rb") as state_file:
                contents = state_file.read(MAX_GATEWAY_STATE_BYTES + 1)
            if len(contents) > MAX_GATEWAY_STATE_BYTES:
                raise ValueError("gateway state is too large")
            state = json.loads(contents.decode("utf-8"))
            if not isinstance(state, dict) or state.get("version") != 1:
                raise ValueError("gateway state has an unsupported format")
            if not isinstance(state.get("persistence_enabled"), bool):
                raise ValueError("gateway persistence setting is invalid")
            if state.get("gateway_state") not in ("unknown", "available", "unavailable"):
                raise ValueError("gateway health state is invalid")
            for field in (
                "outage_observed",
                "outage_stop_latched",
                "reset_pending",
                "recovery_pending",
            ):
                if not isinstance(state.get(field), bool):
                    raise ValueError("gateway recovery state is invalid")
            failures = state.get("consecutive_failures")
            successes = state.get("consecutive_successes")
            if (
                type(failures) is not int
                or not 0 <= failures <= GATEWAY_FAILURES_TO_DOWN
                or type(successes) is not int
                or not 0 <= successes <= GATEWAY_SUCCESSES_TO_UP
            ):
                raise ValueError("gateway probe count is invalid")
            for field in ("panic_stop_latched", "household_stop_latched", "panic_recovery_failed", "control_revision_required"):
                if field in state and not isinstance(state[field], bool):
                    raise ValueError("Panic recovery state is invalid")
            self._panic_stop_latched = state.get("panic_stop_latched", False)
            self._household_stop_latched = state.get("household_stop_latched", self._panic_stop_latched)
            self._panic_recovery_failed = state.get("panic_recovery_failed", False)
            self._control_revision_required = state.get("control_revision_required", self._panic_stop_latched)
            if self._panic_stop_latched or self._household_stop_latched:
                self._control_revision_required = True
            if self._panic_recovery_failed and not (self._panic_stop_latched and self._household_stop_latched):
                raise ValueError("Panic recovery state is inconsistent")
            self._persistence_enabled = state["persistence_enabled"]
            self._persistence_mode_initialized = True
            self._gateway_state = state["gateway_state"]
            self._outage_observed = state["outage_observed"]
            self._outage_stop_latched = state["outage_stop_latched"]
            self._reset_pending = state["reset_pending"]
            self._recovery_pending = state["recovery_pending"]
            self._consecutive_failures = failures
            self._consecutive_successes = successes
            active_plan_needs_cleanup = (
                (self._outage_stop_latched or self._panic_stop_latched)
                and self._active_plan_path is not None
                and self._active_plan_path.is_file()
            )
            if active_plan_needs_cleanup:
                self._reset_pending = True
                self._next_reset_retry_at = 0.0
            elif self._reset_pending:
                self._next_reset_retry_at = time.monotonic() + GATEWAY_RESET_RETRY_SECONDS
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            LOGGER.exception("gateway persistence state could not be read")
            self._gateway_state = "unknown"
            self._persistence_mode_initialized = True
            self._outage_stop_latched = True
            self._panic_stop_latched = True
            self._household_stop_latched = True
            self._panic_recovery_failed = True
            self._control_revision_required = True
            self._reset_pending = True
            self._gateway_state_error = True

    def _control_refusal(self, payload):
        expected = payload.get("expected_control_revision")
        if expected is None and "expected_control_revision" not in payload and not self._control_revision_required:
            return None
        if (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{32}", expected) is None
                or not secrets.compare_digest(expected, self._control_revision)):
            return {"ok": False, "error": "Network control changed. Reload before choosing a fresh action.",
                    "reason": "control-changed"}
        return None

    def _block_unconfirmed_intent(self):
        with self._revision_lock:
            self._control_revision = secrets.token_bytes(16).hex()
            self._control_revision_required = True
            self._panic_stop_latched = True
            self._household_stop_latched = True
            self._panic_recovery_failed = True

    def _write_panic_guard(self, *, panic_recovery_failed=None):
        if self._panic_guard is None:
            return True
        try:
            self._panic_guard.write({
                "version": PANIC_GUARD_FORMAT,
                "panic_stop_latched": self._panic_stop_latched,
                "household_stop_latched": self._household_stop_latched,
                "panic_recovery_failed": (self._panic_recovery_failed
                                          if panic_recovery_failed is None
                                          else panic_recovery_failed),
                "control_revision_required": self._control_revision_required,
            })
            self._panic_guard_error = False
            return True
        except OSError:
            self._panic_guard_error = True
            LOGGER.exception("persistent Panic stop state could not be saved")
            return False

    def _gateway_status(self) -> Dict[str, Any]:
        return {
            "mode_enabled": self._persistence_enabled,
            "mode_initialized": self._persistence_mode_initialized,
            "gateway_state": self._gateway_state,
            "outage_observed": self._outage_observed,
            "outage_stop_latched": self._outage_stop_latched,
            "panic_stop_latched": self._panic_stop_latched,
            "household_stop_latched": self._household_stop_latched,
            "control_revision_required": self._control_revision_required,
            "panic_recovery_failed": self._panic_recovery_failed,
            "reset_pending": self._reset_pending,
            "recovery_pending": self._recovery_pending,
            "state_error": self._gateway_state_error or self._panic_guard_error,
            "control_revision": self._control_revision,
        }

    def _write_gateway_state(self, *, durable: bool = False,
                             panic_recovery_failed: Optional[bool] = None) -> bool:
        path = self._gateway_state_path
        if path is None:
            return True
        state = {
            "version": 1,
            "persistence_enabled": self._persistence_enabled,
            "gateway_state": self._gateway_state,
            "outage_observed": self._outage_observed,
            "outage_stop_latched": self._outage_stop_latched,
            "panic_stop_latched": self._panic_stop_latched,
            "household_stop_latched": self._household_stop_latched,
            "control_revision_required": self._control_revision_required,
            "panic_recovery_failed": (self._panic_recovery_failed if panic_recovery_failed is None
                                      else panic_recovery_failed),
            "reset_pending": self._reset_pending,
            "recovery_pending": self._recovery_pending,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_successes": self._consecutive_successes,
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, sort_keys=True)
                output.write("\n")
                if durable:
                    output.flush()
                    os.fsync(output.fileno())
            os.replace(temporary, path)
            if durable:
                parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            self._gateway_state_error = False
        except OSError:
            self._gateway_state_error = True
            LOGGER.exception("gateway persistence state could not be saved")
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass
        return not self._gateway_state_error

    def _handle_gateway_persistence(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return {"ok": False, "error": "gateway persistence must be true or false"}
        with self._network_lock:
            changed = self._persistence_enabled != enabled
            self._persistence_enabled = enabled
            self._persistence_mode_initialized = True
            if (
                changed
                and not enabled
                and (self._gateway_state == "unavailable" or self._outage_observed)
                and not self._outage_stop_latched
            ):
                self._stop_for_gateway_outage()
            self._write_gateway_state()
            return {"ok": True, "action": "gateway-persistence", **self._gateway_status()}

    def monitor_gateway_once(self) -> None:
        """Update bounded gateway state and perform any required transition."""

        with self._network_lock:
            if not self._persistence_mode_initialized:
                return

        try:
            reachable = self._applier.probe_default_gateway()
        except CommandExecutionError:
            LOGGER.warning("configured router gateway probe is unavailable")
            reachable = None
        with self._network_lock:
            if reachable is None and self._gateway_state != "unknown":
                # A router restart drops the Ethernet link and the default
                # route with it. Once the gateway answered, losing it counts.
                reachable = False
            if reachable is None:
                self._gateway_state = "unknown"
                self._consecutive_failures = 0
                self._consecutive_successes = 0
                if not self._persistence_enabled and self._outage_observed:
                    self._stop_for_gateway_outage()
            elif reachable:
                self._consecutive_failures = 0
                self._consecutive_successes = min(
                    GATEWAY_SUCCESSES_TO_UP,
                    self._consecutive_successes + 1,
                )
                if self._consecutive_successes >= GATEWAY_SUCCESSES_TO_UP:
                    was_unavailable = self._gateway_state == "unavailable"
                    self._gateway_state = "available"
                    if was_unavailable or self._outage_observed:
                        self._outage_observed = False
                        if (self._persistence_enabled and not self._outage_stop_latched
                                and not self._panic_stop_latched):
                            self._recovery_pending = self._has_active_plan()
            else:
                self._consecutive_successes = 0
                self._consecutive_failures = min(
                    GATEWAY_FAILURES_TO_DOWN,
                    self._consecutive_failures + 1,
                )
                if self._consecutive_failures >= GATEWAY_FAILURES_TO_DOWN:
                    newly_unavailable = self._gateway_state != "unavailable"
                    self._gateway_state = "unavailable"
                    self._outage_observed = True
                    if newly_unavailable:
                        LOGGER.warning("configured router gateway is unreachable")
                    if not self._persistence_enabled and not self._outage_stop_latched:
                        self._stop_for_gateway_outage()
            if self._reset_pending and time.monotonic() >= self._next_reset_retry_at:
                self._attempt_gateway_reset()
            self._write_gateway_state()

    def _has_active_plan(self) -> bool:
        path = self._active_plan_path
        return path is not None and path.is_file()

    def _stop_for_gateway_outage(self) -> None:
        self._outage_stop_latched = True
        self._recovery_pending = False
        self._reset_pending = True
        self._next_reset_retry_at = 0.0
        self._write_gateway_state()
        self._attempt_gateway_reset()

    def _attempt_gateway_reset(self) -> None:
        if not self._reset_pending:
            return
        try:
            self._applier.reset()
            if self._active_plan_path is not None:
                self._active_plan_path.unlink(missing_ok=True)
        except (CommandExecutionError, OSError):
            LOGGER.exception("network reset after a router outage failed")
            self._next_reset_retry_at = time.monotonic() + GATEWAY_RESET_RETRY_SECONDS
            self._write_gateway_state()
            return
        self._reset_pending = False
        self._next_reset_retry_at = 0.0
        LOGGER.warning("active experiment stopped after a router gateway outage")
        self._write_gateway_state()

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

    def _handle_queue_stats(self) -> Dict[str, Any]:
        """Report what each applied queue did so far without changing anything.

        The socket handler holds the helper's one operation lock around this read. An
        apply or reset cannot run meanwhile so old plan data never meets new kernel
        queues. A read that arrives during one answers helper-busy. This method takes no
        lock of its own because a Wi-Fi action never changes queues and would only be
        refused for no reason.
        """

        plan = self._current_plan()
        if plan is None:
            return {"ok": True, "action": "queue-stats", "queues": []}
        try:
            stats = self._applier.queue_stats()
        except CommandExecutionError as error:
            # The dashboard asks every few seconds so say it once for each new reason.
            reason = str(error)
            if reason != self._queue_read_error:
                self._queue_read_error = reason
                LOGGER.warning("queue statistics unavailable: %s", reason, exc_info=True)
            return {"ok": False, "error": "queue statistics unavailable"}
        self._queue_read_error = None
        interfaces = {
            "upload": self._applier.uplink_interface,
            "download": self._applier.test_interface,
        }
        generation = f"{self._boot_nonce}-{self._plan_count}"
        entries = []
        for queue in plan.queues:
            interface = interfaces[queue.direction.value]
            handle = queue_handle(queue.mark, queue_kind(queue.impairment))
            parent = queue_parent(queue.mark)
            match = next(
                (
                    item
                    for item in stats.get(interface, ())
                    if isinstance(item, dict)
                    and item.get("handle") == handle
                    and item.get("parent") == parent
                ),
                None,
            )
            if match is None:
                continue
            entries.append(
                {
                    "device_id": queue.device_id,
                    "direction": queue.direction.value,
                    "interface": interface,
                    "handle": handle,
                    "kind": match.get("kind"),
                    "impairment": asdict(queue.impairment),
                    "packets": match.get("packets", 0),
                    "bytes": match.get("bytes", 0),
                    "drops": match.get("drops", 0),
                    "overlimits": match.get("overlimits", 0),
                    "requeues": match.get("requeues", 0),
                    "backlog_bytes": match.get("backlog", 0),
                    "qlen": match.get("qlen", 0),
                    "generation": generation,
                }
            )
        return {"ok": True, "action": "queue-stats", "queues": entries}

    def _access_failure_message(self) -> str:
        if getattr(self._access_manager, "dns_failed", False):
            return (
                "Website rules were not applied because the filtered DNS service did not "
                "start. Panic and Normal access still work. Turn household access rules "
                "off on the Access and rules page or check jittersim-dns (read the cause "
                "with sudo jittersim logs dns)."
            )
        return "access rules could not be applied"

    def _handle_apply(
        self,
        payload: Any,
        *,
        update_access: bool = True,
        explicit_apply: bool = False,
    ) -> Dict[str, Any]:
        try:
            plan = plan_from_payload(payload)
        except PlanCodecError:
            return {"ok": False, "error": "invalid network plan"}
        if self._panic_recovery_failed:
            return {"ok": False, "error": "Normal access recovery is not confirmed. Retry Panic before Start."}
        if self._panic_stop_latched and not explicit_apply:
            return {"ok": False, "error": "Panic stopped profiles. Choose Start explicitly when it is safe."}
        if self._outage_stop_latched:
            if not explicit_apply:
                return {
                    "ok": False,
                    "error": (
                        "profiles stopped after a gateway outage; use Apply after "
                        "the gateway recovers"
                    ),
                }
            if self._gateway_state != "available":
                return {
                    "ok": False,
                    "error": "the router gateway must be reachable before applying profiles",
                }
        elif not self._persistence_enabled and self._gateway_state == "unavailable":
            return {
                "ok": False,
                "error": "the router gateway is unavailable; profiles cannot start in this mode",
            }
        if plan.access is not None and update_access and not self._household_stop_latched:
            if self._access_manager is None:
                return {"ok": False, "error": "persistent access controls are unavailable"}
            try:
                self._access_manager.apply(plan.access)
            except (OSError, ValueError, CommandExecutionError):
                LOGGER.exception("persistent access apply failed")
                return {"ok": False, "error": self._access_failure_message()}
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
        if not report.counters_preserved:
            self._plan_count += 1
        self._list_domains = {rule.list_id: rule.domains for rule in plan.service_list_rules}
        if explicit_apply:
            self._panic_stop_latched = False
            self._outage_stop_latched = False
            self._reset_pending = False
            self._outage_observed = False
            self._gateway_state_error = False
        self._recovery_pending = False
        self._write_gateway_state(durable=explicit_apply)
        guard_saved = self._write_panic_guard() if explicit_apply else True
        if (self._gateway_state_error or not guard_saved) and explicit_apply:
            self._block_unconfirmed_intent()
            self._outage_stop_latched = True
            self._reset_pending = True
            self._next_reset_retry_at = 0.0
            self._write_panic_guard()
            self._attempt_gateway_reset()
            if self._access_manager is not None:
                try:
                    self._access_manager.disable()
                except (OSError, ValueError, CommandExecutionError):
                    LOGGER.exception("household cleanup failed after Panic state save failure")
            return {"ok": False, "error": "gateway recovery state could not be saved safely"}
        if self._ad_blocking_store is not None and explicit_apply:
            try:
                self._ad_blocking_store.resume()
            except (OSError, ValueError, CommandExecutionError):
                LOGGER.warning("Profiles started but ad-blocking resume could not be confirmed.")
        return {"ok": True, "action": "apply", "commands_run": report.commands_run,
                "counters_preserved": report.counters_preserved}

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

    def _handle_router_wifi(self, payload: Dict[str, Any], *, read_only: bool = False) -> Dict[str, Any]:
        if not read_only:
            return {"ok": False, "reason": "router-control-disabled",
                    "error": "Router Wi-Fi switching is disabled. Use the router's own controls."}
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

    def _handle_router_wifi_credentials(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Read the router's own Wi-Fi name and optionally its password for one copy.

        The password leaves this method only inside the response and only when the request
        asks for it. It is never logged and never stored.
        """

        router = None
        try:
            password = payload["admin_password"]
            include_password = payload.get("include_password", False)
            if not isinstance(password, str) or not password or type(include_password) is not bool:
                raise ValueError("invalid router credentials")
            gateway = self._applier.uplink_info().get("gateway")
            if not gateway:
                raise RouterError("router gateway is unavailable", reason="unreachable")
            router = self._router_factory(gateway, password)
            # The dashboard waits on this answer so keep it as short as router-status.
            router.limit_operation(15.0)
            router.login(preserve_deadline=True)
            settings = router.read_wifi_settings()
            response = {"ok": True, "action": "router-wifi-credentials", "ssid": settings.ssid}
            if include_password:
                response["password"] = settings.password
            return response
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "invalid router credentials"}
        except RouterError as error:
            LOGGER.warning("router Wi-Fi credential read failed (%s)", error.reason)
            return {"ok": False, "error": error.check_message, "reason": error.reason}
        except CommandExecutionError:
            LOGGER.warning("router Wi-Fi credential read failed while inspecting the wired connection")
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

    def _handle_reset(self, *, pause_ad_blocking: bool = True) -> Dict[str, Any]:
        blocking_failed = False
        if pause_ad_blocking and self._ad_blocking_store is not None:
            try:
                self._ad_blocking_store.pause()
            except (OSError, ValueError, CommandExecutionError):
                blocking_failed = True
        self._active_plan = None
        try:
            commands_run = self._applier.reset()
            if self._active_plan_path is not None:
                self._active_plan_path.unlink(missing_ok=True)
        except (CommandExecutionError, OSError):
            LOGGER.exception("network reset failed")
            return {"ok": False, "error": "network reset failed"}
        self._recovery_pending = False
        self._outage_observed = False
        self._write_gateway_state()
        if blocking_failed:
            return {"ok": False, "error": "Profiles are off but ad-blocking bypass could not be confirmed. Retry Panic."}
        return {"ok": True, "action": "reset", "commands_run": commands_run}

    def _forget_changed_lists(self, plan: NetworkPlan) -> None:
        """Drop learned addresses of lists that were removed or got other hostnames."""

        wanted = {rule.list_id: rule.domains for rule in plan.service_list_rules}
        for list_id, domains in self._list_domains.items():
            if wanted.get(list_id) != domains:
                self._service_addresses.forget(list_id)

    def _published_plan(self) -> Optional[NetworkPlan]:
        """Read the published copy of the applied plan without adopting it."""

        if self._active_plan_path is None:
            return None
        try:
            payload = json.loads(self._active_plan_path.read_text(encoding="utf-8"))
            return plan_from_payload(payload)
        except (OSError, ValueError):
            return None

    def _current_plan(self) -> Optional[NetworkPlan]:
        """Return the applied plan, reading the published copy after a helper restart."""

        if self._active_plan is not None:
            return self._active_plan
        plan = self._published_plan()
        if plan is None:
            return None
        self._active_plan = plan
        self._plan_count += 1
        self._list_domains = {rule.list_id: rule.domains for rule in plan.service_list_rules}
        return plan

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
    rbufsize = 0

    def handle(self) -> None:
        self.request.settimeout(3)
        self.rfile = io.BufferedReader(DeadlineReader(
            self.rfile, self.request, time.monotonic() + 3,
        ))
        try:
            self._handle_request()
        except (OSError, TimeoutError):
            return

    def _handle_request(self) -> None:
        line = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
        if hasattr(self, "request"):
            # Response transport gets its own bounded allowance after input.
            self.request.settimeout(3)
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
        if self.server.policy_only:
            if action not in ("homepage-snapshot", "homepage-confirm"):
                self._send_response({"ok": False, "error": "action unavailable"})
            else:
                self._send_response(self.server.service.handle_payload(payload))
            return
        if action in ("homepage-snapshot", "homepage-confirm"):
            self._send_response({"ok": False, "error": "use the policy socket"})
            return
        # Capability flags are fixed for this process and read no live network state.
        if action in ("ping", "capabilities", "control-revision", "dashboard-access", "update-retry") or action in WIFI_ACTIONS or action in ROUTER_ACTIONS:
            self._send_response(self.server.service.handle_payload(payload))
            return
        # Apply and reset wait a moment for a read so a poll never refuses Panic.
        # Reads of live network state answer helper-busy at once.
        # Signed staging only changes release files. It must not hold network
        # ownership while verifying an archive or copying it from the dashboard.
        lock = (self.server.release_stage_lock if action in (
                    "update-stage-upload", "update-stage-identified-upload")
                else self.server.operation_lock)
        if action in WRITE_ACTIONS:
            acquired = lock.acquire(timeout=WRITE_LOCK_WAIT_SECONDS)
        else:
            acquired = lock.acquire(blocking=False)
        if not acquired:
            self._send_response({"ok": False, "error": "The networking helper is busy. Try again later.",
                                 "reason": "helper-busy"})
            return
        try:
            # Finish the response snapshot before another operation can change state.
            encoded = self._encode_response(self.server.service.handle_payload(payload))
        finally:
            lock.release()
        # A slow client must not hold the lock after its operation has finished.
        self.wfile.write(encoded)

    @staticmethod
    def _encode_response(payload: Dict[str, Any]) -> bytes:
        return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")

    def _send_response(self, payload: Dict[str, Any]) -> None:
        self.wfile.write(self._encode_response(payload))


class UnixHelperServer(SafeServerErrors, socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Serve the helper protocol through a filesystem-protected Unix socket."""

    def __init__(self, socket_path: Path, service: HelperService, *, policy_only: bool = False) -> None:
        self.socket_path = socket_path
        self.policy_only = policy_only
        self.service = service
        self.operation_lock = threading.Lock()
        self.release_stage_lock = threading.Lock()
        self._request_slots = threading.BoundedSemaphore(2 if policy_only else MAX_HELPER_CONNECTIONS)
        self._request_workers = RequestWorkers("jittersim-helper-request", daemon=policy_only)
        self._prepare_socket_path()
        try:
            super().__init__(str(socket_path), HelperRequestHandler)
            os.chmod(socket_path, 0o660)
        except OSError as error:
            self._remove_socket_path()
            raise HelperError("could not create the helper socket") from error

    def process_request(self, request, client_address) -> None:
        self._request_workers.reap()
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
            self._request_workers.start(
                lambda: self.process_request_thread(request, client_address),
                abandon=lambda: self.shutdown_request(request), release=self._request_slots.release,
            )
        except Exception:
            LOGGER.exception("helper worker startup failed")

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

    def service_actions(self) -> None:
        if self._request_workers.startup_uncertain:
            raise WorkersStillRunning("Helper worker startup is unresolved. Restart the service.")

    def server_close(self) -> None:
        """Close the socket and remove it only when it is still a socket."""

        self._request_workers.close()
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


# Helper actions that only read, so they leave a recent client scan valid.
READ_ONLY_ACTIONS = frozenset((
    "ping", "control-revision", "dashboard-access", "gateway-status", "capabilities", "access-status",
    "update-status", "clients", "uplink-info", "observe-dns", "queue-stats", "traffic-counters",
    "router-preview-ap", "router-status", "router-check", "router-wifi-credentials",
))


class _SnapshotFlight:
    """One client scan in progress that other callers wait for."""

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.done = threading.Event()
        self.value: Optional[Dict[str, Any]] = None
        self.error: Optional[BaseException] = None


class _SnapshotCache:
    """A short-lived shared client scan, shared by every copy of one client."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.generation = 0
        self.expires_at = 0.0
        self.value: Optional[Dict[str, Any]] = None
        self.flight: Optional[_SnapshotFlight] = None
        # The last good scan and when it was read, for a short stand-in while the helper is busy.
        self.recent: Optional[Tuple[float, Dict[str, Any]]] = None


class HelperClient:
    """Send bounded requests to a local privileged helper."""

    # The scheduler and many dashboard reads ask for the clients. Within this window
    # they share one scan, because each scan runs iw and ip on the Pi.
    snapshot_ttl_seconds = 1.5

    def __init__(self, *, socket_path: Path, timeout_seconds: float = 8.0) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 90:
            raise ValueError("timeout_seconds must be greater than 0 and no more than 90")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds
        self._snapshots = _SnapshotCache()

    def _with_timeout(self, timeout_seconds: float) -> "HelperClient":
        """Return a client with a longer timeout that shares this client's scan cache."""

        client = HelperClient(socket_path=self.socket_path, timeout_seconds=timeout_seconds)
        client._snapshots = self._snapshots
        client.snapshot_ttl_seconds = self.snapshot_ttl_seconds
        return client

    def invalidate_connected_devices(self) -> None:
        """Invalidate both current and recent identity before a network change starts."""

        with self._snapshots.lock:
            self._snapshots.generation += 1
            self._snapshots.value = None
            self._snapshots.expires_at = 0.0
            self._snapshots.recent = None

    def recent_connected_devices(self, max_age_seconds: float) -> Optional[Dict[str, Any]]:
        """Return the last good scan if it is young enough and no change has happened since."""

        with self._snapshots.lock:
            recent = self._snapshots.recent
            if recent is None or not 0 <= time.monotonic() - recent[0] <= max_age_seconds:
                return None
            return copy.deepcopy(recent[1])

    def ping(self) -> Dict[str, Any]:
        """Check whether the helper is accepting requests."""

        return self._request({"action": "ping"})

    def ad_blocking_status(self) -> Dict[str, Any]:
        return self._request({"action": "ad-blocking"})["ad_blocking"]

    def configure_ad_blocking(self, mac_addresses, allowed_domains, *, update=False, resume=False,
                             control_revision=None):
        revision = self.control_revision() if control_revision is None else control_revision
        payload = {"action": "ad-blocking-configure", "mac_addresses": list(mac_addresses),
                   "allowed_domains": list(allowed_domains), "update": update, "resume": resume,
                   "expected_control_revision": revision}
        return self._request(payload)["ad_blocking"]

    def dashboard_access(self, operation: str = "status", *, peer: Optional[str] = None,
                         mac_address: Optional[str] = None, name: str = "",
                         move_id: Optional[str] = None,
                         expected_mac: Optional[str] = None,
                         move_access: Optional[bool] = None) -> Dict[str, Any]:
        """Use the saved access list without supplying command arguments."""
        return self._request({"action": "dashboard-access", "operation": operation,
                              "peer": peer, "mac_address": mac_address, "name": name,
                              "move_id": move_id, "expected_mac": expected_mac,
                              "move_access": move_access})

    def _check_plan_support(self, plan: NetworkPlan) -> None:
        if plan.general_profile is not None:
            self.check_general_profile_support()
        if plan.service_list_rules:
            self.check_service_list_support(
                outages=any(rule.outage_active for rule in plan.service_list_rules)
            )

    def apply(self, *, plan: NetworkPlan, explicit_apply: bool = False, control_revision: Optional[str] = None) -> Dict[str, Any]:
        """Ask the helper to apply one validated network plan."""

        revision = self.control_revision() if control_revision is None else control_revision
        self._check_plan_support(plan)
        # Applying waits for the kernel and the access policy, so allow longer.
        return self._request(
            {
                "action": "apply",
                "plan": self._homepage_wire_plan(plan),
                "explicit_apply": explicit_apply,
                "expected_control_revision": revision,
            },
            timeout_seconds=60,
        )

    def control_revision(self) -> str:
        """Capture one immutable helper intent before any plan or saved change."""

        try:
            result = self._request({"action": "control-revision"})
        except HelperClientError as error:
            if str(error) == "unknown action":
                raise HelperClientError(HELPER_OUT_OF_DATE_MESSAGE) from error
            raise
        revision = result.get("control_revision")
        if revision is None:
            raise HelperClientError(HELPER_OUT_OF_DATE_MESSAGE)
        if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{32}", revision) is None:
            raise HelperClientError("helper returned an invalid control revision")
        required = result.get("recovery_required")
        if not isinstance(required, bool):
            raise HelperClientError(HELPER_OUT_OF_DATE_MESSAGE)
        if required:
            raise HelperClientError("Normal access recovery is not confirmed. Retry Panic before Start.")
        return revision

    def _homepage_wire_plan(self, plan: NetworkPlan) -> Dict[str, Any]:
        payload = plan_to_payload(plan)
        rules = plan.mark_rules + plan.service_list_rules + plan.service_assignment_rules + plan.service_quota_rules
        rules += tuple(rule for rule in (plan.general_profile, plan.everyone_else) if rule)
        supported = False
        if any(rule.homepage_bounce_percent for rule in rules):
            try:
                capabilities = self._request({"action": "capabilities"})
                supported = capabilities.get("homepage_policy_version") == 1
            except HelperClientError as error:
                if error.reason == "helper-busy":
                    raise
        if not supported:
            payload.pop("homepage_policy_version", None)
            for name in ("mark_rules", "service_list_rules", "service_assignment_rules", "service_quota_rules"):
                for rule in payload[name]:
                    rule.pop("homepage_bounce_percent", None)
            for name in ("general_profile", "everyone_else"):
                if payload[name] is not None:
                    payload[name].pop("homepage_bounce_percent", None)
        return payload

    def homepage_snapshot(self, *, peer: str, host=None, addresses=(), expected=None) -> dict:
        """Use separate admission and a total socket deadline for policy reads."""
        from .homepage import policy_request
        return policy_request(Path(str(self.socket_path) + ".homepage"), {
            "action": "homepage-snapshot", "peer": peer, "host": host,
            "addresses": list(addresses), "expected": expected,
        })

    def homepage_confirm(self, receipt: dict) -> bool:
        from .homepage import policy_request
        return policy_request(Path(str(self.socket_path) + ".homepage"), {
            "action": "homepage-confirm", "receipt": receipt,
        }).get("verified") is True

    def gateway_status(self) -> Dict[str, Any]:
        """Read the helper's bounded gateway and persistence state."""

        result = self._request({"action": "gateway-status"})
        if result.get("gateway_state") not in ("unknown", "available", "unavailable"):
            raise HelperClientError("helper returned invalid gateway health")
        for field in (
            "mode_enabled",
            "outage_observed",
            "outage_stop_latched",
            "reset_pending",
            "recovery_pending",
            "state_error",
        ):
            if not isinstance(result.get(field), bool):
                raise HelperClientError("helper returned invalid gateway persistence state")
        for field in ("panic_stop_latched", "household_stop_latched", "panic_recovery_failed"):
            if field in result and not isinstance(result[field], bool):
                raise HelperClientError("helper returned invalid Panic recovery state")
        if "control_revision" in result:
            revision = result["control_revision"]
            if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{32}", revision) is None:
                raise HelperClientError("helper returned an invalid control revision")
        if "control_revision_required" in result and not isinstance(result["control_revision_required"], bool):
            raise HelperClientError("helper returned invalid control intent state")
        return result

    def set_gateway_persistence(self, *, enabled: bool) -> Dict[str, Any]:
        """Set whether active experiment plans resume after gateway recovery."""

        if not isinstance(enabled, bool):
            raise HelperClientError("gateway persistence must be true or false")
        result = self._request({"action": "gateway-persistence", "enabled": enabled})
        if result.get("mode_enabled") is not enabled:
            raise HelperClientError("helper did not save gateway persistence state")
        if result.get("state_error") is True:
            raise HelperClientError("helper could not save gateway recovery state safely")
        return result

    def check_service_list_support(self, *, outages: bool = False) -> None:
        """Reject an older running helper before applying service lists.

        With `outages` the helper must also enforce a list's random outage. An
        older helper reads the plan without that field and would leave the
        listed services reachable.
        """

        try:
            capabilities = self._request({"action": "capabilities"})
        except HelperClientError as error:
            if error.reason == "helper-busy":
                raise
            raise HelperClientError("Update the Pi helper before applying service lists") from error
        if capabilities.get("service_lists") is not True:
            raise HelperClientError("Update the Pi helper before applying service lists")
        if outages and capabilities.get("service_list_outages") is not True:
            raise HelperClientError(
                "Update the Pi helper before a service list can have a random outage"
            )

    def check_protected_dns_capture_support(self) -> None:
        """Reject an older running helper before it can capture protected traffic."""

        try:
            capabilities = self._request({"action": "capabilities"})
        except HelperClientError as error:
            # Only a helper that does not know the action is out of date. A helper that is
            # busy or cannot be reached keeps its own message and still blocks the capture.
            if str(error) == "unknown action":
                raise HelperClientError(HELPER_OUT_OF_DATE_MESSAGE) from error
            raise
        if capabilities.get("protected_dns_capture") is not True:
            raise HelperClientError(HELPER_OUT_OF_DATE_MESSAGE)

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
            # A busy helper is not an old one, so it keeps its own message.
            if error.reason == "helper-busy":
                raise
            raise HelperClientError("Update the Pi helper before applying a profile for everyone") from error
        if capabilities.get("general_profile") is not True:
            raise HelperClientError("Update the Pi helper before applying a profile for everyone")

    def reset(self) -> Dict[str, Any]:
        """Ask the helper to remove project-owned network state."""

        return self._request({"action": "reset"})

    def recover_reset(self) -> Dict[str, Any]:
        """Repeat profile cleanup without changing existing ad permission."""

        try:
            return self._request({"action": "recover-reset"})
        except HelperClientError as error:
            if str(error) == "unknown action":
                return self.reset()
            raise

    def apply_experiment(self, *, plan: NetworkPlan, control_revision: Optional[str] = None) -> Dict[str, Any]:
        """Change scheduled effects without activating pending access-rule edits."""

        revision = self.control_revision() if control_revision is None else control_revision
        self._check_plan_support(plan)
        return self._request(
            {"action": "apply-experiment", "plan": self._homepage_wire_plan(plan), "expected_control_revision": revision}, timeout_seconds=60
        )

    def normal_access(self) -> Dict[str, Any]:
        client = self._with_timeout(60)
        return client._request({"action": "normal-access"})

    def access_status(self) -> Dict[str, Any]:
        return self._request({"action": "access-status"})

    def apply_access(self, policy: AccessPlan, control_revision: Optional[str] = None) -> Dict[str, Any]:
        # DNS restart and recovery can take longer than a status read.
        revision = self.control_revision() if control_revision is None else control_revision
        client = self._with_timeout(60)
        return client._request({"action": "access-apply", "policy": policy.payload(), "expected_control_revision": revision})

    def configure_access_point(self, *, ssid: str, password: str) -> Dict[str, Any]:
        """Set the local test AP credentials through the privileged helper."""

        client = self._with_timeout(90)
        return client._request(
            {"action": "configure-ap", "ssid": ssid, "password": password}
        )

    def reset_wifi_name(self) -> Dict[str, Any]:
        """Reset only the Pi Wi-Fi name without dashboard or router credentials."""

        # Live security checks and one restoration can take up to 75 seconds.
        client = self._with_timeout(90)
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

    def release_status(self) -> Dict[str, Any]:
        return self._request({"action": "update-status"})

    def stage_uploaded_release(self, *, upload_id: Optional[str] = None) -> Dict[str, Any]:
        """Ask the root helper to check and stage the release the dashboard just received."""
        if upload_id is None:
            return self._request({"action": "update-stage-upload"}, timeout_seconds=120.0)
        message = "The Pi network helper needs an update before uploading a release."
        try:
            capabilities = self._request({"action": "capabilities"})
            if capabilities.get("identified_release_uploads") is not True:
                raise HelperClientError(message)
            # A distinct action also refuses an older helper replaced after the read.
            return self._request({"action": "update-stage-identified-upload", "upload_id": upload_id},
                                 timeout_seconds=120.0)
        except HelperClientError as error:
            if str(error) == "unknown action":
                raise HelperClientError(message) from None
            raise

    def online_check(self, *, force: bool = False) -> Dict[str, Any]:
        """Start an online release check in the root fetch unit and return its job."""
        return self._request({"action": "update-online-check", "force": bool(force)})

    def online_status(self, job: Optional[str] = None) -> Dict[str, Any]:
        return self._request({"action": "update-online-status", "job": job})

    def online_settings(self, changes: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
        return self._request({"action": "update-online-settings", "changes": changes})

    def install_release(self, *, version: str, rollback: bool = False) -> Dict[str, Any]:
        return self._request({"action": "update-rollback" if rollback else "update-install", "version": version})

    def retry_update(self, *, retry_id: str) -> Dict[str, Any]:
        """Send one explicit retry and leave a lost response to a status read."""

        if not isinstance(retry_id, str) or re.fullmatch(r"[0-9a-f]{64}", retry_id) is None:
            raise HelperClientError("Check the failed update again before retrying.",
                                    reason="update-retry-refused")
        result = self._request({"action": "update-retry", "retry_id": retry_id}, timeout_seconds=20)
        public_id = result.get("request_id")
        if (not isinstance(public_id, str) or re.fullmatch(r"[0-9a-f]{32}", public_id) is None
                or result.get("request_kind") not in ("install", "rollback")):
            raise HelperClientError("The update retry could not be confirmed. Check update status.")
        return result

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

    def read_router_wifi_credentials(
        self, *, admin_password: str, include_password: bool
    ) -> Dict[str, Any]:
        """Read the router's own Wi-Fi name and optionally its password for one copy."""

        response = self._request(
            {"action": "router-wifi-credentials", "admin_password": admin_password,
             "include_password": include_password},
            timeout_seconds=25.0,
        )
        ssid = response.get("ssid")
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
            raise HelperClientError(WIFI_DETAILS_MISSING_MESSAGE)
        result = {"ssid": ssid}
        if include_password:
            password = response.get("password")
            if (
                not isinstance(password, str)
                or not 8 <= len(password) <= 63
                or any(ord(char) < 32 for char in password)
            ):
                raise HelperClientError(WIFI_DETAILS_MISSING_MESSAGE)
            result["password"] = password
        return result

    def connected_device_snapshot(self) -> Dict[str, Any]:
        """Deliver a generation-valid scan within this caller's total deadline.

        One active scan stays owned across invalidation. A newer caller may wait
        for it then start one fresh scan. An older caller cannot receive identity
        after the generation it requested has changed.
        """

        cache = self._snapshots
        deadline = time.monotonic() + self.timeout_seconds
        with cache.lock:
            requested_generation = cache.generation
        for _ in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HelperClientError("client scan timed out")
            with cache.lock:
                if requested_generation != cache.generation:
                    raise HelperClientError("network changed during client scan", reason="helper-busy")
                if cache.value is not None and time.monotonic() < cache.expires_at:
                    return copy.deepcopy(cache.value)
                flight = cache.flight
                leader = flight is None
                if leader:
                    flight = cache.flight = _SnapshotFlight(cache.generation)
            if leader:
                try:
                    value = self._read_connected_device_snapshot(timeout_seconds=remaining)
                    if time.monotonic() >= deadline:
                        raise HelperClientError("client scan timed out")
                except BaseException as error:
                    flight.error = error
                else:
                    flight.value = value
                    with cache.lock:
                        if flight.generation == cache.generation:
                            cache.value = value
                            cache.expires_at = time.monotonic() + self.snapshot_ttl_seconds
                            cache.recent = (time.monotonic(), copy.deepcopy(value))
                finally:
                    with cache.lock:
                        if cache.flight is flight:
                            cache.flight = None
                        flight.done.set()
            elif not flight.done.wait(timeout=max(0.0, deadline - time.monotonic())):
                raise HelperClientError("client scan timed out")
            with cache.lock:
                if requested_generation != cache.generation:
                    raise HelperClientError("network changed during client scan", reason="helper-busy")
                if flight.generation != cache.generation:
                    continue
                if flight.error is not None:
                    raise flight.error
                if time.monotonic() >= deadline:
                    raise HelperClientError("client scan timed out")
                return copy.deepcopy(flight.value)
        raise HelperClientError("client scan could not refresh", reason="helper-busy")

    def _read_connected_device_snapshot(self, timeout_seconds=None) -> Dict[str, Any]:
        response = self._request({"action": "clients"}, timeout_seconds=timeout_seconds)
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
        if macs:
            self.check_protected_dns_capture_support()
        response = self._request({"action": "observe-dns", "excluded_macs": macs})
        if macs and response.get("excluded_macs") != macs:
            raise HelperClientError(HELPER_OUT_OF_DATE_MESSAGE)
        observations = response.get("observations")
        if not isinstance(observations, list) or any(
            not isinstance(observation, dict) for observation in observations
        ):
            raise HelperClientError("helper returned invalid DNS observations")
        return observations

    def queue_stats(self) -> List[Dict[str, Any]]:
        """Return what each applied queue did so far with every field checked."""

        response = self._request({"action": "queue-stats"})
        queues = response.get("queues")
        if not isinstance(queues, list) or len(queues) > MAX_QUEUE_STATS_ENTRIES:
            raise HelperClientError(QUEUE_STATS_INVALID_MESSAGE)
        result = []
        for entry in queues:
            if not isinstance(entry, dict):
                raise HelperClientError(QUEUE_STATS_INVALID_MESSAGE)
            device_id = entry.get("device_id")
            handle = entry.get("handle")
            interface = entry.get("interface")
            generation = entry.get("generation")
            if (
                not isinstance(generation, str)
                or not _QUEUE_GENERATION_PATTERN.fullmatch(generation)
                or not isinstance(device_id, str)
                or not _QUEUE_DEVICE_ID_PATTERN.fullmatch(device_id)
                or not isinstance(handle, str)
                or not _QUEUE_HANDLE_PATTERN.fullmatch(handle)
                or not isinstance(interface, str)
                or not 1 <= len(interface) <= 32
                or entry.get("direction") not in ("upload", "download")
                or entry.get("kind") not in ("netem", "fq_codel")
            ):
                raise HelperClientError(QUEUE_STATS_INVALID_MESSAGE)
            clean: Dict[str, Any] = {
                "device_id": device_id,
                "direction": entry["direction"],
                "interface": interface,
                "handle": handle,
                "kind": entry["kind"],
                "generation": generation,
                "impairment": _clean_queue_impairment(entry.get("impairment")),
            }
            for key in QUEUE_STATS_COUNTERS:
                value = entry.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
                    raise HelperClientError(QUEUE_STATS_INVALID_MESSAGE)
                clean[key] = value
            result.append(clean)
        return result

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

    def _request(
        self, payload: Dict[str, Any], *, timeout_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        if payload.get("action") in READ_ONLY_ACTIONS:
            return self._send(payload, timeout_seconds=timeout_seconds)
        # Anything else may change who is connected, so no earlier scan stays valid.
        self.invalidate_connected_devices()
        try:
            return self._send(payload, timeout_seconds=timeout_seconds)
        finally:
            self.invalidate_connected_devices()

    def _send(
        self, payload: Dict[str, Any], *, timeout_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise HelperClientError("helper request is too large")
        seconds = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + seconds
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(seconds)
                connection.connect(str(self.socket_path))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("helper connection deadline expired")
                connection.settimeout(remaining)
                connection.sendall(encoded)
                response = read_line(connection, MAX_MESSAGE_BYTES + 1, deadline)
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
            if reason not in ("control-changed", "update-retry-refused", "wifi-busy", "helper-busy", "wifi-failed",
                              "wifi-restored", "wifi-restore-failed", "release-refused"):
                reason = None
            raise HelperClientError(str(decoded.get("error", "helper request failed")), reason=reason)
        return decoded


def _clean_queue_impairment(raw: Any) -> Dict[str, Any]:
    """Keep only the checked impairment numbers the dashboard shows."""

    if not isinstance(raw, dict):
        raise HelperClientError(QUEUE_STATS_INVALID_MESSAGE)
    clean: Dict[str, Any] = {}
    for key, maximum in _QUEUE_IMPAIRMENT_LIMITS.items():
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise HelperClientError(QUEUE_STATS_INVALID_MESSAGE)
        clean[key] = value
    bandwidth = raw.get("bandwidth_kbit")
    if bandwidth is not None and (
        isinstance(bandwidth, bool) or not isinstance(bandwidth, int) or not 1 <= bandwidth <= 1_000_000
    ):
        raise HelperClientError(QUEUE_STATS_INVALID_MESSAGE)
    clean["bandwidth_kbit"] = bandwidth
    return clean


def restore_access_at_start(access: AccessManager, *, panic_stopped=False) -> bool:
    """Restore the saved access rules and keep the helper alive when that fails.

    A failed restore leaves the test network closed behind the guard table. The
    helper must still run so that Normal access and Panic can open it again.
    """

    try:
        if panic_stopped:
            access.disable()
        else:
            access.restore()
    except Exception:
        LOGGER.exception(
            "Saved access rules were not restored. The test network stays closed. "
            "Use Normal access to open it."
        )
        return False
    return True


def run_helper(
    *,
    socket_path: Path,
    topology: Topology,
    timeout_seconds: float = 10.0,
    enable_hardware: bool = False,
    router_factory: Optional[Callable[[str, str], TG3442DERouter]] = None,
    homepage_database: Path = Path("/var/lib/jittersim/jittersim.db"),
) -> None:
    """Run the helper only after an explicit hardware-mode opt-in."""

    if not enable_hardware:
        raise HelperError("hardware mode requires --enable-hardware")
    prepare_lock(socket_path.parent / "release-upload.lock")
    active_plan_path = socket_path.parent / "active-plan.json"
    applier = NetworkApplier(executor=SubprocessExecutor(timeout_seconds=timeout_seconds),
                             topology=topology)
    try:
        applier.recover_wifi()
    except (WifiError, CommandExecutionError):
        LOGGER.warning("Pending Wi-Fi recovery failed. Repair jittersim-ap through management access.")
    gateway_state_path = socket_path.parent / "gateway-state.json"
    ad_blocking = AdStore(AdRouting(topology.test_interface),
                          group_id=grp.getgrnam("jittersim").gr_gid)
    access = AccessManager(executor=SubprocessExecutor(timeout_seconds=20), topology=topology,
                           runtime_path=socket_path.parent / "access-policy.json",
                           ad_blocking_store=ad_blocking)
    service = HelperService(
        applier,
        router_factory=router_factory,
        active_plan_path=active_plan_path,
        access_store=AccessStore(ACCESS_FILE, group_id=grp.getgrnam("jittersim").gr_gid),
        gateway_state_path=gateway_state_path,
        access_manager=access,
        ad_blocking_store=ad_blocking,
        panic_guard=PanicGuard(),
        homepage_database=homepage_database,
    )
    restored = restore_access_at_start(access, panic_stopped=service._household_stop_latched)
    if not restored:
        service._block_unconfirmed_intent()
        service._write_panic_guard()
        service._write_gateway_state(durable=True)
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

    def start_daily_release_check():
        # Off unless the owner turned it on. The root fetch unit does the check.
        while not stopped.wait(ONLINE_CHECK_INTERVAL_SECONDS):
            try:
                scheduled_online_check()
            except Exception:
                LOGGER.exception("could not start the daily release check")

    release_worker = threading.Thread(
        target=start_daily_release_check, daemon=True, name="daily-release-check"
    )
    release_worker.start()

    def monitor_gateway():
        while not stopped.is_set():
            started_at = time.monotonic()
            service.monitor_gateway_once()
            remaining = max(
                0.0,
                GATEWAY_CHECK_INTERVAL_SECONDS - (time.monotonic() - started_at),
            )
            if stopped.wait(remaining):
                break

    gateway_worker = threading.Thread(
        target=monitor_gateway,
        daemon=True,
        name="gateway-persistence",
    )
    gateway_worker.start()
    policy_server = None
    policy_worker = None
    try:
        policy_server = UnixHelperServer(Path(str(socket_path) + ".homepage"), service, policy_only=True)
        policy_worker = threading.Thread(target=policy_server.serve_forever, daemon=True, name="homepage-helper")
        policy_worker.start()
    except (HelperError, RuntimeError):
        if policy_server is not None:
            if policy_worker is not None and policy_worker.is_alive():
                policy_server.shutdown()
            policy_server.server_close()
        policy_server = None
        policy_worker = None
        LOGGER.warning("Homepage helper socket unavailable. Control socket remains available.")
    with UnixHelperServer(socket_path, service) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stopped.set()
            if policy_server is not None:
                policy_server.shutdown()
                policy_server.server_close()
                policy_worker.join(timeout=1)
            worker.join(timeout=5)
            gateway_worker.join(timeout=5)
            release_worker.join(timeout=5)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the jittersim privileged helper.")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--uplink", required=True)
    parser.add_argument("--test-interface", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--enable-hardware", action="store_true")
    parser.add_argument("--homepage-database", type=Path, default=Path("/var/lib/jittersim/jittersim.db"))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse helper options and start the explicit hardware process."""

    arguments = _build_parser().parse_args(argv)
    configure_logging(level=logging.INFO)
    run_helper(
        socket_path=arguments.socket,
        homepage_database=arguments.homepage_database,
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
        queue_parent(mark): device_id
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
