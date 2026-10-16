"""Small HTTP server for the jittersim control plane."""

import argparse
import base64
import gzip
import hashlib
import html
import hmac
import http.client
import ipaddress
import io
import json
import logging
import os
import re
import secrets
import select
import signal
import socket
import sqlite3
import socketserver
import ssl
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import MISSING, asdict, dataclass, field, replace
from dataclasses import fields as dataclass_fields
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from ipaddress import IPv4Network
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Sequence, Set, Tuple

from jittersim.diagnostic_privacy import SafeServerErrors, configure_logging
from jittersim.diagnostic_records import recorded
from urllib.parse import parse_qs, urlsplit

from jittersim import release_uploads, updates
from jittersim.request_workers import RequestWorkers, WorkersStillRunning
from jittersim.socket_deadlines import DeadlineReader as _DeadlineReader
from jittersim.api.dashboard import assemble_dashboard
from jittersim.api.access import (
    ACCESS_WRITES,
    PANIC_REPOSITORY_WAIT_SECONDS,
    read_access_api,
    write_access_api,
)
from jittersim.api.trust import TRUST_DOWNLOADS, dashboard_name_report, easy_address, read_trust_api
from jittersim.localname import read_local_name
from jittersim.api.workflows import WORKFLOW_WRITES, machine_health, read_workflow, write_workflow
from jittersim.config.adblocking import MAX_STATE_BYTES as MAX_AD_BLOCKING_BODY_BYTES
from jittersim.config.device_labels import (
    DeviceLabelError,
    guess_device_type,
    normalize_device_display_name,
    normalize_device_mac,
    normalize_device_type,
)
from jittersim.config.device_names import (
    UNKNOWN_DEVICE_NAME,
    has_hidden_characters,
    resolve_device_name,
    saved_device_title,
)
from jittersim.config.domain_labels import group_domain, label_domain, seen_with
from jittersim.config.exemptions import AddressMove, UnaffectedDevice, normalize_exempt_mac
from jittersim.config.payloads import impairment_payload, profile_payload
from jittersim.engine.randomizer import impairment_range_preview
from jittersim.config.models import (
    MAX_LISTED_DOMAINS,
    AccessOptions,
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    GroupDataLimit,
    Impairment,
    MacPolicy,
    Profile,
    Progression,
    Randomness,
    RuntimeState,
    SavedSettings,
    Scenario,
    ScenarioRun,
    ScenarioStep,
    Schedule,
    ServiceAssignment,
    ServiceList,
    ServiceQuota,
    SiteBlock,
    TimeQuota,
    Timer,
    ValidationError,
    WebsitePolicy,
    normalize_domain,
    normalize_mac_address,
    time_quota_counter_id,
)
from jittersim.config.protection import (
    observation_is_protected,
    protected_addresses,
    protected_macs,
    station_mac,
    station_macs_by_address,
)
from jittersim.config.service_catalogue import SERVICE_CATALOGUE
from jittersim.config.storage import (
    ConfigRepository,
    DatabasePath,
    DeviceConflictError,
    RepositoryBusy,
    StorageError,
    TrafficRateSettingsError,
)
from jittersim.config.transfer import (
    SETTINGS_FORMAT,
    SETTINGS_VERSION,
    check_settings,
    keep_sections_missing_from,
    settings_to_payload,
)
from jittersim.config.workflows import WorkflowStore
from jittersim.engine.access import build_access_plan
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import (
    MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN,
    MAX_SHAPED_MARKS_PER_PLAN,
    PlanError,
)
from jittersim.engine.conditions import device_conditions, everyone_else_condition
from jittersim.engine.eligibility import (
    device_protection_states,
    eligible_clients,
    station_snapshot,
)
from jittersim.engine.plan import EVERYONE_ELSE_QUEUE_ID, GENERAL_QUEUE_ID, NetworkPlan
from jittersim.engine.progression import progression_factor
from jittersim.engine.runtime import PreparedRuntimePlan, RuntimeScheduler
from jittersim.engine.scenarios import active_scenario_runs
from jittersim.engine.schedules import active_schedule
from jittersim.engine.service import compile_stored_plan
from jittersim.engine.website import describe_timed_out_names, without_dns
from jittersim.networking.applier import MAX_PROTECTED_MACS, _validate_access_point_settings
from jittersim.networking.dashboard_access import (
    MAX_EFFECTIVE_ACCESS_DEVICES,
    AccessError as DashboardAccessError,
    normalize_mac as normalize_access_mac,
    validate_label as validate_access_label,
)
from jittersim.networking.deadlines import deadline_scope, scope_remaining
from jittersim.networking.helper import (
    HELPER_OUT_OF_DATE_MESSAGE,
    WIFI_DETAILS_MISSING_MESSAGE,
    HelperClient,
    HelperClientError,
)
from jittersim.networking.router import (
    ROUTER_CHECK_MESSAGES,
    safe_router_check_message,
    safe_router_import_message,
    safe_router_wifi_message,
)
from jittersim.networking.wifi import DEFAULT_WIFI_NAME
from jittersim.networking.renderer import UPLOAD_DEVICE, withheld_upload_settings
from jittersim.networking.wifi_security import SECURITY_MODES
from jittersim.api.router_operations import RouterOperations
from jittersim.api.wifi_operations import WifiOperations, WifiOutcome, valid_request_id, issue_request_id
from jittersim.engine.timeline import overlapping_schedules

LOGGER = logging.getLogger(__name__)
# A failed protection sync is retried by the next dashboard request at most this often.
PROTECTED_ACCESS_RETRY_SECONDS = 10.0
# A restarted helper is reconciled at once, but never more than once a second.
PROTECTED_ACCESS_RESTART_RETRY_SECONDS = 1.0
PROTECTED_ACCESS_SYNC_ATTEMPTS = 3
# Startup restore asks a helper that answers helper-busy at most this often (one poll apart).
STARTUP_HELPER_BUSY_ATTEMPTS = 4
WIFI_COPY_BUSY_MESSAGE = "The router is busy right now. Try again in a moment."
MAX_JSON_BODY_BYTES = 64 * 1024
# One block or profile call may carry this many names.
MAX_BATCH_DOMAINS = 64
TLS_HANDSHAKE_TIMEOUT_SECONDS = 5.0
REQUEST_INPUT_TIMEOUT_SECONDS = 10.0
MAX_PENDING_CONNECTION_LIFETIME_SECONDS = 30.0
MAX_PENDING_DASHBOARD_CONNECTIONS = 16
# While every ordinary worker is busy, a waiting connection is read here until its
# request is complete. Only a whole, authorized Panic request may then take a
# reserved worker. This bounds what one waiting connection may buffer.
MAX_WAITING_REQUEST_BYTES = 16 * 1024
# Replacing waiting connections is logged at most this often, with a count.
EVICTION_WARNING_INTERVAL_SECONDS = 60.0
# Requests handled at the same time. A request over the limit gets a clear 503.
MAX_DASHBOARD_WORKERS = 16
# Extra threads kept for Panic alone, so it still gets in when every ordinary slot is busy.
RESERVED_PANIC_WORKERS = 2
RESPONSE_SEND_TIMEOUT_SECONDS = 5.0
# A settings export with many profiles is larger than ordinary requests.
MAX_IMPORT_BODY_BYTES = 1024 * 1024
WRITE_PATHS = frozenset(
    (
        "/api/homepage/pairing-code",
        "/api/homepage/revoke",
        "/api/profiles",
        "/api/profiles/ranges",
        "/api/general-profile",
        "/api/general-profile/stop",
        "/api/profiles/delete",
        "/api/devices",
        "/api/ad-blocking",
        "/api/unaffected-devices",
        "/api/unaffected-devices/delete",
        "/api/connected-devices/group",
        "/api/devices/delete",
        "/api/groups",
        "/api/groups/delete",
        "/api/schedules",
        "/api/schedules/delete",
        "/api/timers",
        "/api/timers/cancel",
        "/api/scenarios",
        "/api/scenarios/delete",
        "/api/scenarios/start",
        "/api/scenarios/stop",
        "/api/progressions",
        "/api/progressions/stop",
        "/api/data-limits",
        "/api/data-limits/delete",
        "/api/data-limits/reset",
        "/api/group-data-limits",
        "/api/group-data-limits/delete",
        "/api/group-data-limits/reset",
        "/api/service-quotas",
        "/api/service-quotas/delete",
        "/api/service-quotas/reset",
        "/api/service-assignments",
        "/api/service-assignments/delete",
        "/api/service-lists",
        "/api/service-lists/delete",
        "/api/time-quotas",
        "/api/time-quotas/delete",
        "/api/time-quotas/reset",
        "/api/time-quotas/start",
        "/api/time-quotas/stop",
        "/api/policies",
        "/api/everyone-else",
        "/api/unaffected-devices",
        "/api/unaffected-devices/delete",
        "/api/access-point",
        "/api/access-point/reset-name",
        "/api/router/sync-ap",
        "/api/router/preview-ap",
        "/api/router/wifi",
        "/api/router/check",
        "/api/router/status",
        "/api/router/operation",
        "/api/router/wifi-credentials",
        "/api/connected-devices/apply-profile",
        "/api/connected-device-labels",
        "/api/connected-device-labels/delete",
        "/api/traffic-settings",
        "/api/gateway-persistence",
        "/api/observations/scan",
        "/api/observations/add-to-profile",
        "/api/observations/add-global-block",
        "/api/observations/remove-global-block",
        "/api/import",
        "/api/activity/clear",
        "/api/apply",
        "/api/reset",
        "/api/dashboard-access/register-self",
        "/api/dashboard-access/remove",
        "/api/dashboard-access/move",
        "/api/dashboard-access/move-undo",
        "/api/dashboard-access/sync-protected",
    )
)
# A signed release sent over HTTPS when the Mac cannot use SSH (see handle_release_upload).
RELEASE_UPLOAD_PATH = "/api/updates/upload"
RELEASE_UPLOAD_HEADER = "X-Jittersim-Archive-Bytes"
# The whole upload (headers, archive and signature) must arrive within this many seconds. The
# dashboard serves one request at a time so a slow sender cannot hold it for longer.
RELEASE_UPLOAD_SECONDS = 120.0
WRITE_PATHS = (WRITE_PATHS | WORKFLOW_WRITES | ACCESS_WRITES | {"/api/schedules/preview"}
               | {RELEASE_UPLOAD_PATH})
ACTION_PATHS = frozenset(
    ("/api/apply", "/api/reset", "/api/observations/scan", "/api/update-preferences",
     "/api/access-point/reset-name", "/api/updates/check-online", "/api/updates/retry")
)
PUBLIC_READ_PATHS = frozenset(("/", "/api/health"))
# What the dashboard says when the standard library refuses a request before any route runs.
REFUSED_REQUEST_SENTENCES = {
    400: "the request could not be understood",
    414: "the request address is too long",
    431: "the request headers are too large",
    501: "this request method is not supported",
    505: "this HTTP version is not supported",
}
# Panic runs ahead of other writes: it never waits for the write lock of an apply in flight.
PRIORITY_WRITE_PATHS = frozenset(("/api/normal-access",))
# Panic and Reset check the device with the helper's emergency check, which does
# not wait behind an ordinary verification (review T5 of PR 91).
EMERGENCY_WRITE_PATHS = frozenset(("/api/normal-access", "/api/reset"))
# Every helper call of that check (the emergency check, a protection resync, the
# fallback for an older helper) shares this budget. With nothing left the answer
# is 503, which the page retries, never access (review G4).
EMERGENCY_CHECK_BUDGET_SECONDS = 8.0
# What the Panic route needs besides the device check and the helper client: a
# few small database writes and the response. The browser's Panic timeout is at
# least the sum (docs/architecture.md, "Panic time budget").
PANIC_DATABASE_ALLOWANCE_SECONDS = 5.0
# Panic's device check reads saved protection after a helper restart. It waits for
# the repository at most this long, within EMERGENCY_CHECK_BUDGET_SECONDS, and is
# helper-busy (retried) without a snapshot.
EMERGENCY_REPOSITORY_WAIT_SECONDS = 2.0
# The product name shown after the token is accepted. The public page never carries it.
PRODUCT_NAME = "JitterSim"
PRODUCT_COMMAND = "jittersim"
DEFAULT_ACCESS_POINT_SSID = DEFAULT_WIFI_NAME


def _neutral_icon() -> bytes:
    """A 16 by 16 icon file with a plain dark square and a light dot and no name in it."""

    import struct
    import zlib

    size = 16
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            inside = (x - 7.5) ** 2 + (y - 7.5) ** 2 <= 22
            row += bytes((112, 183, 255, 255) if inside else (11, 18, 32, 255))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )
    directory = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), 6 + 16)
    return directory + entry + png


NEUTRAL_ICON = _neutral_icon()


DASHBOARD_HTML = assemble_dashboard()


# The phone layout comes last so that it wins over the earlier narrow-screen rules.


_DASHBOARD_PAGE_CACHE: Dict[str, Tuple[str, bytes, bytes, str]] = {}


def _dashboard_page(page_html: Optional[str] = None) -> Tuple[bytes, bytes, str]:
    """Return the page bytes, a gzip copy and an ETag, made once for each page text."""

    page = DASHBOARD_HTML if page_html is None else page_html
    cached = _DASHBOARD_PAGE_CACHE.get("page")
    if cached is None or cached[0] != page:
        raw = page.encode("utf-8")
        etag = 'W/"' + hashlib.sha256(raw).hexdigest()[:32] + '"'
        cached = (page, raw, gzip.compress(raw, 6, mtime=0), etag)
        _DASHBOARD_PAGE_CACHE["page"] = cached
    return cached[1], cached[2], cached[3]


def dashboard_page_bytes(page_html: Optional[str] = None) -> bytes:
    """The page as sent without compression. _send_response recognises this exact object."""

    return _dashboard_page(page_html)[0]


def _accepts_gzip(header: str) -> bool:
    explicit = []
    wildcard = []
    for part in header.lower().split(","):
        name, _, parameters = part.strip().partition(";")
        name = name.strip()
        if name not in ("gzip", "*"):
            continue
        quality = 1.0
        if parameters:
            match = re.fullmatch(r"\s*q\s*=\s*(0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)\s*", parameters)
            quality = float(match.group(1)) if match else 0.0
        (explicit if name == "gzip" else wildcard).append(quality)
    choices = explicit if explicit else wildcard
    return bool(choices) and min(choices) > 0




def _inline_source_hashes(tag: str, page: str = DASHBOARD_HTML) -> str:
    """Hash every inline <tag> block so the policy allows each of them."""

    hashes = []
    for content in re.findall(rf"<{tag}>(.*?)</{tag}>", page, re.DOTALL):
        digest = hashlib.sha256(content.encode("utf-8")).digest()
        hashes.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
    return " ".join(hashes)


# The dashboard may run only its own inline script and style, and talk only
# to this server.
DASHBOARD_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'none'",
        f"script-src {_inline_source_hashes('script')}",
        f"style-src {_inline_source_hashes('style')}",
        "connect-src 'self'",
        "img-src 'self' data:",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)


def _impairment_payload(impairment: Impairment) -> Dict[str, Any]:
    return impairment_payload(impairment)


def _profile_payload(profile: Profile) -> Dict[str, Any]:
    return profile_payload(profile)


def _device_display_name(
    device: Optional[Device], labels_by_mac: Dict[str, Dict[str, str]]
) -> str:
    """Resolve the name shown for a saved device."""

    if device is None:
        return UNKNOWN_DEVICE_NAME
    label = labels_by_mac.get(device.mac_address or "", {}).get("display_name")
    return resolve_device_name(saved_label=label, saved_name=device.name)


def _device_title(
    device: Optional[Device], labels_by_mac: Dict[str, Dict[str, str]], fallback_id: str
) -> str:
    """Name a device in a payload that lists many of them.

    An unnamed device shows its address so that two of them never look the same.
    A device that no longer exists shows the id it was saved under.
    """

    if device is None:
        return fallback_id
    return saved_device_title(
        saved_label=labels_by_mac.get(device.mac_address or "", {}).get("display_name"),
        saved_name=device.name,
        ipv4_address=device.ipv4_address,
    )


IMPACT_NO_HELPER_MESSAGE = "Impact needs the Pi helper."
IMPACT_RESET_MESSAGE = "Network is reset. Choose Start to start impact checks."
IMPACT_OUT_OF_DATE_MESSAGE = (
    "The networking helper is out of date. Update it to see the impact check."
)
IMPACT_UNREADABLE_MESSAGE = "Impact could not be read right now."


def _impact_queue_name(
    queue_id: str,
    *,
    devices_by_id: Dict[str, Device],
    labels_by_mac: Dict[str, Dict[str, str]],
    list_names: Dict[str, str],
) -> Tuple[str, str]:
    """Name one queue of the impact check and say what kind of queue it is."""

    if queue_id == GENERAL_QUEUE_ID:
        return "Everyone", "Everyone"
    for prefix, label in (("quota:", "Service quota"), ("assignment:", "Browser service profile")):
        if queue_id.startswith(prefix):
            return f"{label} {queue_id[len(prefix):]}", label
    if queue_id.startswith("servicelist:"):
        list_id = queue_id[len("servicelist:"):]
        return f"Service list {list_names.get(list_id, list_id)}", "Service list"
    device = devices_by_id.get(queue_id)
    if device is not None:
        return _device_title(device, labels_by_mac, queue_id), "Device"
    return queue_id, "Queue"


def _device_payload(
    device: Device,
    *,
    labels_by_mac: Dict[str, Dict[str, str]],
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = state or {}
    return {
        "effective_state": state.get("effective_state", "active"),
        "effective_note": state.get("effective_note"),
        "protected_match": state.get("protected_match"),
        "id": device.id,
        "name": device.name,
        "display_name": _device_display_name(device, labels_by_mac),
        "ipv4_address": str(device.ipv4_address),
        "profile_id": device.profile_id,
        "mac_address": device.mac_address,
    }


def _observation_display_name(
    station: Dict[str, Optional[str]],
    saved: Optional[Device],
    labels_by_mac: Dict[str, Dict[str, str]],
) -> str:
    """Resolve the name shown for one client in Traffic discovery."""

    mac_address = station.get("mac_address") or (saved.mac_address if saved else None)
    label = labels_by_mac.get(mac_address or "", {}).get("display_name")
    return resolve_device_name(
        saved_label=label,
        saved_name=saved.name if saved else None,
        hostname=station.get("hostname"),
    )


def _connected_device_payload(
    device: Dict[str, Any],
    *,
    assignments_by_address: Dict[str, Device],
    labels_by_mac: Dict[str, Dict[str, str]],
    unaffected_macs: frozenset = frozenset(),
) -> Dict[str, Any]:
    """Add saved display details and any matching profile to one station."""

    mac_address = device["mac_address"]
    saved_label = labels_by_mac.get(mac_address)
    hostname = device.get("hostname")
    suggested_type = guess_device_type(hostname)
    assignment = next(
        (saved for saved in assignments_by_address.values() if saved.mac_address == mac_address),
        None,
    )
    return {
        **device,
        "display_name": (
            saved_label["display_name"]
            if saved_label and saved_label["display_name"]
            else hostname or device.get("ipv4_address") or mac_address
        ),
        "saved_display_name": saved_label["display_name"] if saved_label else "",
        "saved_device_type": saved_label["device_type"] if saved_label else "",
        "has_saved_label": saved_label is not None,
        "device_type": saved_label["device_type"] if saved_label else suggested_type,
        "device_type_source": (
            "saved"
            if saved_label is not None
            else "hostname suggestion"
            if suggested_type != "unknown"
            else "unknown"
        ),
        "assigned_device_id": assignment.id if assignment is not None else None,
        "assigned_device_name": assignment.name if assignment is not None else None,
        "assigned_profile_id": assignment.profile_id if assignment is not None else None,
        "unaffected": mac_address in unaffected_macs,
    }


def _device_group_payload(
    group: DeviceGroup,
    *,
    repository: ConfigRepository,
    labels_by_mac: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    """Build the dashboard representation of one device group."""

    profile = repository.get_profile(group.profile_id) if group.profile_id else None
    devices = [
        _device_payload(device, labels_by_mac=labels_by_mac)
        for device_id in group.device_ids
        if (device := repository.get_device(device_id)) is not None
    ]
    return {
        "id": group.id,
        "name": group.name,
        "device_ids": list(group.device_ids),
        "devices": devices,
        "profile_id": group.profile_id,
        "profile_name": profile.name if profile is not None else None,
    }


def _schedule_payload(schedule: Schedule) -> Dict[str, Any]:
    return asdict(schedule)


def _timer_payload(timer: Timer) -> Dict[str, Any]:
    return asdict(timer)


def _scenario_payload(scenario: Scenario) -> Dict[str, Any]:
    return {
        "id": scenario.id,
        "name": scenario.name,
        "steps": [
            {"profile_id": step.profile_id, "duration_seconds": step.duration_seconds}
            for step in scenario.steps
        ],
        "loop": scenario.loop,
        "total_seconds": scenario.total_seconds,
    }


def _scenario_run_payload(
    run: ScenarioRun, scenario: Scenario, position: Any, *, repository: ConfigRepository
) -> Dict[str, Any]:
    """Describe one running scenario, its current step and the time left."""

    if run.device_id is not None:
        target_name = _device_title(
            repository.get_device(run.device_id),
            repository.list_connected_device_labels(),
            run.device_id,
        )
    elif run.group_id is not None:
        group = repository.get_device_group(run.group_id)
        target_name = "Group " + (group.name if group is not None else run.group_id)
    else:
        target_name = "Everyone else"
    profile = repository.get_profile(position.profile_id)
    return {
        **asdict(run),
        "scenario_name": scenario.name,
        "target_name": target_name,
        "loop": scenario.loop,
        "step_index": position.step_index,
        "step_count": len(scenario.steps),
        "profile_id": position.profile_id,
        "profile_name": profile.name if profile is not None else position.profile_id,
        "step_remaining_seconds": position.step_remaining_seconds,
        "total_remaining_seconds": position.total_remaining_seconds,
    }


def _data_limit_payload(
    data_limit: DataLimit,
    *,
    repository: ConfigRepository,
) -> Dict[str, Any]:
    """Build the dashboard representation of one device quota."""

    device = repository.get_device(data_limit.device_id)
    labels_by_mac = repository.list_connected_device_labels()
    profile = repository.get_profile(data_limit.profile_id)
    return {
        **asdict(data_limit),
        "reset_times": list(data_limit.reset_times),
        "device_name": _device_title(device, labels_by_mac, data_limit.device_id),
        "profile_name": profile.name if profile is not None else data_limit.profile_id,
        "remaining_bytes": max(0, data_limit.limit_bytes - data_limit.used_bytes),
        "reached": data_limit.used_bytes >= data_limit.limit_bytes,
    }


def _group_data_limit_payload(
    data_limit: GroupDataLimit,
    *,
    repository: ConfigRepository,
) -> Dict[str, Any]:
    """Build the dashboard representation of one shared group quota."""

    group = repository.get_device_group(data_limit.group_id)
    profile = repository.get_profile(data_limit.profile_id)
    return {
        **asdict(data_limit),
        "reset_times": list(data_limit.reset_times),
        "group_name": group.name if group is not None else data_limit.group_id,
        "device_ids": list(group.device_ids) if group is not None else [],
        "profile_name": profile.name if profile is not None else data_limit.profile_id,
        "remaining_bytes": max(0, data_limit.limit_bytes - data_limit.used_bytes),
        "reached": data_limit.used_bytes >= data_limit.limit_bytes,
    }


def _service_quota_payload(
    quota: ServiceQuota,
    *,
    repository: ConfigRepository,
) -> Dict[str, Any]:
    """Build the dashboard representation of one service quota."""

    device = repository.get_device(quota.device_id)
    labels_by_mac = repository.list_connected_device_labels()
    profile = repository.get_profile(quota.profile_id)
    return {
        **asdict(quota),
        "domains": list(quota.domains),
        "reset_times": list(quota.reset_times),
        "device_name": _device_title(device, labels_by_mac, quota.device_id),
        "profile_name": profile.name if profile is not None else quota.profile_id,
        "remaining_bytes": max(0, quota.limit_bytes - quota.used_bytes),
        "reached": quota.used_bytes >= quota.limit_bytes,
    }


def _service_assignment_payload(
    assignment: ServiceAssignment, *, repository: ConfigRepository
) -> Dict[str, Any]:
    device = repository.get_device(assignment.device_id)
    labels_by_mac = repository.list_connected_device_labels()
    profile = repository.get_profile(assignment.profile_id)
    return {
        **asdict(assignment),
        "domains": list(assignment.domains),
        "device_name": _device_title(device, labels_by_mac, assignment.device_id),
        "profile_name": profile.name if profile is not None else assignment.profile_id,
    }


def _service_list_payload(
    service_list: ServiceList, *, repository: ConfigRepository, outage_now: bool = False
) -> Dict[str, Any]:
    """Describe one list. `outage_now` says the network cuts it for a random outage."""

    profile = repository.get_profile(service_list.profile_id)
    return {
        "id": service_list.id,
        "name": service_list.name,
        "profile_id": service_list.profile_id,
        "profile_name": profile.name if profile is not None else service_list.profile_id,
        "services": list(service_list.services),
        "hostnames": list(service_list.hostnames),
        "domains": list(service_list.domains),
        "enabled": service_list.enabled,
        "outage_now": service_list.enabled and outage_now,
    }


def _service_catalogue_payload() -> list:
    return [
        {"key": service.key, "name": service.name, "domains": list(service.domains)}
        for service in SERVICE_CATALOGUE
    ]


def _time_quota_payload(
    quota: TimeQuota,
    *,
    repository: ConfigRepository,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the dashboard representation of one timed service quota."""

    current_time = int(time.time()) if now is None else now
    device = repository.get_device(quota.device_id)
    labels_by_mac = repository.list_connected_device_labels()
    profile = repository.get_profile(quota.profile_id)
    window_active = quota.started_at <= current_time < quota.expires_at
    return {
        **asdict(quota),
        "domains": list(quota.domains),
        "reset_times": list(quota.reset_times),
        "device_name": _device_title(device, labels_by_mac, quota.device_id),
        "profile_name": profile.name if profile is not None else quota.profile_id,
        "remaining_seconds": max(0, quota.limit_seconds - quota.used_seconds),
        "window_remaining_seconds": (
            max(0, quota.expires_at - current_time) if quota.expires_at else 0
        ),
        "window_active": window_active,
        "running": quota.started_at > 0 and window_active,
        "reached": window_active and quota.used_seconds >= quota.limit_seconds,
    }


def _progression_payload(
    progression: Progression,
    *,
    repository: ConfigRepository,
    now: int,
    traffic_bytes: int = 0,
) -> Dict[str, Any]:
    device = repository.get_device(progression.device_id)
    labels_by_mac = repository.list_connected_device_labels()
    start_profile = repository.get_profile(progression.start_profile_id)
    end_profile = repository.get_profile(progression.end_profile_id)
    total_queries = repository.total_domain_queries(str(device.ipv4_address)) if device else 0
    factor = progression_factor(
        progression=progression,
        now=now,
        traffic_queries=total_queries,
        traffic_bytes=traffic_bytes,
    )
    return {
        **asdict(progression),
        "device_name": _device_title(device, labels_by_mac, progression.device_id),
        "start_profile_name": (
            start_profile.name
            if start_profile is not None
            else progression.start_profile_id
        ),
        "end_profile_name": (
            end_profile.name if end_profile is not None else progression.end_profile_id
        ),
        "progress_percent": round(factor * 100),
        "remaining_seconds": max(
            0,
            progression.started_at + progression.duration_seconds - now,
        ),
        "traffic_queries": max(0, total_queries - progression.traffic_baseline_queries),
        "traffic_bytes": max(0, traffic_bytes - progression.traffic_baseline_bytes),
    }


def _policies_payload(repository: ConfigRepository) -> Dict[str, Any]:
    mac_policy = repository.get_mac_policy()
    website_policy = repository.get_website_policy()
    return {
        "mac": asdict(mac_policy),
        "websites": asdict(website_policy),
        "options": asdict(repository.get_access_options()),
        "site_blocks": [asdict(rule) for rule in repository.list_site_blocks()],
        "version": repository.get_runtime_state().configuration_version,
    }


def _everyone_else_now(repository: ConfigRepository) -> Tuple[Optional[str], Optional[str]]:
    """Return the profile everyone else gets now and the schedule giving it, if any.

    This is the settings' answer in the Pi's local time; the scheduler applies
    it while the network runs. A running scenario wins over schedules.
    """

    for run, _, position in active_scenario_runs(repository, now=time.time()):
        if run.everyone_else:
            return position.profile_id, None
    schedule = active_schedule(
        schedules=repository.list_schedules(),
        everyone_else=True,
        moment=datetime.fromtimestamp(time.time()),
    )
    if schedule is not None:
        return schedule.profile_id, schedule.id
    return repository.get_everyone_else_profile_id(), None


def _everyone_else_payload(repository: ConfigRepository) -> Dict[str, Any]:
    active_profile_id, active_schedule_id = _everyone_else_now(repository)
    return {
        "profile_id": repository.get_everyone_else_profile_id(),
        "active_profile_id": active_profile_id,
        "active_schedule_id": active_schedule_id,
        "unaffected_devices": [
            {"mac_address": device.mac_address, "name": device.name}
            for device in repository.list_unaffected_devices()
        ],
    }


def _domains_from_payload(payload: Dict[str, Any]) -> Tuple[str, ...]:
    """Return the unique normalized names of a call that sends domain or domains but not both.

    Every name is checked before the caller saves anything so one bad name rejects the call.
    """

    if not isinstance(payload, dict) or ("domain" in payload) == ("domains" in payload):
        raise ValueError("send either domain or domains")
    raw = [payload["domain"]] if "domain" in payload else payload["domains"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_BATCH_DOMAINS:
        raise ValueError("send between 1 and %d names" % MAX_BATCH_DOMAINS)
    return tuple(dict.fromkeys(normalize_domain(item) for item in raw))


def _domain_observation_payload(observation: DomainObservation) -> Dict[str, Any]:
    return {
        "domain": observation.domain,
        "query_count": observation.query_count,
        "first_seen": observation.first_seen,
        "last_seen": observation.last_seen,
    }


def _domain_list_cleared_payload(repository: ConfigRepository) -> Optional[dict]:
    """Say that an upgrade emptied the Traffic list and the list has not filled again.

    The dashboard and the command line show the message as it is so both use the same words.
    """

    clearing = repository.get_domain_list_clearing()
    if clearing is None:
        return None
    cleared_at, rows_cleared = clearing
    day = datetime.fromtimestamp(cleared_at)
    return {
        "cleared_at": cleared_at,
        "rows_cleared": rows_cleared,
        "message": (
            f"The list was cleared on {day.day} {day.strftime('%B %Y')} when JitterSim was upgraded "
            "because protected devices exist. It fills again as devices browse. "
            "Names are kept for 30 days from their last sighting."
        ),
    }


def _labelled_domain_payloads(
    rows: Sequence[DomainObservation], blocked_domains: Collection[str] = ()
) -> List[Dict[str, Any]]:
    """Add a label and a group and a seen-with guess and the global block state to observed names.

    A name counts as blocked only when it is an exact entry of the global block list: that is
    the entry Unblock removes. While household access rules are on the Pi's filtered resolver
    also refuses every name under a listed name. Without them the engine blocks only the
    addresses of each listed name while JitterSim runs.
    """

    hints = seen_with([(row.domain, row.first_seen) for row in rows])
    return [
        {
            **_domain_observation_payload(row),
            "label": label_domain(row.domain).as_dict(),
            "group": group_domain(row.domain),
            "seen_with": hints.get(row.domain, []),
            "globally_blocked": row.domain in blocked_domains,
        }
        for row in rows
    ]


def _runtime_state_payload(state: RuntimeState) -> Dict[str, Any]:
    return {
        "configuration_version": state.configuration_version,
        "observed_state": state.observed_state,
        "last_action": state.last_action,
        "last_result": state.last_result,
        "last_error": state.last_error,
    }


RESET_REQUEST_PATHS = frozenset(("/api/reset", "/api/normal-access"))
# Recorded when a device-only start failed and the helper cleanup did not succeed yet.
DEVICE_CLEANUP_ERROR = "device-only cleanup failed"

_RESET_SAVED_ONLY = {
    "live_applied": False,
    "message": "saved; the network is reset, so choose Start to use it",
}


def _json_bytes(payload: Dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


@dataclass
class _PendingConnection:
    address: Any
    deadline: float
    handshake: bool
    expires_at: float
    want_write: bool = False
    # The client has sent something (TLS handshake or request bytes).
    progressed: bool = False
    # Request bytes read while every ordinary worker was busy.
    buffer: bytearray = field(default_factory=bytearray)


_RESET_REQUESTS_LOCK = threading.Lock()


class ControlServer(SafeServerErrors, socketserver.ThreadingMixIn, HTTPServer):
    """Serve configuration and plan data from one repository.

    Each request runs on its own daemon thread so one slow request (an apply or a
    scan or a router call) cannot hold up Panic or the health check. The accept
    loop still advances every TLS handshake without blocking, and at most
    `MAX_DASHBOARD_WORKERS` requests run at once.
    """

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    # Browsers open several connections at once. The default queue of five resets the rest.
    request_queue_size = 64
    request_timeout_seconds = 5.0
    shutdown_timeout_seconds = 5.0
    # Where an uploaded release waits for the root helper (see jittersim.updates.stage_upload).
    release_spool = updates.UPLOAD_SPOOL
    release_upload_lock = release_uploads.LOCK_PATH
    release_upload_seconds = RELEASE_UPLOAD_SECONDS
    # After a reboot the dashboard can start before the helper's socket exists.
    helper_startup_wait_seconds = 30.0
    helper_poll_seconds = 0.5

    def __init__(
        self,
        server_address: Tuple[str, int],
        repository: ConfigRepository,
        admin_token: Optional[str] = None,
        helper_client: Optional[HelperClient] = None,
        scheduler: Optional[RuntimeScheduler] = None,
        tls_certfile: Optional[Path] = None,
        tls_keyfile: Optional[Path] = None,
        homepage_host: Optional[str] = None,
        homepage_port: int = 8443,
        homepage_certfile: Path = Path("/etc/jittersim/dashboard-wifi.crt"),
        tls_wifi_certfile: Optional[Path] = None,
        tls_wifi_network: str = "192.168.50.0/24",
        trust_ca_path: Optional[Path] = None,
        local_name: Optional[str] = None,
        name_status_path: Optional[Path] = None,
    ) -> None:
        if admin_token == "":
            raise ValueError("admin_token must not be empty")
        if (tls_certfile is None) != (tls_keyfile is None):
            raise ValueError("TLS certificate and key must be provided together")
        tls_context = None
        wifi_context = None
        if tls_certfile is not None and tls_keyfile is not None:
            tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
            tls_context.load_cert_chain(str(tls_certfile), str(tls_keyfile))
            if tls_wifi_certfile is not None:
                # Devices on the test Wi-Fi see a certificate that names only the
                # neutral name and the gateway, before they sign in.
                wifi_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                wifi_context.minimum_version = ssl.TLSVersion.TLSv1_2
                wifi_context.load_cert_chain(str(tls_wifi_certfile), str(tls_keyfile))
        wifi_network = ipaddress.ip_network(tls_wifi_network)
        bind_host = server_address[0]
        if tls_context is None and bind_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("TLS is required when the dashboard listens beyond this device")
        self.repository = repository
        WorkflowStore(repository).checkpoint("Initial settings")
        self.admin_token = admin_token
        self.trust_ca_path = trust_ca_path
        self.local_name = local_name or read_local_name()
        self.name_status_path = name_status_path
        self.helper_client = helper_client
        self.scheduler = scheduler
        # Protection-derived dashboard access: the last confirmed sync with the root helper.
        self._protected_access_lock = threading.Lock()
        self._protected_access_state: Dict[str, Any] = {
            "synced": None, "protected": 0, "granted": 0, "not_eligible": [], "error": None,
        }
        self._protected_access_key: Optional[tuple] = None
        self._protected_access_attempted = float("-inf")
        self.router_operations = RouterOperations(
            self._router_execute, str(admin_token) + "\0" + str(getattr(helper_client, "socket_path", "local")),
        )
        # Wi-Fi restarts answer first and restart later. Their outcome waits to be read.
        self.wifi_operations = WifiOperations()
        self.secure_transport = tls_context is not None
        self._tls_context = tls_context
        self._tls_wifi_context = wifi_context
        self._tls_wifi_network = wifi_network
        self._pending_connections: Dict[socket.socket, _PendingConnection] = {}
        self._request_inputs: Dict[socket.socket, Tuple[bytes, float]] = {}
        self._stop_connections = threading.Event()
        self._workers_lock = threading.Lock()
        self._active_workers = 0
        self._request_workers = RequestWorkers("jittersim-request", self._uncertain_worker_start)
        self._reserved_requests: Set[socket.socket] = set()
        self._evictions_unreported = 0
        self._eviction_warned_at = float("-inf")
        self.homepage_store = None
        self.homepage_server = None
        self.homepage_worker = None
        super().__init__(server_address, ControlRequestHandler)
        if homepage_host is not None and tls_context is not None and helper_client is not None:
            from .homepage import PairingStore, PolicyServer
            try:
                if homepage_host != "192.168.50.1" or homepage_port != 8443 or not admin_token:
                    raise ValueError("Homepage pairing requires the AP address, port 8443 and admin authentication")
                store = PairingStore(helper_client)
                policy_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                policy_context.minimum_version = ssl.TLSVersion.TLSv1_2
                policy_context.load_cert_chain(str(homepage_certfile), str(tls_keyfile))
                listener = PolicyServer((homepage_host, homepage_port), store, policy_context)
                worker = threading.Thread(target=listener.serve_forever,
                    daemon=True, name="homepage-policy")
                worker.start()
                self.homepage_store = store
                self.homepage_server = listener
                self.homepage_worker = worker
            except (OSError, ValueError, RuntimeError):
                if 'listener' in locals():
                    if 'worker' in locals() and worker.is_alive():
                        listener.shutdown()
                    listener.server_close()
                if 'store' in locals():
                    store.close()
                LOGGER.warning("Homepage pairing listener unavailable. Dashboard controls remain available.")
        self.dashboard_page_html = DASHBOARD_HTML.replace(
            "@@EASY_ADDRESS@@", html.escape(easy_address(self), quote=True)
        )

    @property
    def active_workers(self) -> int:
        """Return how many requests are being handled right now."""

        self._request_workers.reap()
        with self._workers_lock:
            return self._active_workers

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        self._request_workers.reap()
        with self._workers_lock:
            accepted = self._active_workers < MAX_DASHBOARD_WORKERS + RESERVED_PANIC_WORKERS
            if accepted:
                if self._active_workers >= MAX_DASHBOARD_WORKERS:
                    # Only Panic is served from the reserve. The handler refuses the rest.
                    self._reserved_requests.add(request)
                self._active_workers += 1
        if not accepted:
            self._refuse_request(request)
            return
        try:
            self._request_workers.start(
                lambda: self.process_request_thread(request, client_address),
                abandon=lambda: self.shutdown_request(request), release=self._release_worker,
            )
        except Exception:
            # The worker manager retains ambiguous ownership. The accept loop must
            # not close a socket that a started worker may still be handling.
            LOGGER.exception("dashboard worker startup failed")

    def _release_worker(self) -> None:
        with self._workers_lock:
            self._active_workers -= 1

    def _uncertain_worker_start(self) -> None:
        self._stop_connections.set()
        if self.scheduler is not None:
            self.scheduler.halt()

    def is_reserved_request(self, request: socket.socket) -> bool:
        """Tell whether this request runs in a slot that is kept for Panic."""

        with self._workers_lock:
            return request in self._reserved_requests

    def _refuse_request(self, request: socket.socket, *, busy_body: bool = False) -> None:
        """Tell a client over the limit to retry instead of leaving it hanging."""

        # The accept loop must never wait for a client, so nothing here blocks.
        body = _json_bytes({"error": "the dashboard is busy, try again"}) if busy_body else b""
        kind = b"Content-Type: application/json\r\n" if busy_body else b""
        try:
            request.setblocking(False)
            request.send(
                b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 1\r\n" + kind
                + b"Content-Length: " + str(len(body)).encode("ascii")
                + b"\r\nConnection: close\r\n\r\n" + body
            )
            # Read what the client already sent so closing does not reset the reply.
            request.recv(65536)
        except (OSError, ssl.SSLError):
            pass
        self.shutdown_request(request)

    def get_request(self) -> Tuple[socket.socket, Any]:
        """Advance waiting connections without serial waits for idle clients."""
        attempted = bool(self._pending_connections)
        while not self._stop_connections.is_set():
            now = time.monotonic()
            for connection, pending in tuple(self._pending_connections.items()):
                if now >= min(pending.deadline, pending.expires_at):
                    self._close_pending(connection)
            if attempted and not self._pending_connections:
                raise TimeoutError("dashboard connections expired")
            readers = [self.socket] + [connection for connection, pending
                                      in self._pending_connections.items() if not pending.want_write]
            writers = [connection for connection, pending
                       in self._pending_connections.items() if pending.want_write]
            readable, writable, _ = select.select(readers, writers, [], 0.05)
            # Waiting connections that are ready go first. A newcomer accepted
            # before them could otherwise replace one whose request had arrived.
            for connection in readable + writable:
                if connection is self.socket:
                    continue
                pending = self._pending_connections.get(connection)
                if pending is None:
                    continue
                pending.progressed = True
                try:
                    if pending.handshake:
                        connection.do_handshake()
                        pending.handshake = False
                        pending.deadline = min(
                            pending.expires_at, time.monotonic() + REQUEST_INPUT_TIMEOUT_SECONDS)
                    if not pending.buffer and self.active_workers < MAX_DASHBOARD_WORKERS:
                        prefix = connection.recv(1)
                        if not prefix:
                            self._close_pending(connection)
                            continue
                    else:
                        waiting = self._read_waiting_request(connection, pending)
                        if waiting is None:
                            continue
                        prefix = waiting
                    remaining = pending.deadline - time.monotonic()
                    if remaining <= 0:
                        self._close_pending(connection)
                        continue
                    connection.settimeout(remaining)
                    del self._pending_connections[connection]
                    self._request_inputs[connection] = prefix, pending.deadline
                    return connection, pending.address
                except ssl.SSLWantWriteError:
                    pending.want_write = True
                except (ssl.SSLWantReadError, BlockingIOError):
                    pending.want_write = False
                except OSError:
                    self._close_pending(connection)
            if self.socket in readable:
                attempted = True
                self._accept_connection()
        raise OSError("dashboard is stopping")

    def tls_context_for(self, local_address: str, peer_address: str) -> Optional[ssl.SSLContext]:
        """The certificate set for one connection.

        A client on the test Wi-Fi gets the neutral certificate, whichever of the
        Pi's addresses it reached (the firewall lets it reach the dashboard port
        on the Ethernet address too), so it never reads the other names.
        """
        if self._tls_wifi_context is not None:
            for text in (peer_address, local_address):
                try:
                    if ipaddress.ip_address(str(text).split("%")[0]) in self._tls_wifi_network:
                        return self._tls_wifi_context
                except ValueError:
                    continue
        return self._tls_context

    def _read_waiting_request(self, connection: socket.socket,
                              pending: _PendingConnection) -> Optional[bytes]:
        """Read a waiting request while every ordinary worker is busy.

        Return the bytes to dispatch, or None while the request must keep waiting
        or after it was answered and closed here.
        """
        ended = False
        try:
            while len(pending.buffer) <= MAX_WAITING_REQUEST_BYTES:
                chunk = connection.recv(4096)
                if not chunk:
                    ended = True
                    break
                pending.buffer.extend(chunk)
        except (ssl.SSLWantReadError, BlockingIOError):
            pass
        if self.active_workers < MAX_DASHBOARD_WORKERS and pending.buffer:
            # An ordinary worker came free: carry on as usual with what was read.
            return bytes(pending.buffer)
        verdict = self._waiting_request_verdict(bytes(pending.buffer))
        if verdict is True:
            return bytes(pending.buffer)
        if verdict is False or len(pending.buffer) > MAX_WAITING_REQUEST_BYTES:
            self._pending_connections.pop(connection, None)
            self._refuse_request(connection, busy_body=True)
        elif ended:
            self._close_pending(connection)
        return None

    def _waiting_request_verdict(self, data: bytes) -> Optional[bool]:
        """Tell whether waiting input is a whole, authorized Panic request.

        None means the request is not complete yet. Only True may take a worker
        kept for Panic, so a stalled or unauthenticated client never holds one.
        """
        end = data.find(b"\r\n\r\n")
        if end < 0:
            return None
        request_line, _, header_block = data[:end].partition(b"\r\n")
        parts = request_line.split()
        if len(parts) != 3 or parts[0] != b"POST":
            return False
        try:
            path = urlsplit(parts[1].decode("ascii")).path
            headers = http.client.parse_headers(io.BytesIO(header_block + b"\r\n\r\n"))
        except (UnicodeDecodeError, ValueError, http.client.HTTPException):
            return False
        if path not in PRIORITY_WRITE_PATHS or headers.get("Transfer-Encoding") is not None:
            return False
        if not self.is_authorized(headers.get("Authorization")):
            return False
        try:
            length = int(headers.get("Content-Length", "0"))
        except ValueError:
            return False
        if length < 0 or length > MAX_WAITING_REQUEST_BYTES:
            return False
        return True if len(data) - (end + 4) >= length else None

    def _eviction_victim(self) -> Optional[socket.socket]:
        """Choose which waiting connection a newcomer replaces, if any.

        The peer with the most waiting connections gives one up, so a single host
        cannot push out everyone else. Within that peer a connection that has sent
        nothing goes before one that has (a TLS handshake under way, for example),
        oldest first. A connection with input ready to read is never chosen.
        """
        entries = list(self._pending_connections.items())
        try:
            ready = set(select.select([connection for connection, _ in entries], [], [], 0)[0])
        except (OSError, ValueError):
            ready = set()
        for connection, _ in entries:
            try:
                if isinstance(connection, ssl.SSLSocket) and connection.pending():
                    ready.add(connection)
            except (OSError, ValueError):
                pass

        def peer(pending: _PendingConnection) -> str:
            address = pending.address
            return str(address[0] if isinstance(address, tuple) else address)

        counts: Dict[str, int] = {}
        for _, pending in entries:
            counts[peer(pending)] = counts.get(peer(pending), 0) + 1
        # Peers by how many they hold. Ties keep the order of each peer's oldest entry.
        for name in sorted(counts, key=lambda name: -counts[name]):
            candidates = [(connection, pending) for connection, pending in entries
                          if peer(pending) == name and connection not in ready]
            if candidates:
                silent = [connection for connection, pending in candidates if not pending.progressed]
                return silent[0] if silent else candidates[0][0]
        return None

    def _note_eviction(self) -> None:
        self._evictions_unreported += 1
        now = time.monotonic()
        if now - self._eviction_warned_at >= EVICTION_WARNING_INTERVAL_SECONDS:
            LOGGER.warning(
                "dashboard replaced %d waiting dashboard connection(s) because the queue of %d was full",
                self._evictions_unreported, MAX_PENDING_DASHBOARD_CONNECTIONS)
            self._evictions_unreported = 0
            self._eviction_warned_at = now

    def _accept_connection(self) -> None:
        connection, address = self.socket.accept()
        while len(self._pending_connections) >= MAX_PENDING_DASHBOARD_CONNECTIONS:
            # A waiting connection has not said what it wants yet, so the Panic
            # reserve cannot protect it here. Refusing newcomers would let a few
            # silent clients lock Panic out until they expire. Instead the newest
            # connection replaces a waiting one (see _eviction_victim), and the
            # queue stays bounded.
            victim = self._eviction_victim()
            if victim is None:
                # Every waiting connection has input ready. They are read next.
                connection.close()
                return
            self._close_pending(victim)
            self._note_eviction()
        try:
            connection.setblocking(False)
            if self._tls_context is not None:
                context = self.tls_context_for(connection.getsockname()[0], address[0])
                connection = context.wrap_socket(
                    connection, server_side=True, do_handshake_on_connect=False)
            now = time.monotonic()
            self._pending_connections[connection] = _PendingConnection(
                address=address,
                deadline=now + (TLS_HANDSHAKE_TIMEOUT_SECONDS
                    if self._tls_context is not None else REQUEST_INPUT_TIMEOUT_SECONDS),
                handshake=self._tls_context is not None,
                expires_at=now + MAX_PENDING_CONNECTION_LIFETIME_SECONDS,
            )
        except OSError:
            connection.close()

    def _close_pending(self, connection: socket.socket) -> None:
        self._pending_connections.pop(connection, None)
        connection.close()

    def service_actions(self) -> None:
        if any(owner.startup_uncertain for owner in (
            self._request_workers, self.router_operations._workers, self.wifi_operations._workers,
        )):
            raise WorkersStillRunning("Dashboard worker startup is unresolved. Restart the service.")
        if self._pending_connections and not self._stop_connections.is_set():
            self._handle_request_noblock()

    def shutdown(self) -> None:
        self._stop_connections.set()
        super().shutdown()

    def shutdown_request(self, request: socket.socket) -> None:
        self._request_inputs.pop(request, None)
        with self._workers_lock:
            self._reserved_requests.discard(request)
        super().shutdown_request(request)

    def dashboard_access(self, peer: str, operation: str = "status", **values: Any) -> Dict[str, Any]:
        """Authorize the socket peer through the helper instead of request headers."""
        if operation in ("status", "emergency-status") and peer in ("127.0.0.1", "::1"):
            return {"ok": True, "network": "management", "allowed": True,
                    "can_register": False, "mac_address": None}
        if self.helper_client is None:
            raise HelperClientError("Device verification needs the Pi networking helper.")
        result = self._checked_access(operation, peer, values)
        if operation == "emergency-status":
            # Panic and Reset never wait for the routine protection retry.
            return self._emergency_access(peer, values, result)
        if self._protected_access_due(result):
            # The helper restarted or an earlier sync failed: reconcile, then answer again.
            state = self.sync_protected_access(blocking=False)
            if state.get("synced") and operation in ("status", "list"):
                result = self._checked_access(operation, peer, values)
        return result

    def _emergency_access(self, peer: str, values: Dict[str, Any],
                          result: Dict[str, Any]) -> Dict[str, Any]:
        """Decide Panic's device check when a restarted helper lacks the protection grants.

        Only the helper grants access. A refusal from a helper that has not received
        the protection grants is final only when protection could not grant this
        device. Otherwise the grants are synced once (never waiting for a sync that
        is already running) and the device is checked again. When that still cannot
        decide, the answer is helper-busy, which the page retries: never allowed.
        """
        if (result["allowed"] or result.get("protected_synced") is not False
                or not self._may_be_protected(result.get("mac_address"))):
            return result
        if self.sync_protected_access(blocking=False, emergency=True).get("synced"):
            result = self._checked_access("emergency-status", peer, values)
        if not result["allowed"] and result.get("protected_synced") is False:
            raise HelperClientError(
                "Device verification is waiting for the Pi helper to restore protected "
                "devices' access. Try again.", reason="helper-busy")
        return result

    def _may_be_protected(self, mac: Any) -> bool:
        """Whether saved protection might grant this device (unreadable counts as yes)."""
        if mac is None or getattr(self.helper_client, "dashboard_access_protected_sync", None) is None:
            return False
        try:
            with self._scoped_repository():
                targets, _ = self._protected_access_targets()
        except RepositoryBusy as error:
            # No snapshot within the emergency budget: undecided, so retried, never allowed.
            raise HelperClientError(
                "Device verification is waiting for the dashboard database. Try again.",
                reason="helper-busy") from error
        except (StorageError, sqlite3.Error):
            return True
        return any(item["mac_address"] == mac for item in targets)

    def bounded_repository(self, seconds: float):
        """The repository lock for a block, waited for at most `seconds` (RepositoryBusy)."""
        locked = getattr(self.repository, "locked_within", None)
        return nullcontext() if locked is None else locked(seconds)

    def _scoped_repository(self):
        """Inside a request deadline (Panic's device check), a bounded repository wait."""
        remaining = scope_remaining()
        if remaining is None:
            return nullcontext()
        return self.bounded_repository(min(EMERGENCY_REPOSITORY_WAIT_SECONDS, max(0.0, remaining)))

    def _checked_access(self, operation: str, peer: str, values: Dict[str, Any]) -> Dict[str, Any]:
        result = self.helper_client.dashboard_access(operation, peer=peer, **values)
        if (type(result.get("allowed")) is not bool or type(result.get("can_register")) is not bool
                or result.get("network") not in ("test", "management")):
            raise HelperClientError("Device verification returned an invalid response.")
        return result

    def _protected_access_due(self, result: Dict[str, Any]) -> bool:
        if getattr(self.helper_client, "dashboard_access_protected_sync", None) is None:
            return False
        waited = time.monotonic() - self._protected_access_attempted
        if result.get("protected_synced") is False:
            return waited >= PROTECTED_ACCESS_RESTART_RETRY_SECONDS
        return (self._protected_access_state.get("synced") is False
                and waited >= PROTECTED_ACCESS_RETRY_SECONDS)

    def protected_access_status(self) -> Dict[str, Any]:
        """The last protection sync outcome, safe to show to a signed-in owner."""
        state = self._protected_access_state
        return {**state, "not_eligible": [dict(item) for item in state["not_eligible"]]}

    def _protected_access_targets(self) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        targets: List[Dict[str, str]] = []
        not_eligible: List[Dict[str, str]] = []
        for device in self.repository.list_unaffected_devices():
            try:
                mac = normalize_access_mac(device.mac_address)
            except DashboardAccessError:
                # Listed, never dropped: the owner sees which protected device cannot sign in.
                not_eligible.append({"mac_address": device.mac_address,
                                     "reason": "This address cannot identify one Wi-Fi device."})
                continue
            try:
                name = validate_access_label(device.display_name)
            except DashboardAccessError:
                name = ""
            targets.append({"mac_address": mac, "name": name})
        return targets, not_eligible

    def sync_protected_access(self, *, blocking: bool = True, emergency: bool = False) -> Dict[str, Any]:
        """Replace the helper's protection grants with saved protection. Safe to repeat.

        Syncs run one at a time and each reads the protected list inside the lock, so an
        older request can never overwrite the result of a newer change. `emergency` is
        Panic's device check: the helper serves that sync from its Panic reserve without
        waiting for its access lock (review N1).
        """
        sync = ordinary = getattr(self.helper_client, "dashboard_access_protected_sync", None)
        urgent = getattr(self.helper_client, "dashboard_access_emergency_protected_sync", None)
        if emergency and ordinary is not None and urgent is not None:
            def sync(targets):
                try:
                    return urgent(targets)
                except HelperClientError as failure:
                    if failure.reason == "helper-busy":
                        raise
                    # An older helper does not know the emergency sync.
                    return ordinary(targets)
        if sync is None or not self._protected_access_lock.acquire(blocking=blocking):
            return self.protected_access_status()
        try:
            self._protected_access_attempted = time.monotonic()
            result = None
            error = "Saved protection could not be read."
            targets: Optional[List[Dict[str, str]]] = None
            not_eligible: List[Dict[str, str]] = []
            try:
                with self._scoped_repository():
                    targets, not_eligible = self._protected_access_targets()
            except (StorageError, sqlite3.Error):
                LOGGER.warning("saved protection could not be read for dashboard access")
            for attempt in range(PROTECTED_ACCESS_SYNC_ATTEMPTS if targets is not None else 0):
                try:
                    result = sync(targets)
                    break
                except HelperClientError as failure:
                    error = str(failure)
                    if failure.reason != "helper-busy" or attempt + 1 == PROTECTED_ACCESS_SYNC_ATTEMPTS:
                        break
                    time.sleep(0.05 * (attempt + 1))
            synced = (
                targets is not None and isinstance(result, dict)
                and result.get("protected_synced") is True
                and result.get("protected_count") == len(targets)
            )
            if result is not None and not synced:
                error = "The Pi helper did not confirm every protected device."
            state = {
                "synced": synced,
                "protected": len(targets or ()) + len(not_eligible),
                "granted": len(targets) if synced else 0,
                "not_eligible": not_eligible,
                "error": None if synced else error,
            }
            self._protected_access_state = state
            self._protected_access_key = (
                tuple((item["mac_address"], item["name"]) for item in targets) if synced else None
            )
            if synced:
                LOGGER.info("dashboard access confirmed for %d protected devices", len(targets))
            else:
                LOGGER.warning("dashboard access for protected devices is not confirmed")
            return self.protected_access_status()
        finally:
            self._protected_access_lock.release()

    def reconcile_protected_access(self) -> Dict[str, Any]:
        """Sync only when saved protection differs from the last confirmed sync."""
        if getattr(self.helper_client, "dashboard_access_protected_sync", None) is None:
            return self.protected_access_status()
        try:
            targets, _ = self._protected_access_targets()
        except (StorageError, sqlite3.Error):
            return self.sync_protected_access()
        key = tuple((item["mac_address"], item["name"]) for item in targets)
        if self._protected_access_state.get("synced") and key == self._protected_access_key:
            return self.protected_access_status()
        return self.sync_protected_access()

    def recover_protected_access(self) -> Dict[str, Any]:
        """The upgrade and boot step: grant every saved protected device again."""
        state = self.sync_protected_access()
        if state.get("synced") is False:
            LOGGER.warning("protected devices' dashboard access will be retried")
        return state

    def _with_protected_access(self, response: Tuple[int, str, bytes]) -> Tuple[int, str, bytes]:
        """Run the access half of a protection change and report its exact outcome."""
        status, content_type, body = response
        state = self.sync_protected_access()
        if state.get("synced") is None:
            return response
        try:
            data = json.loads(body)
        except ValueError:
            return response
        if not isinstance(data, dict):
            return response
        data["dashboard_access"] = state
        if not state["synced"]:
            note = ("Protection is saved but dashboard access for protected devices is not "
                    "confirmed. A device that is no longer protected may still open the "
                    "dashboard until Retry succeeds. Retry under Dashboard access.")
            data["warning"] = (str(data["warning"]) + " " + note) if data.get("warning") else note
        return status, content_type, _json_bytes(data)

    def _only_protection_route(self, peer: str, mac: str) -> Optional[Dict[str, Any]]:
        """The requester's verified identity when its access comes only from protecting mac."""
        identity = self.dashboard_access(peer)
        if (identity.get("network") == "test" and identity.get("mac_address") == mac
                and identity.get("grants") == ["protection"]):
            return identity
        return None

    def _keep_access_as_saved_grant(self, peer: str, mac: str,
                                    claim_id: Optional[str] = None) -> Tuple[bool, bool]:
        """Return whether access is kept and whether this call's claim created the grant."""
        name = next((item.display_name for item in self.repository.list_unaffected_devices()
                     if item.mac_address == mac), "")
        try:
            name = validate_access_label(name)
        except DashboardAccessError:
            name = ""
        values: Dict[str, Any] = {"name": name}
        if claim_id is not None:
            values["claim_id"] = claim_id
        data = self.dashboard_access(peer, "register-self", **values)
        kept = data.get("mac_address") == mac and data.get("allowed") is True
        return kept, kept and claim_id is not None and data.get("claimed") is True

    def remove_protection_for_peer(self, payload: Any, peer: str) -> Tuple[int, str, bytes]:
        """Never silently remove the requester's only verified control route."""
        path = "/api/unaffected-devices/delete"
        if not isinstance(payload, dict):
            return self.write_response_for(path, payload)
        payload = dict(payload)
        choice = payload.pop("dashboard_access", None)
        if choice not in (None, "keep", "remove"):
            return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                {"error": "Choose keep or remove for this device's dashboard access."})
        try:
            mac = normalize_exempt_mac(payload.get("mac_address"))
        except (TypeError, ValueError):
            return self.write_response_for(path, payload)
        own = self._only_protection_route(peer, mac)
        if own is not None and choice is None:
            return HTTPStatus.CONFLICT, "application/json", _json_bytes({
                "error": "This is the device you are using and it opens the dashboard only "
                         "because it is protected. Choose whether it keeps dashboard access "
                         "as a separate saved grant.",
                "access_choice_required": True,
            })
        kept = False
        if own is not None and choice == "keep":
            try:
                kept, _ = self._keep_access_as_saved_grant(peer, mac)
            except HelperClientError as error:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "Nothing changed. Dashboard access could not be saved for this "
                              "device: " + str(error)})
            if not kept:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                    {"error": "Nothing changed. The verified connection changed. Try again."})
        response = self.write_response_for(path, payload)
        if kept:
            response = self._note_access(response, "This device kept dashboard access as a "
                                                   "separate saved grant.")
        return response

    def import_for_peer(self, payload: Any, peer: str) -> Tuple[int, str, bytes]:
        """Import settings and keep the requester's access when protection was its only route."""
        return self._replace_settings_for_peer("/api/import", payload, peer)

    def history_restore_for_peer(self, payload: Any, peer: str) -> Tuple[int, str, bytes]:
        """Restore a saved version and keep the requester's only access route the same way."""
        return self._replace_settings_for_peer("/api/history/restore", payload, peer)

    def _replace_settings_for_peer(
        self, path: str, payload: Any, peer: str
    ) -> Tuple[int, str, bytes]:
        identity = self.dashboard_access(peer)
        if not (identity.get("network") == "test" and isinstance(identity.get("mac_address"), str)
                and identity.get("grants") == ["protection"]):
            return self.write_response_for(path, payload)
        mac = identity["mac_address"]
        # One entry once access is kept: this request's claim when it created the grant,
        # None when the grant already existed and so is never this request's to take back.
        granted: List[Optional[str]] = []

        def keep_route_first(settings: SavedSettings) -> Optional[Tuple[int, str, bytes]]:
            # Runs after validation and before the replacement is saved: the requester's only
            # control route becomes a saved grant first, or nothing changes at all.
            if any(item.mac_address == mac for item in settings.unaffected_devices):
                return None
            claim = uuid.uuid4().hex
            try:
                kept, created = self._keep_access_as_saved_grant(peer, mac, claim)
            except HelperClientError as error:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "Nothing changed. These settings no longer protect this device "
                              "and its dashboard access could not be saved: " + str(error)})
            if not kept:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                    {"error": "Nothing changed. These settings no longer protect this device "
                              "and its dashboard access could not be saved. Try again."})
            granted.append(claim if created else None)
            return None

        response = self.write_response_for(path, payload, before_replace=keep_route_first)
        if not granted:
            return response
        if any(item.mac_address == mac for item in self.repository.list_unaffected_devices()):
            # The replacement was refused after the grant was saved: put access back as it was.
            # Only the exact grant this request created, and only while nobody else saved it.
            if granted[0] is not None:
                try:
                    self.dashboard_access(peer, "remove-claimed", mac_address=mac,
                                          claim_id=granted[0])
                except HelperClientError:
                    LOGGER.warning("could not remove a saved grant for a refused settings change")
            return response
        # The grant stays even when the live apply of the saved replacement failed.
        self.reconcile_protected_access()
        return self._note_access(response, "This device kept dashboard access as a separate "
                                           "saved grant because the restored settings no longer "
                                           "protect it.")

    def _note_access(self, response: Tuple[int, str, bytes], message: str) -> Tuple[int, str, bytes]:
        status, content_type, body = response
        try:
            data = json.loads(body)
        except ValueError:
            return response
        if not isinstance(data, dict):
            return response
        data["dashboard_access"] = {**self.protected_access_status(), "message": message}
        return status, content_type, _json_bytes(data)

    def _station_devices(self) -> list:
        """Return the helper's current client list or nothing when it cannot be read."""

        if self.helper_client is None:
            return []
        try:
            stations = station_snapshot(self.helper_client)["devices"]
            return [
                station
                for station in stations
                if isinstance(station, dict)
                and all(
                    station.get(key) is None or isinstance(station.get(key), str)
                    for key in ("ipv4_address", "mac_address", "hostname")
                )
            ]
        except (AttributeError, HelperClientError, KeyError, OSError, TypeError, ValueError):
            LOGGER.warning("could not read client names from the station snapshot", exc_info=True)
            return []

    def _station_identities(
        self, stations: Optional[list] = None
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """Map each visible client address to its MAC and announced hostname."""

        if stations is None:
            stations = self._station_devices()
        return {
            station["ipv4_address"]: {
                "mac_address": station.get("mac_address"),
                "hostname": station.get("hostname"),
            }
            for station in stations
            if station.get("ipv4_address")
        }

    def _runtime_state_with_running(self, state: Optional[RuntimeState] = None) -> Dict[str, Any]:
        """Return the runtime state plus whether the scheduler keeps applying changes."""

        payload = _runtime_state_payload(
            self.repository.get_runtime_state() if state is None else state
        )
        payload["running"] = self.scheduler is not None and self.scheduler.is_active
        # The saved devices of a device-only start. Everything else stays reset.
        payload["device_only"] = sorted(self.repository.get_device_start_scope() or ())
        # The saved devices that run their own profile while Everyone runs for the rest.
        payload["everyone_exceptions"] = sorted(self.repository.get_everyone_exceptions())
        return payload

    def _applied_summary(self, plan: NetworkPlan) -> Dict[str, Any]:
        """Count what an apply sent so the dashboard can say what is now running."""

        device_ids = {rule.device_id for rule in plan.mark_rules}
        websites = self.repository.get_website_policy()
        general_name = None
        if plan.general_profile is not None:
            general = self.repository.get_profile(plan.general_profile.profile_id)
            general_name = general.name if general is not None else None
        return {
            "devices": len(plan.mark_rules),
            "groups": sum(
                1
                for group in self.repository.list_device_groups()
                if device_ids.intersection(group.device_ids)
            ),
            "service_lists": len(plan.service_list_rules),
            "protected": len(plan.protected_mac_addresses),
            "general_profile": general_name,
            # Start enforces the block list and the allow list and the MAC list too.
            "access_rules": (
                websites.mode == "allow_only"
                or bool(websites.blocked_domains)
                or self.repository.get_mac_policy().mode != "disabled"
            ),
        }

    def response_for(self, path: str, query: str = "") -> Tuple[int, str, bytes]:
        """Build the response body for a supported GET path."""

        access_response = read_access_api(self, path)
        if access_response is not None:
            return access_response
        workflow = read_workflow(self, path)
        if workflow is not None:
            return workflow
        trust = read_trust_api(self, path)
        if trust is not None:
            return trust
        if path == "/api/wifi-operation/request-id":
            identifier = issue_request_id()
            return HTTPStatus.OK, "application/json", _json_bytes({"request_id": identifier})
        if path == "/api/wifi-operation":
            self._record_wifi_outcome()
            identifier = parse_qs(query).get("id", [None])[0]
            status, data = self.wifi_operations.last(identifier)
            return status, "application/json", _json_bytes(data)
        if path == "/":
            return HTTPStatus.OK, "text/html; charset=utf-8", dashboard_page_bytes(self.dashboard_page_html)
        if path == "/favicon.ico":
            return HTTPStatus.OK, "image/x-icon", NEUTRAL_ICON
        if path == "/api/brand":
            return HTTPStatus.OK, "application/json", _json_bytes({
                "name": PRODUCT_NAME,
                "command": PRODUCT_COMMAND,
                "default_ssid": DEFAULT_ACCESS_POINT_SSID,
            })
        if path == "/api/health":
            helper_reachable = False
            if self.helper_client is not None:
                try:
                    helper_reachable = self.helper_client.ping().get("ok") is True
                except HelperClientError:
                    LOGGER.warning("network helper health check failed")
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "status": "ok" if self.helper_client is None or helper_reachable else "degraded",
                    "mode": "hardware" if self.helper_client is not None else "configuration-only",
                    "helper_configured": self.helper_client is not None,
                    "helper_reachable": helper_reachable,
                }
            )
        if path == "/api/system-health":
            # The public health check carries only the mode like the Pi base. The
            # version and free disk and temperature and uptime need the admin token.
            return HTTPStatus.OK, "application/json", _json_bytes({"system": machine_health()})
        if path == "/api/profiles":
            profiles = [_profile_payload(profile) for profile in self.repository.list_profiles()]
            return HTTPStatus.OK, "application/json", _json_bytes({"profiles": profiles})
        if path == "/api/pattern-history":
            return HTTPStatus.OK, "application/json", _json_bytes({
                "events": self.repository.list_pattern_events(now=int(time.time())),
                "limit": 100, "retention_days": 7,
            })
        if path == "/api/general-profile":
            return HTTPStatus.OK, "application/json", _json_bytes(self._general_profile_status())
        if path == "/api/devices":
            labels_by_mac = self.repository.list_connected_device_labels()
            states = self._device_protection_states()
            devices = [
                _device_payload(device, labels_by_mac=labels_by_mac, state=states.get(device.id))
                for device in self.repository.list_devices()
            ]
            return HTTPStatus.OK, "application/json", _json_bytes({"devices": devices})
        if path == "/api/ad-blocking":
            return HTTPStatus.OK, "application/json", _json_bytes(self._ad_blocking_payload())
        if path == "/api/unaffected-devices":
            blocking = set(self.repository.list_ad_blocking_devices())
            return HTTPStatus.OK, "application/json", _json_bytes({
                "devices": [{**asdict(device), "ad_blocking": device.mac_address in blocking}
                            for device in self.repository.list_unaffected_devices()],
                "limit": 256,
                "ad_blocking": self._ad_blocking_payload(),
            })
        if path == "/api/groups":
            labels_by_mac = self.repository.list_connected_device_labels()
            groups = [
                _device_group_payload(
                    group, repository=self.repository, labels_by_mac=labels_by_mac
                )
                for group in self.repository.list_device_groups()
            ]
            return HTTPStatus.OK, "application/json", _json_bytes({"groups": groups})
        if path == "/api/schedules":
            schedules = [_schedule_payload(schedule) for schedule in self.repository.list_schedules()]
            return HTTPStatus.OK, "application/json", _json_bytes({
                "schedules": schedules,
                "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "time_zone": time.strftime("%Z"),
            })
        if path == "/api/activity":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"events": list(self.repository.list_activity())}
            )
        if path == "/api/activity/latest":
            # The dashboard polls this and fetches the whole log only when it changed.
            return HTTPStatus.OK, "application/json", _json_bytes(
                self.repository.activity_summary()
            )
        if path == "/api/scenarios":
            runs = [
                _scenario_run_payload(run, scenario, position, repository=self.repository)
                for run, scenario, position in active_scenario_runs(
                    self.repository, now=time.time()
                )
            ]
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "scenarios": [
                        _scenario_payload(scenario) for scenario in self.repository.list_scenarios()
                    ],
                    "runs": runs,
                }
            )
        if path == "/api/timers":
            now = int(time.time())
            timers = []
            labels_by_mac = self.repository.list_connected_device_labels()
            for timer in self.repository.list_active_timers(now=now):
                device = self.repository.get_device(timer.device_id)
                profile = self.repository.get_profile(timer.profile_id)
                timers.append(
                    {
                        **_timer_payload(timer),
                        "device_name": _device_title(device, labels_by_mac, timer.device_id),
                        "profile_name": profile.name if profile is not None else timer.profile_id,
                        "remaining_seconds": max(0, timer.expires_at - now),
                    }
                )
            return HTTPStatus.OK, "application/json", _json_bytes({"timers": timers})
        if path == "/api/progressions":
            now = int(time.time())
            device_counters = {}
            scheduler_counters = {}
            if self.scheduler is not None:
                scheduler_counters = {
                    device.id: self.scheduler.device_traffic_bytes(device_id=device.id)
                    for device in self.repository.list_devices()
                }
            elif self.helper_client is not None:
                try:
                    device_counters = self.helper_client.traffic_counters().get("devices", {})
                except (HelperClientError, AttributeError, TypeError):
                    LOGGER.warning("could not read progression traffic counters")
            progressions = [
                _progression_payload(
                    progression,
                    repository=self.repository,
                    now=now,
                    traffic_bytes=scheduler_counters.get(
                        progression.device_id,
                        device_counters.get(progression.device_id, 0),
                    ),
                )
                for progression in self.repository.list_active_progressions(now=now)
            ]
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"progressions": progressions}
            )
        if path == "/api/data-limits":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "data_limits": [
                        _data_limit_payload(data_limit, repository=self.repository)
                        for data_limit in self.repository.list_data_limits()
                    ]
                }
            )
        if path == "/api/group-data-limits":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "group_data_limits": [
                        _group_data_limit_payload(data_limit, repository=self.repository)
                        for data_limit in self.repository.list_group_data_limits()
                    ]
                }
            )
        if path == "/api/service-quotas":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "service_quotas": [
                        _service_quota_payload(quota, repository=self.repository)
                        for quota in self.repository.list_service_quotas()
                    ]
                }
            )
        if path == "/api/service-assignments":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "service_assignments": [
                        _service_assignment_payload(assignment, repository=self.repository)
                        for assignment in self.repository.list_service_assignments()
                    ]
                }
            )
        if path == "/api/service-lists":
            # Only the plan on the network says whether a list is in an outage now.
            applied = self.scheduler.applied_plan() if self.scheduler is not None else None
            outages = {
                rule.list_id: rule.outage_active
                for rule in (applied.service_list_rules if applied is not None else ())
            }
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "service_lists": [
                        _service_list_payload(
                            service_list,
                            repository=self.repository,
                            outage_now=outages.get(service_list.id, False),
                        )
                        for service_list in self.repository.list_service_lists()
                    ],
                    "catalogue": _service_catalogue_payload(),
                }
            )
        if path == "/api/time-quotas":
            now = int(time.time())
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "time_quotas": [
                        _time_quota_payload(quota, repository=self.repository, now=now)
                        for quota in self.repository.list_time_quotas()
                    ]
                }
            )
        if path == "/api/policies":
            return HTTPStatus.OK, "application/json", _json_bytes(
                _policies_payload(self.repository)
            )
        if path == "/api/everyone-else":
            return HTTPStatus.OK, "application/json", _json_bytes(
                _everyone_else_payload(self.repository)
            )
        if path == "/api/router":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "model": "Vodafone TG3442DE",
                    "automatic_wifi_control": True,
                    "wifi_control": "tg3442de-local-web",
                    "message": (
                        "Router admin credentials are used for one authenticated request and are not saved. "
                        "Keep the Pi connected by Ethernet before changing router Wi-Fi."
                    ),
                }
            )
        if path == "/api/uplink":
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "available": False,
                        "uplink": None,
                        "message": "upstream connection information is unavailable in configuration-only mode",
                    }
                )
            try:
                uplink = self.helper_client.uplink_info()
            except HelperClientError:
                LOGGER.exception("could not inspect the upstream connection")
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "could not inspect the upstream connection"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "available": True,
                    "uplink": uplink,
                    "router": {
                        "model": "Vodafone TG3442DE",
                        "settings_url": "http://192.168.0.1",
                        "wifi_control": "tg3442de-local-web",
                    },
                }
            )
        if path == "/api/connected-device-labels":
            labels = self.repository.list_connected_device_labels()
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "labels": [
                        {
                            "mac_address": mac_address,
                            "display_name": label["display_name"],
                            "device_type": label["device_type"],
                        }
                        for mac_address, label in labels.items()
                    ]
                }
            )
        if path == "/api/connected-devices":
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "available": False,
                        "devices": [],
                        "message": "connected-client information is unavailable in configuration-only mode",
                    }
                )
            try:
                snapshot = self.helper_client.connected_device_snapshot()
                devices = snapshot["devices"]
            except HelperClientError:
                LOGGER.exception("could not inspect connected clients")
                return (
                    HTTPStatus.BAD_GATEWAY,
                    "application/json",
                    _json_bytes({"error": "could not inspect connected clients"}),
                )
            assignments_by_address = {
                str(device.ipv4_address): device for device in self.repository.list_devices()
            }
            labels_by_mac = self.repository.list_connected_device_labels()
            protected = {item.mac_address for item in self.repository.list_unaffected_devices()}
            unaffected_macs = frozenset(protected)
            general_status = self._general_profile_status()
            groups_by_device = {
                device_id: group
                for group in self.repository.list_device_groups()
                for device_id in group.device_ids
            }
            devices = [
                _connected_device_payload(
                    device,
                    assignments_by_address=assignments_by_address,
                    labels_by_mac=labels_by_mac,
                    unaffected_macs=unaffected_macs,
                )
                for device in devices
            ]
            states = device_protection_states(
                self.repository, snapshot if protected else None
            )
            beside_everyone = self.repository.get_everyone_exceptions()
            for client in devices:
                group = groups_by_device.get(client["assigned_device_id"])
                client["unaffected"] = client["mac_address"] in protected
                client["assigned_effective_state"] = states.get(
                    client["assigned_device_id"], {}
                ).get("effective_state")
                client["group_id"] = group.id if group else None
                client["group_name"] = group.name if group else None
                client["group_profile_id"] = group.profile_id if group else None
                if general_status["active"] and not client["unaffected"]:
                    if client["assigned_device_id"] in beside_everyone:
                        # This device left Everyone and runs the profile saved for it.
                        client["beside_everyone"] = True
                    else:
                        client["general_profile_name"] = general_status["profile"]["name"]
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes(
                    {
                        "available": True,
                        "association_status_available": snapshot["association_status_available"],
                        "devices": devices,
                        # The list polls every 5 seconds, so its labels follow schedules.
                        "everyone_else_profile_id": _everyone_else_now(self.repository)[0],
                    }
                ),
            )
        if path == "/api/observations":
            observations = self.repository.list_domain_observations()
            assignments_by_address = {
                str(device.ipv4_address): device for device in self.repository.list_devices()
            }
            station_devices = self._station_devices()
            blocked = protected_addresses(self.repository, station_devices)
            protected = set(protected_macs(self.repository))
            grouped = {}
            for observation in observations:
                address = str(observation.ipv4_address)
                if observation_is_protected(observation, protected, blocked):
                    continue
                grouped.setdefault(address, []).append(observation)
            stations = self._station_identities(station_devices)
            labels_by_mac = self.repository.list_connected_device_labels()
            blocked_names = frozenset(self.repository.get_website_policy().blocked_domains)
            devices = [
                {
                    "ipv4_address": address,
                    "display_name": _observation_display_name(
                        stations.get(address, {}),
                        assignments_by_address.get(address),
                        labels_by_mac,
                    ),
                    "hostname": stations.get(address, {}).get("hostname"),
                    "assigned_device_id": (
                        assignments_by_address[address].id
                        if address in assignments_by_address
                        else None
                    ),
                    "assigned_device_name": (
                        assignments_by_address[address].name
                        if address in assignments_by_address
                        else None
                    ),
                    "assigned_profile_id": (
                        assignments_by_address[address].profile_id
                        if address in assignments_by_address
                        else None
                    ),
                    "domains": _labelled_domain_payloads(rows, blocked_names),
                }
                for address, rows in sorted(grouped.items())
            ]
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes(
                    {
                        "available": self.helper_client is not None,
                        "devices": devices,
                        "total_domains": sum(len(rows) for rows in grouped.values()),
                        "list_cleared": _domain_list_cleared_payload(self.repository),
                        "message": (
                            None
                            if self.helper_client is not None
                            else "traffic discovery is unavailable in configuration-only mode"
                        ),
                    }
                ),
            )
        if path == "/api/plan":
            plan = (
                self.scheduler.build_plan(repository=self.repository)
                if self.scheduler is not None
                else compile_stored_plan(
                    repository=self.repository,
                    connected_snapshot=station_snapshot(self.helper_client)
                    if self.helper_client
                    else None,
                )
            )
            return HTTPStatus.OK, "application/json", _json_bytes({"plan": plan_to_payload(plan)})
        if path == "/api/conditions":
            # Read the applied plan without the apply lock, so this read never waits for an apply.
            if self.scheduler is None:
                plan, sources = None, None
            elif hasattr(self.scheduler, "applied_snapshot"):
                plan, sources = self.scheduler.applied_snapshot()
            else:
                plan, sources = self.scheduler.applied_plan(), self.scheduler.applied_target_states()
            applied = plan is not None

            def described_condition(state, target_id):
                payload = {"condition": state.condition, "reasons": list(state.reasons)}
                target = (sources or {}).get(target_id)
                if applied and target is not None:
                    payload["source"] = asdict(target.source)
                    payload["next_change_at"] = (
                        datetime.fromtimestamp(target.next_change_at).astimezone().isoformat()
                        if target.next_change_at is not None else None
                    )
                return payload
            if plan is None:
                # Nothing runs on the network, so describe the saved settings.
                # The scheduler's runtime plan would advance its rerolls.
                plan = compile_stored_plan(
                    repository=self.repository, connected_snapshot=self._protection_snapshot()
                )
            everyone_else = everyone_else_condition(plan)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "applied": applied,
                    "everyone_else": (
                        None
                        if everyone_else is None
                        else described_condition(everyone_else, EVERYONE_ELSE_QUEUE_ID)
                    ),
                    "devices": {
                        device_id: described_condition(state, device_id)
                        for device_id, state in device_conditions(
                            plan,
                            mac_addresses={
                                device.id: device.mac_address
                                for device in self.repository.list_devices()
                            },
                        ).items()
                    },
                }
            )
        if path == "/api/state":
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes({"state": self._runtime_state_with_running()}),
            )
        if path == "/api/action-history":
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes({"actions": list(self.repository.list_recent_actions())}),
            )
        if path == "/api/random-website-analytics":
            try:
                parameters = parse_qs(
                    query,
                    keep_blank_values=True,
                    max_num_fields=4,
                )
            except ValueError:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "too many analytics filters"}
                )
            day_values = parameters.get("days", ["30"])
            device_values = parameters.get("device_id", [""])
            if len(day_values) != 1 or len(device_values) != 1:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "provide one date range and one device filter"}
                )
            try:
                days = int(day_values[0])
            except (TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "days must be between 1 and 30"}
                )
            if not 1 <= days <= 30:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "days must be between 1 and 30"}
                )
            device_id = device_values[0] or None
            if device_id is not None and len(device_id) > 64:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "device id is too long"}
                )
            report = self.repository.list_random_website_analytics(
                days=days,
                device_id=device_id,
            )
            labels_by_mac = self.repository.list_connected_device_labels()
            devices_by_id = {device.id: device for device in self.repository.list_devices()}
            for row in report["rows"]:
                row["display_name"] = _device_display_name(
                    devices_by_id.get(row["device_id"]), labels_by_mac
                )
            return HTTPStatus.OK, "application/json", _json_bytes(report)
        if path == "/api/backup":
            return HTTPStatus.OK, "application/vnd.sqlite3", self.repository.backup_bytes()
        if path == "/api/export":
            return HTTPStatus.OK, "application/json", _json_bytes(
                settings_to_payload(
                    self.repository.saved_settings(), exported_at=int(time.time())
                )
            )
        if path == "/api/traffic-settings":
            return HTTPStatus.OK, "application/json", _json_bytes(
                self.repository.get_traffic_rate_settings()
            )
        if path == "/api/gateway-persistence":
            saved_mode = self.repository.get_persist_profiles_after_gateway_outage()
            state = {
                "persistence_enabled": saved_mode,
                "mode_enabled": saved_mode,
                "gateway_state": "unknown",
                "outage_stop_latched": False,
                "recovery_pending": False,
                "state_error": False,
                "helper_available": self.helper_client is not None,
                "helper_synced": self.helper_client is None,
            }
            if self.helper_client is not None:
                try:
                    helper_state = self.helper_client.gateway_status()
                    # Reading must not change network state. Only a restarted
                    # helper, which has no outage to act on yet, gets the mode.
                    if (
                        helper_state.get("mode_initialized") is False
                        and not helper_state["state_error"]
                    ):
                        helper_state = self.helper_client.set_gateway_persistence(
                            enabled=saved_mode
                        )
                    state.update(helper_state)
                    state["helper_synced"] = (
                        state["mode_enabled"] == saved_mode
                        and state["state_error"] is False
                    )
                except HelperClientError:
                    state["state_error"] = True
                    state["helper_synced"] = False
            return HTTPStatus.OK, "application/json", _json_bytes(state)
        if path == "/api/traffic":
            isp_limits = self.repository.get_traffic_rate_settings()
            reset = self.repository.get_runtime_state().observed_state == "reset"
            if self.helper_client is None or reset:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "available": False,
                        "message": (
                            "Live traffic needs hardware mode."
                            if self.helper_client is None
                            else "Network is reset. Choose Start to start traffic measurements."
                        ),
                        "devices": {},
                        "isp_limits": isp_limits,
                        "profile_limits_available": False,
                        "profile_limits": {},
                    }
                )
            try:
                counters = self.helper_client.traffic_counters()
            except HelperClientError:
                LOGGER.exception("could not read live traffic counters")
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "live traffic counters are unavailable"}
                )
            directions = counters.get("directions")
            device_counters = directions.get("devices") if isinstance(directions, dict) else None
            if not isinstance(device_counters, dict):
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "live traffic directions are unavailable"}
                )
            queues = counters.get("queues")
            device_queues = queues.get("devices") if isinstance(queues, dict) else None
            everyone_else = counters.get("everyone_else")
            everyone_else_queue = queues.get("everyone_else") if isinstance(queues, dict) else None
            profile_limits = (
                self.scheduler.applied_queue_bandwidth_limits()
                if self.scheduler is not None
                else None
            )
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "available": True,
                    "sampled_at": time.time(),
                    "devices": device_counters,
                    "queues": device_queues if isinstance(device_queues, dict) else {},
                    "everyone_else": everyone_else if isinstance(everyone_else, dict) else None,
                    "everyone_else_queue": (
                        everyone_else_queue if isinstance(everyone_else_queue, dict) else None
                    ),
                    "isp_limits": isp_limits,
                    "profile_limits_available": profile_limits is not None,
                    "profile_limits": profile_limits or {},
                    "general_profile_limits": (
                        (profile_limits or {}).get(GENERAL_QUEUE_ID)
                        if self.repository.get_general_profile() is not None else None
                    ),
                }
            )
        if path == "/api/impact":
            return HTTPStatus.OK, "application/json", _json_bytes(self._impact_status())
        if path == "/api/diagnostics":
            runtime = self.repository.get_runtime_state()
            diagnostics = {
                "checked_at": int(time.time()),
                "runtime": self._runtime_state_with_running(runtime),
                "helper_available": self.helper_client is not None,
                "connected_clients": None,
                "traffic_counters": None,
                "traffic_counters_message": (
                    "Network is reset. Choose Start to start traffic measurements."
                    if runtime.observed_state == "reset"
                    else None
                ),
                "warnings": [],
                "dashboard_name": dashboard_name_report(self),
            }
            if self.helper_client is not None:
                try:
                    diagnostics["connected_clients"] = len(
                        self.helper_client.connected_devices()
                    )
                except (HelperClientError, AttributeError, TypeError):
                    diagnostics["warnings"].append("connected-client scan failed")
                if runtime.observed_state != "reset":
                    try:
                        diagnostics["traffic_counters"] = self.helper_client.traffic_counters()
                    except (HelperClientError, AttributeError, TypeError):
                        diagnostics["warnings"].append("traffic counters are unavailable")
            return HTTPStatus.OK, "application/json", _json_bytes(diagnostics)
        return HTTPStatus.NOT_FOUND, "application/json", _json_bytes({"error": "not found"})

    def write_response_for(self, path: str, payload: Dict[str, Any], *,
                           before_replace=None) -> Tuple[int, str, bytes]:
        """Handle one write. before_replace(settings) runs on a validated import or restore
        just before it is saved and may refuse it by returning a response."""
        if path in RESET_REQUEST_PATHS:
            # A device Apply that arrives while this runs, or waits behind it, starts nothing.
            self.note_reset_request()
            try:
                return self._tracked_write_response_for(path, payload, before_replace)
            finally:
                self.note_reset_finished()
        return self._tracked_write_response_for(path, payload, before_replace)

    def _tracked_write_response_for(self, path: str, payload: Dict[str, Any],
                                    before_replace=None) -> Tuple[int, str, bytes]:
        if path == "/api/profiles/ranges":
            return self._write_response_for(path, payload)
        control_intent = None
        live_access = path in ACCESS_WRITES and path not in (
            "/api/normal-access", "/api/site-blocks", "/api/site-blocks/delete"
        )
        live_global = path in ("/api/observations/add-global-block", "/api/observations/remove-global-block")
        live_general = path in ("/api/general-profile", "/api/general-profile/stop")
        live_ads = path == "/api/ad-blocking"
        device_apply = path == "/api/connected-devices/apply-profile"
        resets_at_entry = (self._reset_requests, self._resets_pending)
        if path == "/api/apply" or live_access or live_global or live_general or live_ads or device_apply:
            generation = getattr(self.scheduler, "apply_generation", getattr(self.scheduler, "halt_count", 0))
            refused, revision = self._capture_control_intent(generation)
            if refused is not None:
                return refused
            control_intent = (generation, revision)
        # Panic skips the write lock so it never queues behind an apply in flight.
        operation = (
            self.scheduler.exclusive()
            if self.scheduler is not None and path not in ACTION_PATHS and path not in PRIORITY_WRITE_PATHS
            else nullcontext()
        )
        # Panic waits for the repository only boundedly and skips its history
        # checkpoint rather than wait for a backup (review N3).
        panic = path in PRIORITY_WRITE_PATHS

        def bookkeeping():
            return self.bounded_repository(PANIC_REPOSITORY_WAIT_SECONDS) if panic else nullcontext()

        with operation:
            try:
                with bookkeeping():
                    before = self.repository.get_runtime_state().configuration_version
            except RepositoryBusy:
                LOGGER.warning("Panic runs without its history checkpoint: the database is busy")
                before = None
            try:
                access_response = (
                    write_access_api(self, path, payload, control_intent=control_intent)
                    if live_access else write_access_api(self, path, payload)
                )
                if access_response is not None:
                    return access_response
                workflow = write_workflow(self, path, payload, _settings_from_payload,
                                          before_replace=before_replace)
                if workflow is not None:
                    return workflow
                if device_apply:
                    return self._apply_profile_to_connected_device(
                        payload, control_intent=control_intent, resets_at_entry=resets_at_entry)
                return (self._write_response_for(path, payload, control_intent=control_intent)
                        if path == "/api/apply" or live_global or live_general or live_ads
                        else self._write_response_for(path, payload, before_replace=before_replace))
            finally:
                try:
                    with bookkeeping():
                        if (before is not None and
                                self.repository.get_runtime_state().configuration_version != before):
                            WorkflowStore(self.repository).checkpoint(
                                path.removeprefix("/api/").replace("/", " "))
                except RepositoryBusy:
                    LOGGER.warning("Panic skipped its history checkpoint: the database is busy")

    def _write_response_for(self, path: str, payload: Dict[str, Any], *, control_intent=None,
                            before_replace=None) -> Tuple[int, str, bytes]:
        """Handle one supported configuration or network action request."""

        if path == "/api/profiles/ranges":
            try:
                ranges = {
                    direction: impairment_range_preview(_impairment_from_payload(payload.get(direction)))
                    for direction in ("upload", "download")
                }
            except (TypeError, ValueError) as error:
                return _bad_request("invalid value range", error)
            return HTTPStatus.OK, "application/json", _json_bytes({"ranges": ranges})

        if path == "/api/gateway-persistence":
            enabled = payload.get("persistence_enabled")
            if not isinstance(enabled, bool):
                return _bad_request(
                    "invalid gateway persistence setting",
                    ValueError("persistence_enabled must be true or false"),
                )
            try:
                version = self.repository.set_persist_profiles_after_gateway_outage(
                    enabled,
                    expected_version=payload.get("version"),
                )
            except DeviceConflictError as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            except StorageError as error:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": str(error)})
            helper_status = None
            helper_synced = self.helper_client is None
            if self.helper_client is not None:
                try:
                    helper_status = self.helper_client.set_gateway_persistence(enabled=enabled)
                    helper_synced = helper_status.get("state_error") is False
                except HelperClientError:
                    LOGGER.exception("saved gateway persistence setting did not reach the helper")
            response = {
                "persistence_enabled": enabled,
                "version": version,
                "saved": True,
                "helper_synced": helper_synced,
                "message": (
                    "Saved for use when hardware mode runs."
                    if self.helper_client is None
                    else (
                        "Saved and applied to the networking helper."
                        if helper_synced
                        else "Saved, but the networking helper could not confirm its outage state. Check its service logs."
                    )
                ),
                "helper_available": self.helper_client is not None,
            }
            if helper_status is not None:
                response.update(helper_status)
            else:
                response.update(
                    {
                        "gateway_state": "unknown",
                        "outage_stop_latched": False,
                        "recovery_pending": False,
                        "state_error": self.helper_client is not None,
                    }
                )
            return HTTPStatus.OK, "application/json", _json_bytes(response)

        if path == "/api/import":
            try:
                settings = keep_sections_missing_from(
                    payload, _settings_from_payload(payload), self.repository.saved_settings()
                )
                check_settings(
                    settings,
                    max_traffic_classes=MAX_SHAPED_MARKS_PER_PLAN,
                    max_service_domains=MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN,
                )
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid settings import", error)
            refused = before_replace(settings) if before_replace is not None else None
            if refused is not None:
                return refused
            self.repository.replace_settings(
                self._with_counter_baselines(settings),
                observation_addresses=self.newly_protected_station_addresses(settings),
            )
            self._sync_gateway_persistence()
            return self._apply_saved_change(
                "imported",
                {
                    "profiles": len(settings.profiles),
                    "devices": len(settings.devices),
                    "schedules": len(settings.schedules),
                },
            )

        if path == "/api/profiles/delete":
            try:
                profile_id = _required_text(payload, "id")
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid profile delete request", error)
            if self.repository.get_profile(profile_id) is None:
                return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                    {"error": "profile does not exist"}
                )
            references = self.repository.profile_references(profile_id, now=int(time.time()))
            if references:
                # Deleting would silently remove the rules that use the profile.
                return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                    {
                        "error": "profile is in use by " + ", ".join(references),
                        "references": list(references),
                    }
                )
            self.repository.delete_profile(profile_id)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"profile": {"id": profile_id, "deleted": True}}
            )

        deletes = {
            "/api/devices/delete": ("id", "device", self.repository.delete_device),
            "/api/schedules/delete": ("id", "schedule", self.repository.delete_schedule),
            "/api/scenarios/delete": ("id", "scenario", self.repository.delete_scenario),
            "/api/data-limits/delete": (
                "device_id",
                "data_limit",
                self.repository.delete_data_limit,
            ),
            "/api/group-data-limits/delete": (
                "group_id",
                "group_data_limit",
                self.repository.delete_group_data_limit,
            ),
            "/api/service-quotas/delete": (
                "id",
                "service_quota",
                self.repository.delete_service_quota,
            ),
            "/api/time-quotas/delete": ("id", "time_quota", self.repository.delete_time_quota),
        }
        if path in deletes:
            key, kind, delete = deletes[path]
            label = kind.replace("_", " ")
            try:
                identifier = _required_text(payload, key)
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request(f"invalid {label} delete request", error)
            if not delete(identifier):
                return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                    {"error": f"{label} does not exist"}
                )
            return self._apply_saved_change(kind, {key: identifier, "deleted": True})
        if path == "/api/traffic-settings":
            try:
                if "upload_kbit" not in payload or "download_kbit" not in payload:
                    raise TrafficRateSettingsError("both rate fields are required")
                settings = self.repository.save_traffic_rate_settings(
                    upload_kbit=payload["upload_kbit"],
                    download_kbit=payload["download_kbit"],
                )
            except TrafficRateSettingsError:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid ISP rate references"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes(settings)

        if path == "/api/connected-device-labels":
            try:
                mac_address = normalize_device_mac(payload.get("mac_address"))
                display_name = normalize_device_display_name(payload.get("display_name"))
                device_type = normalize_device_type(payload.get("device_type"))
            except DeviceLabelError:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "connected-device details are invalid"}
                )
            self.repository.save_connected_device_label(
                mac_address=mac_address,
                display_name=display_name,
                device_type=device_type,
            )
            return HTTPStatus.OK, "application/json", _json_bytes({"saved": True})

        if path == "/api/connected-device-labels/delete":
            try:
                mac_address = normalize_device_mac(payload.get("mac_address"))
            except DeviceLabelError:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "connected-device address is invalid"}
                )
            self.repository.delete_connected_device_label(mac_address=mac_address)
            return HTTPStatus.OK, "application/json", _json_bytes({"deleted": True})

        if path in ("/api/homepage/pairing-code", "/api/homepage/revoke"):
            if self.homepage_store is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes({"error": "Trusted homepage pairing is not enabled"})
            try:
                if path.endswith("revoke") and payload == {}:
                    self.homepage_store.revoke()
                    result = {"revoked": True}
                elif path.endswith("pairing-code") and set(payload) == {"extension_id", "pages"}:
                    result = self.homepage_store.create(payload["extension_id"], payload["pages"])
                else:
                    raise ValueError("invalid pairing request")
                return HTTPStatus.OK, "application/json", _json_bytes(result)
            except (ValueError, RuntimeError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": "Pairing unavailable. Check the extension ID and controlled HTTPS pages."})

        if path == "/api/profiles":
            try:
                profile = _profile_from_payload(payload)
            except ValidationError as error:
                if str(error) == "Clear the saved seed before choosing a fresh pattern":
                    return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": str(error)})
                return _bad_request("invalid profile configuration", error)
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid profile configuration", error)
            self.repository.save_profile(profile)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"profile": _profile_payload(profile)}
            )

        if path == "/api/devices":
            try:
                device = _device_from_payload(payload)
                expected = (
                    _device_from_payload(payload["expected"])
                    if "expected" in payload else None
                )
                if expected is not None and expected.id != device.id:
                    raise ValueError("the original device ID must match")
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid device configuration", error)
            # A protected saved device stays editable. Its assignment is dormant while
            # the protection lasts and comes back when the protection is removed.
            if self.repository.get_device(device.id) is None:
                capacity_error = self._capacity_error(adding=1)
                if capacity_error is not None:
                    return capacity_error
            try:
                self.repository.save_device(device, expected=expected)
            except DeviceConflictError as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            labels_by_mac = self.repository.list_connected_device_labels()
            state = self._device_protection_states().get(device.id)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"device": _device_payload(device, labels_by_mac=labels_by_mac, state=state)}
            )

        if path in ("/api/general-profile", "/api/general-profile/stop"):
            return self._change_general_profile(
                payload, stop=path.endswith("/stop"), control_intent=control_intent)

        if path == "/api/ad-blocking":
            return self._change_ad_blocking(payload, control_intent=control_intent)
        if path in ("/api/unaffected-devices", "/api/unaffected-devices/delete"):
            return self._change_unaffected_device(payload, remove=path.endswith("/delete"))

        if path in ("/api/groups", "/api/connected-devices/group"):
            return self._save_connected_group(payload, existing=path.endswith("/group"))

        if path == "/api/groups/delete":
            try:
                group_id = payload["id"]
                if not isinstance(group_id, str) or not group_id:
                    raise ValueError("group id is invalid")
                if not self.repository.delete_device_group(group_id):
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "device group does not exist"}
                    )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid device group delete request", error)
            return self._apply_saved_change("group", {"id": group_id, "deleted": True})

        if path == "/api/schedules/preview":
            try:
                schedule = _schedule_from_payload(payload)
                return HTTPStatus.OK, "application/json", _json_bytes({
                    "overlaps": overlapping_schedules(schedule, self.repository.list_schedules()),
                    "message": "For the same target, the active schedule with the first ID alphabetically wins.",
                })
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid schedule configuration", error)

        if path == "/api/schedules":
            try:
                schedule = _schedule_from_payload(payload)
                if (
                    schedule.device_id is not None
                    and self.repository.get_device(schedule.device_id) is None
                ):
                    raise ValueError("device does not exist")
                if (
                    schedule.group_id is not None
                    and self.repository.get_device_group(schedule.group_id) is None
                ):
                    raise ValueError("group does not exist")
                if self.repository.get_profile(schedule.profile_id) is None:
                    raise ValueError("profile does not exist")
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid schedule configuration", error)
            if schedule.everyone_else and schedule.enabled and not self._everyone_else_counted():
                capacity_error = self._capacity_error(adding=1)
                if capacity_error is not None:
                    return capacity_error
            self.repository.save_schedule(schedule)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"schedule": _schedule_payload(schedule)}
            )

        if path == "/api/timers":
            try:
                timer = _timer_from_payload(payload)
                if self.repository.get_device(timer.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(timer.profile_id) is None:
                    raise ValueError("profile does not exist")
                self.repository.save_timer(timer)
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid timer configuration", error)
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "timer": _timer_payload(timer),
                        "live_applied": False,
                        "message": "timer saved; choose Start in hardware mode to start it",
                    }
                )
            status, content_type, body = self._apply_after_change()
            response = json.loads(body.decode("utf-8"))
            response["timer"] = _timer_payload(timer)
            response.setdefault("live_applied", status == HTTPStatus.OK)
            return status, content_type, _json_bytes(response)

        if path == "/api/scenarios":
            try:
                scenario = _scenario_from_payload(payload)
                for step in scenario.steps:
                    if self.repository.get_profile(step.profile_id) is None:
                        raise ValueError(f"profile {step.profile_id} does not exist")
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid scenario", error)
            self.repository.save_scenario(scenario)
            return self._apply_saved_change("scenario", _scenario_payload(scenario))

        if path == "/api/scenarios/start":
            operation = self.scheduler.exclusive() if self.scheduler is not None else nullcontext()
            with operation:
                return self._start_scenario(payload)

        if path == "/api/scenarios/stop":
            try:
                run_id = _required_text(payload, "id")
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid scenario stop", error)
            if not self.repository.stop_scenario_run(run_id):
                return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                    {"error": "scenario run does not exist or has already stopped"}
                )
            return self._apply_saved_change("run_id", run_id)

        if path == "/api/timers/cancel":
            try:
                timer_id = payload["id"]
                if not isinstance(timer_id, str) or not timer_id:
                    raise ValueError("timer id is invalid")
                if not self.repository.delete_timer(timer_id):
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "timer does not exist or is already inactive"}
                    )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid timer cancellation", error)
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {"timer_id": timer_id, "live_applied": False}
                )
            status, content_type, body = self._apply_after_change()
            response = json.loads(body.decode("utf-8"))
            response["timer_id"] = timer_id
            response.setdefault("live_applied", status == HTTPStatus.OK)
            return status, content_type, _json_bytes(response)

        if path == "/api/progressions":
            try:
                progression = _progression_from_payload(payload, repository=self.repository)
                if self.repository.get_device(progression.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(progression.start_profile_id) is None:
                    raise ValueError("start profile does not exist")
                if self.repository.get_profile(progression.end_profile_id) is None:
                    raise ValueError("end profile does not exist")
                self.repository.save_progression(progression)
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid progression configuration", error)
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "progression": _progression_payload(
                            progression,
                            repository=self.repository,
                            now=int(time.time()),
                        ),
                        "live_applied": False,
                        "message": "progression saved; choose Start in hardware mode to start it",
                    }
                )
            status, content_type, body = self._apply_after_change()
            response = json.loads(body.decode("utf-8"))
            response["progression"] = _progression_payload(
                progression,
                repository=self.repository,
                now=int(time.time()),
            )
            response.setdefault("live_applied", status == HTTPStatus.OK)
            return status, content_type, _json_bytes(response)

        if path == "/api/progressions/stop":
            try:
                progression_id = payload["id"]
                if not isinstance(progression_id, str) or not progression_id:
                    raise ValueError("progression id is invalid")
                if not self.repository.delete_progression(progression_id):
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "progression does not exist or is already inactive"}
                    )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid progression stop request", error)
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {"progression_id": progression_id, "live_applied": False}
                )
            status, content_type, body = self._apply_after_change()
            response = json.loads(body.decode("utf-8"))
            response["progression_id"] = progression_id
            response.setdefault("live_applied", status == HTTPStatus.OK)
            return status, content_type, _json_bytes(response)

        if path == "/api/data-limits":
            try:
                data_limit = _data_limit_from_payload(payload, repository=self.repository)
                if self.repository.get_device(data_limit.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(data_limit.profile_id) is None:
                    raise ValueError("profile does not exist")
                existing = self.repository.get_data_limit(data_limit.device_id)
                if data_limit.enabled and not (existing is not None and existing.enabled):
                    # The device counter already holds traffic from before the limit.
                    data_limit = replace(
                        data_limit,
                        counter_bytes=self._quota_counter("devices", data_limit.device_id) or 0,
                    )
                self.repository.save_data_limit(data_limit)
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid data limit configuration", error)
            return self._apply_saved_change(
                "data_limit",
                _data_limit_payload(data_limit, repository=self.repository),
            )

        if path == "/api/data-limits/reset":
            try:
                device_id = payload["device_id"]
                if not isinstance(device_id, str) or not device_id:
                    raise ValueError("device_id is invalid")
                data_limit = self.repository.get_data_limit(device_id)
                if data_limit is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "data limit does not exist"}
                    )
                self.repository.reset_data_limit_usage(
                    device_id,
                    counter_bytes=self._quota_counter("devices", device_id),
                    reset_at=int(time.time()),
                )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid data limit reset request", error)
            refreshed = self.repository.get_data_limit(device_id)
            return self._apply_saved_change(
                "data_limit",
                _data_limit_payload(refreshed, repository=self.repository),
            )

        if path == "/api/group-data-limits":
            try:
                data_limit = _group_data_limit_from_payload(
                    payload,
                    repository=self.repository,
                )
                if self.repository.get_device_group(data_limit.group_id) is None:
                    raise ValueError("device group does not exist")
                if self.repository.get_profile(data_limit.profile_id) is None:
                    raise ValueError("profile does not exist")
                existing = self.repository.get_group_data_limit(data_limit.group_id)
                if data_limit.enabled and not (existing is not None and existing.enabled):
                    # The device counters already hold traffic from before the limit.
                    data_limit = replace(
                        data_limit,
                        counter_bytes=self._group_quota_counter(data_limit.group_id) or 0,
                    )
                self.repository.save_group_data_limit(data_limit)
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid group data limit configuration", error)
            return self._apply_saved_change(
                "group_data_limit",
                _group_data_limit_payload(data_limit, repository=self.repository),
            )

        if path == "/api/group-data-limits/reset":
            try:
                group_id = payload["group_id"]
                if not isinstance(group_id, str) or not group_id:
                    raise ValueError("group_id is invalid")
                data_limit = self.repository.get_group_data_limit(group_id)
                if data_limit is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "group data limit does not exist"}
                    )
                self.repository.reset_group_data_limit_usage(
                    group_id,
                    counter_bytes=self._group_quota_counter(group_id),
                    reset_at=int(time.time()),
                )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid group data limit reset request", error)
            refreshed = self.repository.get_group_data_limit(group_id)
            return self._apply_saved_change(
                "group_data_limit",
                _group_data_limit_payload(refreshed, repository=self.repository),
            )

        if path == "/api/service-quotas":
            try:
                quota = _service_quota_from_payload(payload, repository=self.repository)
                if self.repository.get_device(quota.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(quota.profile_id) is None:
                    raise ValueError("profile does not exist")
                existing = self.repository.get_service_quota(quota.id)
                if quota.enabled and not (existing is not None and existing.enabled):
                    capacity_error = self._capacity_error(adding=1)
                    if capacity_error is not None:
                        return capacity_error
                self.repository.save_service_quota(quota)
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid service quota configuration", error)
            return self._apply_saved_change(
                "service_quota",
                _service_quota_payload(quota, repository=self.repository),
            )

        if path == "/api/service-assignments":
            try:
                assignment = ServiceAssignment(
                    id=payload["id"],
                    device_id=payload["device_id"],
                    name=payload["name"],
                    domains=payload["domains"],
                    profile_id=payload["profile_id"],
                    enabled=payload.get("enabled", True),
                )
                device = self.repository.get_device(assignment.device_id)
                if device is None or device.ipv4_address not in IPv4Network("192.168.50.0/24"):
                    raise ValueError("device must be on the test Wi-Fi")
                if self.repository.get_profile(assignment.profile_id) is None:
                    raise ValueError("profile does not exist")
                if assignment.enabled:
                    other_assignments = tuple(
                        current for current in self.repository.list_service_assignments()
                        if current.enabled and current.id != assignment.id
                    )
                    if (
                        len(assignment.domains)
                        + sum(len(current.domains) for current in other_assignments)
                        > MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN
                    ):
                        raise ValueError("too many browser service domains")
                    already_enabled = any(
                        current.id == assignment.id and current.enabled
                        for current in self.repository.list_service_assignments()
                    )
                    if not already_enabled:
                        capacity_error = self._capacity_error(adding=1)
                        if capacity_error is not None:
                            return capacity_error
                    existing = (
                        current for current in other_assignments
                        if current.device_id == assignment.device_id
                    )
                    if any(
                        domain == previous or domain.endswith("." + previous)
                        or previous.endswith("." + domain)
                        for current in existing
                        for domain in assignment.domains
                        for previous in current.domains
                    ):
                        raise ValueError("service assignment domains overlap")
                self.repository.save_service_assignment(assignment)
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid service assignment configuration", error)
            return self._apply_saved_change(
                "service_assignment",
                _service_assignment_payload(assignment, repository=self.repository),
            )

        if path == "/api/service-assignments/delete":
            assignment_id = payload.get("id")
            if not isinstance(assignment_id, str):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid service assignment id"}
                )
            if not self.repository.delete_service_assignment(assignment_id):
                return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                    {"error": "service assignment does not exist"}
                )
            return self._apply_saved_change("deleted_id", assignment_id)

        if path == "/api/service-lists":
            return self._save_service_list(payload)

        if path == "/api/service-lists/delete":
            return self._delete_service_list(payload)

        if path == "/api/service-quotas/reset":
            try:
                quota_id = payload["id"]
                if not isinstance(quota_id, str) or not quota_id:
                    raise ValueError("quota id is invalid")
                quota = self.repository.get_service_quota(quota_id)
                if quota is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "service quota does not exist"}
                    )
                self.repository.reset_service_quota_usage(
                    quota_id,
                    counter_bytes=self._quota_counter("services", quota_id),
                    reset_at=int(time.time()),
                )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid service quota reset request", error)
            refreshed = self.repository.get_service_quota(quota_id)
            return self._apply_saved_change(
                "service_quota",
                _service_quota_payload(refreshed, repository=self.repository),
            )

        if path == "/api/time-quotas":
            try:
                quota = _time_quota_from_payload(payload, repository=self.repository)
                if self.repository.get_device(quota.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(quota.profile_id) is None:
                    raise ValueError("profile does not exist")
                existing = self.repository.get_time_quota(quota.id)
                if quota.enabled and not (existing is not None and existing.enabled):
                    capacity_error = self._capacity_error(adding=1)
                    if capacity_error is not None:
                        return capacity_error
                self.repository.save_time_quota(quota)
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid time quota configuration", error)
            return self._apply_saved_change(
                "time_quota",
                _time_quota_payload(quota, repository=self.repository),
            )

        if path == "/api/time-quotas/start":
            try:
                quota_id = payload["id"]
                if not isinstance(quota_id, str) or not quota_id:
                    raise ValueError("quota id is invalid")
                quota = self.repository.get_time_quota(quota_id)
                if quota is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "time quota does not exist"}
                    )
                started_at = int(time.time())
                refreshed = self.repository.start_time_quota(
                    quota_id,
                    started_at=started_at,
                    expires_at=started_at + quota.window_duration_seconds,
                    counter_bytes=self._quota_counter(
                        "services", time_quota_counter_id(quota_id)
                    ),
                )
                if refreshed is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "time quota does not exist"}
                    )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid time quota start request", error)
            return self._apply_saved_change(
                "time_quota",
                _time_quota_payload(refreshed, repository=self.repository),
            )

        if path == "/api/time-quotas/stop":
            try:
                quota_id = payload["id"]
                if not isinstance(quota_id, str) or not quota_id:
                    raise ValueError("quota id is invalid")
                if not self.repository.stop_time_quota(quota_id):
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "time quota does not exist"}
                    )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid time quota stop request", error)
            refreshed = self.repository.get_time_quota(quota_id)
            return self._apply_saved_change(
                "time_quota",
                _time_quota_payload(refreshed, repository=self.repository),
            )

        if path == "/api/time-quotas/reset":
            try:
                quota_id = payload["id"]
                if not isinstance(quota_id, str) or not quota_id:
                    raise ValueError("quota id is invalid")
                quota = self.repository.get_time_quota(quota_id)
                if quota is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "time quota does not exist"}
                    )
                self.repository.reset_time_quota_usage(
                    quota_id,
                    counter_bytes=self._quota_counter(
                        "services", time_quota_counter_id(quota_id)
                    ),
                    reset_at=int(time.time()),
                )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return _bad_request("invalid time quota reset request", error)
            refreshed = self.repository.get_time_quota(quota_id)
            return self._apply_saved_change(
                "time_quota",
                _time_quota_payload(refreshed, repository=self.repository),
            )

        if path == "/api/everyone-else":
            if "profile_id" not in payload:
                return _bad_request(
                    "invalid everyone-else profile", KeyError("profile_id is required")
                )
            profile_id = payload["profile_id"]
            if profile_id is not None and (
                not isinstance(profile_id, str) or self.repository.get_profile(profile_id) is None
            ):
                return _bad_request(
                    "invalid everyone-else profile", ValueError("profile does not exist")
                )
            if profile_id is not None and not self._everyone_else_counted():
                capacity_error = self._capacity_error(adding=1)
                if capacity_error is not None:
                    return capacity_error
            self.repository.save_everyone_else_profile_id(profile_id)
            return self._apply_saved_change(
                "everyone_else", _everyone_else_payload(self.repository)
            )

        if path == "/api/policies":
            try:
                mac_policy, website_policy = _policies_from_payload(payload)
                blocks = payload.get("site_blocks")
                if blocks is not None:
                    if not isinstance(blocks, list) or len(blocks) > 32:
                        raise ValueError("site_blocks must be a list of at most 32 rules")
                    blocks = tuple(
                        _from_known_fields(SiteBlock, rule, f"site_blocks[{index}]")
                        for index, rule in enumerate(blocks))
                options = (_from_known_fields(AccessOptions, payload["options"], "options")
                           if "options" in payload else None)
                # Check every supplied section above, then write only the sections the
                # request carried. An omitted section keeps its stored value, while an
                # explicit empty section resets just that section.
                self.repository.save_access_policies(
                    mac_policy if "mac" in payload else None,
                    website_policy if "websites" in payload else None,
                    blocks=blocks, options=options,
                    expected_version=payload.get("version"),
                )
            except DeviceConflictError as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid network policy configuration", error)
            return HTTPStatus.OK, "application/json", _json_bytes(
                _policies_payload(self.repository)
            )

        if path == "/api/access-point/reset-name":
            if self.helper_client is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "network helper is not configured"}
                )
            return self._start_wifi_change("reset-name", self._reset_wifi_name, payload)

        if path == "/api/access-point":
            if self.helper_client is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "network helper is not configured"}
                )
            try:
                ssid, password = payload["ssid"], payload["password"]
                # A refusal can only reach the browser now, not after the Wi-Fi restarted.
                _validate_access_point_settings(ssid=ssid, password=password)
            except (KeyError, TypeError, ValueError) as error:
                return _bad_request("invalid access point settings", error)
            return self._start_wifi_change(
                "access-point", lambda: self._configure_access_point(ssid, password), payload)

        if path.startswith("/api/router/"):
            return self._handle_router_request(path, payload)

        if path == "/api/connected-devices/apply-profile":
            return self._apply_profile_to_connected_device(payload)

        if path == "/api/observations/scan":
            return self._scan_dns_observations()

        if path == "/api/observations/add-to-profile":
            return self._add_observation_to_profile(payload)

        if path == "/api/observations/add-global-block":
            arguments = {"control_intent": control_intent} if control_intent is not None else {}
            return self._add_observation_to_global_block(payload, **arguments)

        if path == "/api/observations/remove-global-block":
            arguments = {"control_intent": control_intent} if control_intent is not None else {}
            return self._remove_observation_from_global_block(payload, **arguments)

        if path == "/api/activity/clear":
            self.repository.clear_activity()
            return HTTPStatus.OK, "application/json", _json_bytes({"cleared": True})

        # Saved changes apply the plan too, but only these buttons count as
        # an Apply or a Reset in the activity log.
        if path == "/api/apply":
            arguments = {"control_intent": control_intent} if control_intent is not None else {}
            status, content_type, body = self._apply_configuration(explicit_apply=True, **arguments)
            if status == HTTPStatus.OK:
                self._record_activity("apply", "Configuration applied")
            return status, content_type, body

        if path == "/api/reset":
            status, content_type, body = self._reset_network()
            if status == HTTPStatus.OK:
                self._record_activity("reset", "Network reset")
            return status, content_type, body

        return HTTPStatus.NOT_FOUND, "application/json", _json_bytes({"error": "not found"})

    def server_close(self) -> None:
        deadline = time.monotonic() + self.shutdown_timeout_seconds
        if self.scheduler is not None:
            self.scheduler.begin_shutdown()
        if self.homepage_store is not None:
            self.homepage_store.close()
        if self.homepage_server is not None:
            self.homepage_server.shutdown()
            self.homepage_server.server_close()
            self.homepage_worker.join(timeout=1)
        self._stop_connections.set()
        self._request_workers.stop_admission()
        self.router_operations.begin_shutdown()
        self.wifi_operations.begin_shutdown()
        for connection in tuple(self._pending_connections):
            self._close_pending(connection)
        # The listener can close at once. Shared state must outlive every owner.
        super().server_close()
        unresolved = []
        cleanup = self._cleanup_worker
        if cleanup is not None and cleanup is not threading.current_thread():
            cleanup.join(timeout=max(0.0, deadline - time.monotonic()))
            if cleanup.is_alive():
                unresolved.append(WorkersStillRunning(
                    "The device-only cleanup did not stop. Its state remains owned."))
        for owner in (self._request_workers, self.router_operations, self.wifi_operations):
            try:
                owner.close(timeout=max(0.0, deadline - time.monotonic()))
            except WorkersStillRunning as error:
                unresolved.append(error)
        if unresolved:
            raise unresolved[0]
        self._request_inputs.clear()

    def _record_wifi_outcome(self) -> None:
        outcome = self.wifi_operations.completed_action()
        if outcome:
            try:
                self.repository.record_action(action=outcome[0], status_code=outcome[1])
            except StorageError:
                LOGGER.warning("could not save completed operation history")

    def _start_wifi_change(self, kind, work, payload):
        """Legacy callers keep synchronous responses. The dashboard opts into exact outcomes."""
        self._record_wifi_outcome()
        identifier = payload.get("request_id")
        if "request_id" in payload and not valid_request_id(identifier):
            return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": "Invalid or expired Wi-Fi request identifier."})
        if identifier is None:
            reserved = self.wifi_operations.reserve(kind)
            if reserved is None:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": "Another Pi Wi-Fi change is running."})
            if not self.wifi_operations.ready(reserved["id"], delayed=False):
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes({"error": "Service is stopping. Check current Wi-Fi settings."})
            try:
                if kind == "reset-name":
                    result = self.helper_client.reset_wifi_name()
                    if result.get("ssid") != DEFAULT_WIFI_NAME or result.get("security") not in SECURITY_MODES:
                        raise HelperClientError("The helper did not confirm the expected Wi-Fi settings.")
                    outcome = WifiOutcome(200, "Wi-Fi name restored.", ssid=result["ssid"], security=result["security"])
                else:
                    result = self.helper_client.configure_access_point(ssid=payload["ssid"], password=payload["password"])
                    outcome = WifiOutcome(200, "Wi-Fi updated.", ssid=payload["ssid"], security="WPA3", password_changed=True)
            except HelperClientError as error:
                outcome = (_wifi_failure(error, "Wi-Fi name reset failed",
                           "Check the Pi helper and jittersim-ap connection through management access.")
                           if kind == "reset-name" else WifiOutcome(502, "access point configuration failed"))
                self.wifi_operations.finish(reserved["id"], outcome)
                return outcome.status, "application/json", _json_bytes({"error": outcome.message})
            except Exception:
                self.wifi_operations.finish(reserved["id"], WifiOutcome(502, "Wi-Fi result is uncertain. Check current settings."))
                raise
            self.wifi_operations.finish(reserved["id"], outcome)
            return HTTPStatus.OK, "application/json", _json_bytes({"result": result})
        started = self.wifi_operations.start(kind, work, request_id=identifier)
        if started is None:
            return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                {"error": "Another Wi-Fi change is running or the outcome store is full. Try again later."})
        return HTTPStatus.ACCEPTED, "application/json", _json_bytes(
            {"operation_id": started["id"], "pending": True, "kind": kind})

    def _reset_wifi_name(self) -> WifiOutcome:
        try:
            result = self.helper_client.reset_wifi_name()
            if result.get("ssid") != DEFAULT_WIFI_NAME or result.get("security") not in SECURITY_MODES:
                raise HelperClientError("The helper did not confirm the expected Wi-Fi settings.")
        except HelperClientError as error:
            return _wifi_failure(error, "Wi-Fi name reset failed",
                                 "Check the Pi helper and jittersim-ap connection through management access.")
        return WifiOutcome(
            200, "%s restored using %s. Password unchanged." % (result["ssid"], result["security"]),
            ssid=result["ssid"], security=result["security"])

    def _configure_access_point(self, ssid, password) -> WifiOutcome:
        try:
            self.helper_client.configure_access_point(ssid=ssid, password=password)
        except (TypeError, ValueError, HelperClientError) as error:
            LOGGER.warning("access point configuration failed")
            if not isinstance(error, HelperClientError):
                error = HelperClientError("access point configuration failed")
            return _wifi_failure(error, "Pi Wi-Fi update failed",
                                 "Check the Pi helper and jittersim-ap connection through management access.")
        return WifiOutcome(
            200, "Pi Wi-Fi updated. Join %s with the new password." % ssid,
            ssid=ssid, security="WPA3", password_changed=True)

    def _router_execute(self, path, payload):
        """Run a router request on its worker. A Wi-Fi copy is also recorded as a Wi-Fi change."""
        if path != "/api/router/sync-ap":
            return self._router_response_for(path, payload)
        identifier = payload.get("_wifi_operation_id")
        if identifier is None:
            reserved = self.wifi_operations.reserve("router-copy")
            identifier = reserved["id"] if reserved else None
        if identifier is None:
            return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": "Another Pi Wi-Fi change is running."})
        if not self.wifi_operations.ready(identifier, delayed=payload.get("background", True)):
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes({"error": "Service is stopping. Check current Wi-Fi settings."})
        outcome = WifiOutcome(502, "The Wi-Fi copy did not finish. Check the Pi Wi-Fi settings before trying again.")
        try:
            status, content_type, body = self._router_response_for(path, payload)
            try:
                data = json.loads(body)
                if status == HTTPStatus.OK:
                    result = data["result"]
                    outcome = WifiOutcome(
                        200, "Copied %s using %s. Join with the router's Wi-Fi password."
                        % (result["ssid"], result["security"]),
                        ssid=result["ssid"], security=result["security"], password_changed=True)
                else:
                    outcome = WifiOutcome(int(status), data["error"])
            except (KeyError, TypeError, ValueError):
                pass
            return status, content_type, body
        finally:
            # A request that raised keeps the failure outcome and the exception goes on.
            if identifier is not None:
                self.wifi_operations.finish(identifier, outcome)

    def _record_router_outcome(self) -> None:
        outcome = self.router_operations.completed_action()
        if outcome:
            try:
                self.repository.record_action(action=outcome[0], status_code=outcome[1])
            except StorageError:
                LOGGER.warning("could not save completed operation history")

    def _handle_router_request(self, path, payload):
        self._record_router_outcome()
        # A copy of the router Wi-Fi replaces the remembered Wi-Fi change. Write the last one
        # into the history first, on this request thread, so it is not lost.
        self._record_wifi_outcome()
        password = payload.get("admin_password")
        if not isinstance(password, str) or not password or len(password) > 1024:
            return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                {"error": "router admin password is required"})
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "network helper is not configured"})
        if path == "/api/router/operation":
            identifier = payload.get("operation_id")
            if not isinstance(identifier, str) or len(identifier) > 64:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": "invalid router operation"})
            status, data = self.router_operations.result(identifier, password)
            return status, "application/json", _json_bytes(data)
        if path == "/api/router/status":
            refresh = payload.get("refresh", False)
            if type(refresh) is not bool:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid router status request"})
            data = self.router_operations.status(password, refresh=refresh)
            return HTTPStatus.OK, "application/json", _json_bytes({"result": data})
        if path == "/api/router/wifi":
            if type(payload.get("enabled")) is not bool:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": "router Wi-Fi state is invalid"})
            if not payload["enabled"] and payload.get("confirm_disable") is not True:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "turning off router Wi-Fi requires confirmation"})
        if path == "/api/router/wifi-credentials":
            # Never a background operation: those keep their result for minutes and this one
            # carries the Wi-Fi password.
            return self._router_response_for(path, payload)
        if payload.get("background") is True:
            identifier = payload.get("request_id")
            if "request_id" in payload and not valid_request_id(identifier):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": "Invalid or expired router request identifier."})
            fields = {key: payload[key] for key in (
                "admin_password", "enabled", "confirm_disable", "expected_fingerprint", "confirm_legacy") if key in payload}
            if identifier is not None:
                fields["request_id"] = identifier
            reserved = None
            if path == "/api/router/sync-ap":
                if identifier is not None:
                    previous_status, previous = self.router_operations.result(identifier, password, expected_path=path)
                    if previous_status != 410:
                        return previous_status, "application/json", _json_bytes(previous)
                reserved = self.wifi_operations.reserve("router-copy", identifier)
                if reserved is None:
                    return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": "Another Wi-Fi change is running or the outcome store is full."})
                fields["_wifi_operation_id"] = reserved["id"]
            status, data = self.router_operations.start(path, fields)
            if status != 202 and reserved is not None:
                self.wifi_operations.finish(reserved["id"], WifiOutcome(status, data.get("error", "Router change refused.")))
            return status, "application/json", _json_bytes(data)
        if path == "/api/router/sync-ap":
            return self._router_execute(path, {**payload, "background": False})
        if path == "/api/router/check":
            return self._router_check_now(path, payload)
        return self._router_response_for(path, payload)

    def _router_check_now(self, path, payload):
        """A password check that is not a background operation spends the same router budget.

        Every check signs in to the router, so a direct API call may not repeat one early.
        """
        refused = self.router_operations.begin_check()
        if refused is not None:
            return refused[0], "application/json", _json_bytes(refused[1])
        status = HTTPStatus.BAD_GATEWAY
        try:
            response = self._router_response_for(path, payload)
            status = response[0]
            return response
        finally:
            self.router_operations.end_check(status == HTTPStatus.OK)

    def _router_response_for(self, path, payload):
        if path == "/api/router/status":
            try:
                reader = getattr(self.helper_client, "read_router_wifi_status", None)
                if not callable(reader):
                    raise HelperClientError("The networking helper is out of date. Restart the JitterSim services or reinstall the release.")
                result = reader(admin_password=payload["admin_password"])
                if type(result.get("enabled")) is not bool:
                    raise HelperClientError("Could not check the router password. Try again in a moment.")
                result = {"enabled": result["enabled"]}
            except HelperClientError as error:
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": safe_router_check_message(str(error))})
            return HTTPStatus.OK, "application/json", _json_bytes({"result": result})
        if path == "/api/router/wifi-credentials":
            include_password = payload.get("include_password", False)
            if type(include_password) is not bool:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid router Wi-Fi request"})
            reader = getattr(self.helper_client, "read_router_wifi_credentials", None)
            try:
                if not callable(reader):
                    raise HelperClientError(
                        "The networking helper is out of date. Restart the JitterSim services "
                        "or reinstall the release.")
                found = reader(admin_password=payload["admin_password"],
                               include_password=include_password)
                # Pass on only what this request asked for whatever the helper sent.
                result = {"ssid": found["ssid"]}
                if include_password:
                    result["password"] = found["password"]
                if not all(isinstance(value, str) for value in result.values()):
                    raise TypeError("invalid Wi-Fi details")
            except HelperClientError as error:
                message = _safe_wifi_copy_message(error)
            except (KeyError, TypeError):
                message = ROUTER_CHECK_MESSAGES["failed"]
            else:
                return HTTPStatus.OK, "application/json", _json_bytes({"result": result})
            LOGGER.warning("router Wi-Fi copy failed: %s", message)
            return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes({"error": message})
        if path in ("/api/router/sync-ap", "/api/router/preview-ap"):
            if self.helper_client is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "network helper is not configured"}
                )
            admin_password = payload.get("admin_password")
            if not isinstance(admin_password, str) or not admin_password:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "router admin password is required"}
                )
            try:
                if path == "/api/router/preview-ap":
                    preview = getattr(self.helper_client, "preview_router_wifi", None)
                    if not callable(preview):
                        raise HelperClientError("The networking helper is out of date. Reinstall the release before copying Wi-Fi.")
                    result = preview(admin_password=admin_password)
                    if (result.get("security") not in SECURITY_MODES
                            or result.get("band") not in ("2.4 GHz", "5 GHz", "6 GHz")
                            or not isinstance(result.get("ssid"), str)
                            or not isinstance(result.get("fingerprint"), str)
                            or len(result["fingerprint"]) != 64
                            or not isinstance(result.get("legacy"), bool)):
                        raise HelperClientError("The networking helper is out of date. Reinstall the release before copying Wi-Fi.")
                    result = {key: result[key] for key in ("ssid", "security", "band", "fingerprint", "legacy")}
                else:
                    fingerprint = payload.get("expected_fingerprint")
                    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
                        return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": "Check router Wi-Fi security before copying."})
                    result = self.helper_client.sync_access_point_from_router(
                        admin_password=admin_password, expected_fingerprint=fingerprint,
                        confirm_legacy=payload.get("confirm_legacy") is True,
                    )
            except HelperClientError as error:
                message = safe_router_import_message(str(error))
                LOGGER.warning("router Wi-Fi import failed: %s", message)
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": message}
                )
            return HTTPStatus.OK, "application/json", _json_bytes({"result": result})

        if path == "/api/router/check":
            if self.helper_client is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "network helper is not configured"}
                )
            admin_password = payload.get("admin_password")
            if not isinstance(admin_password, str) or not admin_password:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "router admin password is required"}
                )
            try:
                result = self.helper_client.check_router_password(admin_password=admin_password)
            except HelperClientError as error:
                message = safe_router_check_message(str(error))
                LOGGER.warning("router password check failed: %s", message)
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes({"error": message})
            return HTTPStatus.OK, "application/json", _json_bytes({"result": result})

        if path == "/api/router/wifi":
            if self.helper_client is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "network helper is not configured"}
                )
            admin_password = payload.get("admin_password")
            enabled = payload.get("enabled")
            if not isinstance(admin_password, str) or not admin_password:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "router admin password is required"}
                )
            if not isinstance(enabled, bool):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "router Wi-Fi state is invalid"}
                )
            if not enabled and payload.get("confirm_disable") is not True:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "turning off router Wi-Fi requires confirmation"}
                )
            try:
                result = self.helper_client.set_router_wifi(
                    admin_password=admin_password,
                    enabled=enabled,
                )
                if result.get("enabled") is not enabled:
                    raise HelperClientError(
                        "The router may have changed Wi-Fi, but JitterSim could not confirm both radios. "
                        "Check the router over Ethernet before retrying.")
            except HelperClientError as error:
                message = safe_router_wifi_message(str(error))
                LOGGER.warning("router Wi-Fi change failed: %s", message)
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes({"error": message})
            return HTTPStatus.OK, "application/json", _json_bytes({"result": result})

        return HTTPStatus.NOT_FOUND, "application/json", _json_bytes({"error": "not found"})

    def _scan_dns_observations(self) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "network helper is not configured"}
            )
        macs = protected_macs(self.repository)
        if len(macs) > MAX_PROTECTED_MACS:
            return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                {
                    "error": (
                        "Traffic can only be scanned while at most "
                        f"{MAX_PROTECTED_MACS} devices are protected. "
                        "Remove some from the Protected list."
                    )
                }
            )
        try:
            raw_observations = self.helper_client.observe_dns_queries(macs)
            stations = self._station_devices()
            blocked = protected_addresses(self.repository, stations)
            macs_by_address = station_macs_by_address(stations)
            observed_at = int(time.time())
            observations = [
                DomainObservation(
                    ipv4_address=observation["ipv4_address"],
                    domain=observation["domain"],
                    query_count=observation["query_count"],
                    first_seen=observed_at,
                    last_seen=observed_at,
                    mac_address=macs_by_address.get(str(observation["ipv4_address"])),
                )
                for observation in raw_observations
            ]
            observations = [
                observation
                for observation in observations
                if not observation_is_protected(observation, set(macs), blocked)
            ]
            self.repository.save_domain_observations(observations, now=observed_at)
        except (HelperClientError, KeyError, TypeError, ValueError, StorageError) as error:
            LOGGER.exception("could not scan DNS observations")
            message = "could not scan traffic observations"
            if isinstance(error, HelperClientError) and str(error) == HELPER_OUT_OF_DATE_MESSAGE:
                message = HELPER_OUT_OF_DATE_MESSAGE
            return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes({"error": message})
        return HTTPStatus.OK, "application/json", _json_bytes(
            {
                "captured_observations": len(observations),
                "observations": [
                    {
                        "ipv4_address": str(observation.ipv4_address),
                        **_domain_observation_payload(observation),
                    }
                    for observation in observations
                ],
            }
        )

    def _add_observation_to_profile(
        self, payload: Dict[str, Any]
    ) -> Tuple[int, str, bytes]:
        try:
            profile_id = payload["profile_id"]
            names = _domains_from_payload(payload)
            confirm_allow_only = payload.get("confirm_allow_only", False)
            if not isinstance(confirm_allow_only, bool):
                raise ValueError("confirm_allow_only must be true or false")
            with self.repository.transaction():
                profile = self.repository.get_profile(profile_id)
                if profile is None:
                    raise ValueError("profile does not exist")
                if profile.website_mode != "allow_only" and not confirm_allow_only:
                    return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                        {
                            "code": "allow_only_confirmation_required",
                            "error": (
                                "This profile no longer allows only listed sites. "
                                "Nothing was added. Choose Add again to confirm restricting it."
                            ),
                            "profile": _profile_payload(profile),
                        }
                    )
                listed = tuple(dict.fromkeys(profile.website_domains + names))
                if len(listed) > MAX_LISTED_DOMAINS:
                    return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                        {"error": "A profile can list %d websites. Remove some first." % MAX_LISTED_DOMAINS}
                    )
                updated = replace(profile, website_mode="allow_only", website_domains=listed)
                self.repository.save_profile(updated)
        except (KeyError, TypeError, ValueError, StorageError) as error:
            return _bad_request("could not add domain to profile", error)
        except sqlite3.Error:
            LOGGER.exception("could not save observed websites to a profile")
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "The profile could not be saved. Try again."}
            )
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"profile": _profile_payload(updated)}
        )

    def _add_observation_to_global_block(
        self, payload: Dict[str, Any], *, control_intent=None
    ) -> Tuple[int, str, bytes]:
        try:
            names = _domains_from_payload(payload)
            policy = self.repository.get_website_policy()
            listed = tuple(dict.fromkeys(policy.blocked_domains + names))
            if len(listed) > MAX_LISTED_DOMAINS:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {
                        "error": "The global block list can hold %d names. Remove some first."
                        % MAX_LISTED_DOMAINS
                    }
                )
            updated = replace(policy, blocked_domains=listed)
            self.repository.save_website_policy(updated)
        except (KeyError, TypeError, ValueError, StorageError) as error:
            return _bad_request("could not add domain to global block list", error)
        arguments = {"control_intent": control_intent} if control_intent is not None else {}
        failed = self._apply_household_block_list(**arguments)
        if failed is not None:
            return failed
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"policies": _policies_payload(self.repository)}
        )

    def _remove_observation_from_global_block(
        self, payload: Dict[str, Any], *, control_intent=None
    ) -> Tuple[int, str, bytes]:
        try:
            names = _domains_from_payload(payload)
            policy = self.repository.get_website_policy()
            remaining = tuple(item for item in policy.blocked_domains if item not in names)
            if remaining != policy.blocked_domains:
                self.repository.save_website_policy(replace(policy, blocked_domains=remaining))
        except (KeyError, TypeError, ValueError, StorageError):
            return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                {"error": "could not remove domain from global block list"}
            )
        arguments = {"control_intent": control_intent} if control_intent is not None else {}
        failed = self._apply_household_block_list(**arguments)
        if failed is not None:
            return failed
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"policies": _policies_payload(self.repository)}
        )

    def _apply_household_block_list(self, *, control_intent=None) -> Optional[Tuple[int, str, bytes]]:
        """Bring the filtered resolver in line with a changed global block list.

        Block globally and Unblock act at once like main's device quick action. Main's
        resolver otherwise kept refusing an unblocked name and the names under it until
        someone chose Apply access rules. While household access rules are off the
        resolver does not filter so nothing needs to change. The scheduler still blocks
        the addresses of each listed name within a second while JitterSim runs.
        """

        if control_intent is None:
            generation = getattr(self.scheduler, "apply_generation", 0)
            refused, revision = self._capture_control_intent(generation)
        else:
            generation, revision = control_intent
            refused = self._apply_refused(generation)
        if refused is not None:
            return refused
        settings = self.repository.access_settings()
        if (
            not settings.access_options.enabled
            or self.helper_client is None
            or not hasattr(self.helper_client, "apply_access")
        ):
            return None
        try:
            arguments = {"control_revision": revision} if revision is not None else {}
            refused = self._apply_refused(generation)
            if refused is not None:
                return refused
            self.helper_client.apply_access(build_access_plan(settings), **arguments)
            refused = self._apply_refused(generation, undo=True)
            if refused is not None:
                return refused
        except (HelperClientError, ValidationError) as error:
            if isinstance(error, HelperClientError):
                rejected = self._control_rejected(error)
                if rejected is not None:
                    return rejected
            refused = self._apply_refused(generation, undo=True)
            if refused is not None:
                return refused
            LOGGER.warning("the global block list did not reach the filtered resolver")
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {
                    "error": (
                        "Saved but the household access rules could not be updated. "
                        "Choose Apply access rules on the Access and rules page."
                    )
                }
            )
        return None

    def _save_service_list(self, payload: dict):
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            try:
                service_list = ServiceList(
                    id=payload["id"],
                    name=payload["name"],
                    profile_id=payload["profile_id"],
                    services=payload.get("services", []),
                    hostnames=payload.get("hostnames", []),
                    enabled=payload.get("enabled", True),
                )
                if self.repository.get_profile(service_list.profile_id) is None:
                    raise ValidationError("profile does not exist")
                if service_list.enabled and self.helper_client is not None:
                    self.helper_client.check_service_list_support()
                with self.repository.transaction():
                    self.repository.save_service_list(service_list)
                    self._validate_saved_plan()
            except HelperClientError as error:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": str(error)}
                )
            except PlanError as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            except ValidationError as error:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": str(error)}
                )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid service list configuration"}
                )
            return self._apply_saved_change(
                "service_list",
                _service_list_payload(service_list, repository=self.repository),
            )

    def _delete_service_list(self, payload: dict):
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            list_id = payload.get("id")
            if not isinstance(list_id, str):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid service list id"}
                )
            if not self.repository.delete_service_list(list_id):
                return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                    {"error": "service list does not exist"}
                )
            return self._apply_saved_change("deleted_id", list_id)

    def _validate_saved_plan(self) -> None:
        # Saving checks structure atomically. Applying resolves live addresses later.
        with without_dns():
            self._validate_saved_structure()

    def _validate_saved_structure(self) -> None:
        if self.repository.get_general_profile() is not None:
            exceptions = self.repository.get_everyone_exceptions()
            if exceptions:
                # Everyone, the devices beside it, their service rules and the service lists
                # share the classes. Count them structurally: a plan built without DNS leaves
                # service limits out, which must not let a device in that the real plan refuses.
                eligible_ids = {
                    device.id for device in eligible_clients(self.repository).devices
                    if device.id in exceptions
                }
                classes = 1 + len(eligible_ids) + sum(
                    bool(item.enabled and item.device_id in eligible_ids)
                    for items in (
                        self.repository.list_service_quotas(),
                        self.repository.list_time_quotas(),
                        self.repository.list_service_assignments(),
                    )
                    for item in items
                ) + sum(item.enabled for item in self.repository.list_service_lists())
                if classes > MAX_SHAPED_MARKS_PER_PLAN:
                    raise PlanError(
                        "Everyone, the devices that run beside it, their service rules and "
                        "the service lists can use at most "
                        f"{MAX_SHAPED_MARKS_PER_PLAN} traffic classes together. Remove one or "
                        "keep the device protected."
                    )
            compile_stored_plan(repository=self.repository)
            return
        clients = eligible_clients(self.repository)
        eligible_ids = {device.id for device in clients.devices}
        rules = len(eligible_ids) + sum(
            bool(item.enabled and item.device_id in eligible_ids)
            for items in (
                self.repository.list_service_quotas(),
                self.repository.list_time_quotas(),
                self.repository.list_service_assignments(),
            )
            for item in items
        ) + sum(item.enabled for item in self.repository.list_service_lists())
        if rules > 15:
            raise PlanError(
                "At most 15 affected devices, service rules and service lists can be active. Remove one or keep the device protected."
            )
        compile_stored_plan(repository=self.repository)

    def _verify_selected_client(self, device: Device, snapshot: dict) -> None:
        if not snapshot["association_status_available"] or not any(
            client["association_verified"]
            and client["ipv4_address"] == str(device.ipv4_address)
            and client["mac_address"] == device.mac_address
            for client in snapshot["devices"]
        ):
            raise ValueError(
                "The selected client is no longer verified on JitterSim Wi-Fi. Refresh the list."
            )

    def _saved_control_response(self, fields: dict, snapshot=None):
        if self.helper_client is None:
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes(
                    {
                        **fields,
                        "live_applied": False,
                        "message": "Saved. Connect the network helper to apply this change.",
                    }
                ),
            )
        scoped = False
        if self.repository.get_runtime_state().last_action == "reset":
            # Only an explicit Apply may undo a Reset. A device-only start keeps
            # applying its own devices and nothing else.
            scoped = self.repository.get_device_start_scope() is not None
            if not scoped:
                return (
                    HTTPStatus.OK,
                    "application/json",
                    _json_bytes(
                        {
                            **fields,
                            "live_applied": False,
                            "message": "Saved. The network is reset, so choose Start to use it.",
                        }
                    ),
                )
        status, content_type, body = self._apply_configuration_locked(
            connected_snapshot=snapshot, sample_usage=False, device_scope=scoped,
            keep_exceptions=True,
        )
        response = json.loads(body)
        response.update(fields)
        response["live_applied"] = status == HTTPStatus.OK
        if status != HTTPStatus.OK:
            response["error"] = (
                "Settings saved but the network update failed. Protection is not confirmed. "
                + response.get("error", "")
            )
        return status, content_type, _json_bytes(response)

    def _device_protection_states(self) -> Dict[str, Dict[str, Any]]:
        """Whether each saved profile applies, using the same check as the plan."""

        return device_protection_states(self.repository, self._protection_snapshot())

    def _protection_snapshot(self) -> Optional[dict]:
        """The live clients when a protected client makes identity matter."""

        if self.helper_client is None or not self.repository.list_unaffected_devices():
            return None
        return station_snapshot(self.helper_client)

    def _impact_status(self) -> Dict[str, Any]:
        """Name what each applied queue did so far and list the protected devices."""

        def unavailable(message: str, *, paused: bool = False) -> Dict[str, Any]:
            # `paused` means the answer is only late: the page keeps what it shows.
            return {
                "available": False,
                "paused": paused,
                "message": message,
                "sampled_at": time.time(),
                "queues": [],
                "protected": [],
            }

        if self.helper_client is None:
            return unavailable(IMPACT_NO_HELPER_MESSAGE)
        if self.repository.get_runtime_state().observed_state == "reset":
            return unavailable(IMPACT_RESET_MESSAGE)
        read_queues = getattr(self.helper_client, "queue_stats", None)
        if read_queues is None:
            return unavailable(IMPACT_OUT_OF_DATE_MESSAGE)
        try:
            queues = read_queues()
        except HelperClientError as error:
            # A helper from before this check answers that it does not know the action.
            if str(error) == "unknown action":
                return unavailable(IMPACT_OUT_OF_DATE_MESSAGE)
            LOGGER.warning("could not read queue statistics for the impact check")
            return unavailable(IMPACT_UNREADABLE_MESSAGE, paused=True)
        # The page divides counter changes by the time between samples so the time is taken
        # when the helper answers and not after the names are looked up.
        sampled_at = time.time()
        devices_by_id = {device.id: device for device in self.repository.list_devices()}
        labels_by_mac = self.repository.list_connected_device_labels()
        list_names = {item.id: item.name for item in self.repository.list_service_lists()}
        protected = self.repository.list_unaffected_devices()
        protected_hardware = {item.mac_address for item in protected}
        entries = []
        for queue in queues:
            device = devices_by_id.get(queue["device_id"])
            # A protected device has no queue. A stale plan must never put one on screen.
            # A device without a MAC address is paused while any client is protected.
            if device is not None and protected_hardware and (
                device.mac_address is None or device.mac_address in protected_hardware
            ):
                continue
            name, kind_label = _impact_queue_name(
                queue["device_id"],
                devices_by_id=devices_by_id,
                labels_by_mac=labels_by_mac,
                list_names=list_names,
            )
            entries.append({**queue, "name": name, "kind_label": kind_label})
        return {
            "available": True,
            "paused": False,
            "message": None,
            "sampled_at": sampled_at,
            "queues": entries,
            "protected": sorted(
                (item.display_name or item.mac_address for item in protected),
                key=lambda name: (name.lower(), name),
            ),
        }

    def _general_profile_status(self) -> dict:
        # A read: it must not wait behind an apply that holds the write lock.
        return self._general_profile_status_locked()

    def _general_profile_status_locked(self) -> dict:
        profile = self.repository.get_general_profile()
        state = self.repository.get_runtime_state()
        active = profile is not None and state.observed_state == "applied"
        if active and self.scheduler is not None:
            limits = self.scheduler.applied_queue_bandwidth_limits()
            active = limits is not None and GENERAL_QUEUE_ID in limits
        return {
            "profile": _profile_payload(profile) if profile else None,
            "active": active,
            "state": _runtime_state_payload(state),
        }

    def _change_general_profile(self, payload: dict, *, stop: bool, control_intent=None):
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "Connect the Pi networking helper to apply a profile for everyone"}
            )
        if control_intent is None:
            generation = getattr(self.scheduler, "apply_generation", getattr(self.scheduler, "halt_count", 0))
            refused, revision = self._capture_control_intent(generation)
            if refused is not None:
                return refused
            control_intent = (generation, revision)
        generation, revision = control_intent
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            refused = self._apply_refused(generation)
            if refused is not None:
                return refused
            try:
                if set(payload) != (set() if stop else {"profile_id"}):
                    raise ValueError("Choose one saved profile without device or schedule settings")
                if not stop:
                    if not isinstance(payload["profile_id"], str):
                        raise ValueError("Choose an existing saved profile")
                    self.helper_client.check_general_profile_support()
                elif self.repository.get_general_profile() is None:
                    return HTTPStatus.OK, "application/json", _json_bytes(
                        {"general_profile": self._general_profile_status()}
                    )
                if self.scheduler:
                    self.scheduler.sample_before_control_change(repository=self.repository)
                refused, current_revision = self._capture_control_intent(generation)
                if refused is not None:
                    return refused
                if revision is not None and current_revision != revision:
                    return self._control_rejected(HelperClientError(
                        "Network control changed before saving the profile", reason="control-changed"))

                def save_selection():
                    with self.repository.transaction():
                        self.repository.save_general_profile(None if stop else payload["profile_id"])
                        self._validate_saved_plan()

                accepted, _ = self._publish_apply(generation, save_selection)
                if not accepted:
                    return self._apply_refused(generation)
                if stop and self.repository.get_runtime_state().last_action == "reset":
                    # Everyone was not running. Stopping it starts nothing else.
                    return HTTPStatus.OK, "application/json", _json_bytes({
                        "general_profile": self._general_profile_status(),
                        "live_applied": False,
                        "message": "Everyone stopped. Nothing else started.",
                    })
            except HelperClientError as error:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": str(error)}
                )
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            status, content_type, body = self._apply_configuration_locked(
                sample_usage=False, explicit_apply=not stop, control_intent=control_intent)
            response = json.loads(body)
            response["general_profile"] = self._general_profile_status()
            if status != HTTPStatus.OK:
                response["error"] = "Selection saved but the network update failed. " + response["error"]
            return status, content_type, _json_bytes(response)

    def _address_access_state(self, record: AddressMove) -> str:
        """Read exact root ownership or an atomic completed-Undo receipt."""
        reader = getattr(self.helper_client, "dashboard_access", None)
        if reader is None:
            return "unknown"
        try:
            devices = reader("local-list")["devices"]
            if not isinstance(devices, list) or len(devices) > MAX_EFFECTIVE_ACCESS_DEVICES:
                return "unknown"
            # Move ownership lives only on saved grants. Protection grants follow protection.
            devices = [item for item in devices if item.get("manual", True)]
            old = next((item for item in devices if item["mac_address"] == record.old_mac), None)
            new = next((item for item in devices if item["mac_address"] == record.new_mac), None)
        except (HelperClientError, KeyError, TypeError):
            return "unknown"
        if not record.move_id:
            return "changed"
        if (
            old
            and old.get("replacement_id") == record.move_id
            and old.get("replaced_by") == record.new_mac
            and new
            and "replaced_by" not in new
            and new.get("owner_id") == record.move_id
        ):
            return "owned"
        if (
            old
            and "replaced_by" not in old
            and old.get("undo_id") == record.move_id
            and new is None
        ):
            return "undone"
        return "changed"

    @staticmethod
    def _address_recovery_response(record: AddressMove, error: str):
        return (
            HTTPStatus.CONFLICT,
            "application/json",
            _json_bytes(
                {
                    "error": error
                    + " Use the management connection to finish or inspect this move.",
                    "saved": record.phase == "saved",
                    "recovery_required": True,
                    "old_mac": record.old_mac,
                    "new_mac": record.new_mac,
                    "move_id": record.move_id,
                    "phase": record.phase,
                }
            ),
        )

    def _address_reservation(self, old: str, new: str, *, move_id: str = ""):
        return next(
            (
                move
                for move in self.repository.list_address_moves()
                if move.phase != "saved"
                and move.move_id != move_id
                and {move.old_mac, move.new_mac} & {old, new}
            ),
            None,
        )

    def _address_return_history(self, old: str, new: str, move_id: str) -> list:
        moves = [move for move in self.repository.list_address_moves() if move.move_id != move_id]
        if not any(move.old_mac == new for move in moves):
            return []
        component = {old, new}
        # At most 64 records can extend this component. No recursive history walks.
        for _ in moves:
            before = len(component)
            for move in moves:
                if {move.old_mac, move.new_mac} & component:
                    component.update((move.old_mac, move.new_mac))
            if len(component) == before:
                break
        superseded = [move for move in moves if {move.old_mac, move.new_mac} & component]
        if any(move.phase != "saved" for move in superseded):
            raise ValueError("Finish the pending move before returning to an earlier address.")
        return superseded

    def move_device_address_for_peer(self, old_mac: str, peer: str):
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            identity = self.dashboard_access(peer)
            new = normalize_exempt_mac(identity.get("mac_address"))
            old = normalize_exempt_mac(old_mac)
            existing = self.repository.get_address_move(old)
            if existing is not None and existing.phase != "saved":
                return self._address_recovery_response(existing, "An earlier move is unfinished.")
            if not self.is_address_move_source(identity, old):
                raise RequestError(
                    "That device cannot be moved to this one. Choose a saved "
                    "device that is not connected."
                )
            entries = self.helper_client.dashboard_access("local-list")["devices"]
            if not isinstance(entries, list) or len(entries) > MAX_EFFECTIVE_ACCESS_DEVICES:
                raise HelperClientError("Saved device access could not be verified.")
            # Only a saved grant moves with the helper. Protection moves in the database.
            entries = [item for item in entries if isinstance(item, dict) and item.get("manual", True)]
            access = not identity.get("allowed") and any(
                item.get("mac_address") == old and "replaced_by" not in item for item in entries
            )
            source_name = next(
                (item.get("name", "") for item in entries if item.get("mac_address") == old), ""
            )
            with self.repository.address_move_transaction():
                reserved = self._address_reservation(old, new)
                if reserved is not None:
                    return self._address_recovery_response(
                        reserved, "These addresses are reserved."
                    )
                self._address_return_history(old, new, "")
                record = AddressMove(
                    old,
                    new,
                    name=source_name,
                    moved_access=access,
                    moved_at=int(time.time()),
                    move_id=uuid.uuid4().hex,
                    phase="pending",
                    previous_protection_owner=(
                        self.repository.address_move_protection_owner(old) or ""
                    ),
                    prepared_version=self.repository.get_runtime_state().configuration_version,
                )
                self.repository.save_address_move(record)
            try:
                data = self.dashboard_access(
                    peer,
                    "move-address-owned",
                    mac_address=old,
                    move_id=record.move_id,
                    expected_mac=new,
                    move_access=record.moved_access,
                )
            except HelperClientError:
                return self._address_recovery_response(record, "The helper reply was uncertain.")
            if data.get("mac_address") != new:
                return self._address_recovery_response(record, "The verified connection changed.")
            status, content_type, body = self.move_device_address(
                old, new, access_moved=bool(data.get("moved")), prepared=record
            )
            result = json.loads(body)
            if result.get("saved") is True:
                allowed = bool(data.get("allowed"))
                if not allowed and (result.get("dashboard_access") or {}).get("synced"):
                    # Protection moved here and its grant was confirmed. Ask again.
                    try:
                        allowed = bool(self.dashboard_access(peer).get("allowed"))
                    except HelperClientError:
                        allowed = False
                return (
                    status,
                    content_type,
                    _json_bytes({**result, "allowed": allowed}),
                )
            current = self.repository.get_address_move(old)
            if current != record:
                return self._address_recovery_response(
                    current or record, "The saved move changed during the request."
                )
            if data.get("moved"):
                try:
                    self.dashboard_access(
                        peer, "undo-move-owned", mac_address=old, move_id=record.move_id
                    )
                except HelperClientError:
                    return self._address_recovery_response(
                        record, "Saving failed and access reversal is uncertain."
                    )
            # A concurrent edit or import invalidates the prepared decision. Keep its evidence.
            if self.repository.get_runtime_state().configuration_version != record.prepared_version:
                return self._address_recovery_response(record, "Settings changed during the move.")
            try:
                with self.repository.address_move_transaction():
                    if self.repository.get_address_move(old) != record:
                        raise StorageError("The recovery record changed.")
                    if (
                        self.repository.get_runtime_state().configuration_version
                        != record.prepared_version
                    ):
                        raise StorageError("Settings changed during recovery.")
                    self.repository.delete_address_move(old)
            except StorageError:
                return self._address_recovery_response(
                    record, "The move's recovery record could not be cleared."
                )
            return status, content_type, body

    def _active_access_entries(self) -> list:
        """The saved dashboard access entries that grant access, or nothing when unreadable."""

        reader = getattr(self.helper_client, "dashboard_access", None)
        if reader is None:
            return []
        try:
            devices = reader("local-list").get("devices", [])
        except (HelperClientError, AttributeError, TypeError):
            return []
        return [item for item in devices if isinstance(item, dict) and "replaced_by" not in item
                and item.get("manual", True)]

    @staticmethod
    def _address_move_summary(move: AddressMove) -> dict:
        return {
            "old_mac": move.old_mac,
            "new_mac": move.new_mac,
            "name": move.name or move.old_mac,
            "moved_access": move.moved_access,
            "moved_protection": move.moved_protection,
            "moved_at": move.moved_at,
            "recovery_required": move.phase != "saved",
        }

    def address_move_offer(self, identity: Dict[str, Any]) -> Dict[str, Any]:
        """Describe the requester's address and the saved devices it could take over.

        The identity comes from the helper. Nothing here reads a name or address from the browser.
        """

        none = {"offer": False, "current": None, "candidates": [], "replaced": []}
        if identity.get("network") != "test":
            if identity.get("network") == "management" and identity.get("allowed"):
                none["replaced"] = [
                    self._address_move_summary(move)
                    for move in self.repository.list_address_moves()
                ]
            return none
        try:
            current = normalize_exempt_mac(identity.get("mac_address"))
        except ValueError:
            return none
        registered = bool(identity.get("allowed"))
        protected_entries = {
            item.mac_address: item for item in self.repository.list_unaffected_devices()
        }
        protected = current in protected_entries
        private = int(current[1], 16) & 2 == 2
        result = {
            "offer": False,
            "current": {
                "mac_address": current,
                "private_address": private,
                "registered": registered,
                "protected": protected,
            },
            "candidates": [],
            "replaced": [],
        }
        if registered:
            result["replaced"] = [
                self._address_move_summary(move)
                for move in self.repository.list_address_moves()
                if move.new_mac == current
            ]
        if registered and protected or identity.get("can_register") is not True:
            # Nothing to take over, or the Pi could not verify this device so a move would be refused.
            return result
        # A device that is still connected under its own address is not an old address.
        connected = {station_mac(item) for item in self._station_devices()}
        candidates: Dict[str, Dict[str, Any]] = {}
        if not registered:
            for entry in self._active_access_entries():
                try:
                    mac = normalize_exempt_mac(entry.get("mac_address"))
                except ValueError:
                    continue
                candidates[mac] = {
                    "mac_address": mac,
                    "name": str(entry.get("name") or mac),
                    "registered": True,
                    "protected": False,
                }
        if not protected:
            for mac, entry in protected_entries.items():
                item = candidates.setdefault(
                    mac,
                    {
                        "mac_address": mac,
                        "name": entry.name,
                        "registered": False,
                        "protected": False,
                    },
                )
                item["protected"] = True
                if entry.display_name:
                    item["name"] = entry.display_name
        replaced_targets = {move.old_mac for move in self.repository.list_address_moves()}
        result["candidates"] = sorted(
            (
                item
                for mac, item in candidates.items()
                if mac != current and mac not in connected and mac not in replaced_targets
            ),
            key=lambda item: (item["name"].lower(), item["mac_address"]),
        )
        result["offer"] = bool(result["candidates"])
        return result

    def is_address_move_source(self, identity: Dict[str, Any], old_mac: str) -> bool:
        """Only a device the page could have offered may be moved, never one that is still connected."""

        offer = self.address_move_offer(identity)
        return any(item["mac_address"] == old_mac for item in offer["candidates"])

    def move_device_address(
        self,
        old_mac: str,
        new_mac: str,
        *,
        access_moved: bool,
        name: str = "",
        prepared: Optional[AddressMove] = None,
    ):
        """Commit protection and history together, then report the separate live outcome."""
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            try:
                old = normalize_exempt_mac(old_mac)
                new = normalize_exempt_mac(new_mac)
                record = prepared or AddressMove(
                    old,
                    new,
                    move_id=uuid.uuid4().hex,
                    previous_protection_owner=self.repository.address_move_protection_owner(old)
                    or "",
                    prepared_version=self.repository.get_runtime_state().configuration_version,
                )
                protected = {
                    item.mac_address: item for item in self.repository.list_unaffected_devices()
                }
                move_protection = old in protected and new not in protected
                if old == new or not (move_protection or access_moved):
                    raise ValueError("There is nothing to move for that device.")
                if move_protection:
                    name = protected[old].display_name
                    purge = {
                        str(station["ipv4_address"])
                        for station in self._station_devices()
                        if station_mac(station) == new and station.get("ipv4_address")
                    } | {
                        str(device.ipv4_address)
                        for device in self.repository.list_devices()
                        if device.mac_address == new
                    }
                    if self.scheduler:
                        self.scheduler.sample_before_control_change(repository=self.repository)
                with self.repository.address_move_transaction():
                    if prepared is not None and self.repository.get_address_move(old) != record:
                        raise ValueError("The prepared move changed before its save completed.")
                    if (
                        self.repository.get_runtime_state().configuration_version
                        != record.prepared_version
                    ):
                        raise ValueError(
                            "Settings changed during the move. Nothing was overwritten."
                        )
                    reserved = self._address_reservation(old, new, move_id=record.move_id)
                    if reserved is not None:
                        raise ValueError("These addresses have an unfinished move.")
                    superseded = self._address_return_history(old, new, record.move_id)
                    if move_protection:
                        blocking = old in self.repository.list_ad_blocking_devices()
                        self.repository.save_unaffected_device(
                            UnaffectedDevice(new, name, int(time.time()))
                        )
                        self.repository.claim_address_move_protection(new, record.move_id)
                        if blocking:
                            self.repository.set_ad_blocking(new, True)
                        self.repository.delete_unaffected_device(old)
                        self.repository.delete_domain_observations(purge, [new])
                    for prior in superseded:
                        self.repository.delete_address_move(prior.old_mac)
                    self.repository.save_address_move(
                        replace(
                            record,
                            name=name,
                            moved_access=access_moved,
                            moved_protection=move_protection,
                            phase="saved",
                        )
                    )
                    if move_protection:
                        self._validate_saved_plan()
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return (
                    HTTPStatus.CONFLICT,
                    "application/json",
                    _json_bytes({"error": str(error), "saved": False}),
                )
            fields = {
                "saved": True,
                "moved": {
                    "old_mac": old,
                    "new_mac": new,
                    "access": access_moved,
                    "protection": move_protection,
                },
            }
            if not move_protection:
                return HTTPStatus.OK, "application/json", _json_bytes(fields)
            return self._with_protected_access(self._address_saved_response(fields))

    def _address_saved_response(self, fields: dict):
        try:
            self._sync_ad_blocking()
            return self._saved_control_response(fields)
        except (HelperClientError, PlanError, StorageError, sqlite3.Error):
            return (
                HTTPStatus.BAD_GATEWAY,
                "application/json",
                _json_bytes(
                    {
                        **fields,
                        "live_applied": False,
                        "error": "Settings saved but the network update failed. Protection is not confirmed.",
                    }
                ),
            )

    def _check_address_protection_ownership(self, record: AddressMove) -> None:
        if record.moved_protection and (
            not record.move_id
            or self.repository.address_move_protection_owner(record.new_mac) != record.move_id
            or self.repository.address_move_protection_owner(record.old_mac) is not None
        ):
            raise ValueError(
                "Protection changed or its ownership cannot be proved. Nothing was overwritten."
            )

    def undo_device_address_move(self, old_mac: str, peer: str):
        """Undo only owned entries, retaining evidence until both stores agree."""
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            record = None
            try:
                record = self.repository.get_address_move(old_mac)
                if record is None:
                    return (
                        HTTPStatus.NOT_FOUND,
                        "application/json",
                        _json_bytes({"error": "There is no move to undo for that device."}),
                    )
                if not record.move_id:
                    raise ValueError(
                        "Legacy move ownership cannot be proved. Use management recovery."
                    )
                if any(
                    move.old_mac == record.new_mac for move in self.repository.list_address_moves()
                ):
                    raise ValueError("That device moved again. Undo the latest move first.")
                state = self._address_access_state(record) if record.moved_access else "unused"
                with self.repository.address_move_transaction():
                    if self.repository.get_address_move(record.old_mac) != record:
                        raise ValueError("The move changed before Undo started.")
                    reserved = self._address_reservation(
                        record.old_mac, record.new_mac, move_id=record.move_id
                    )
                    if reserved is not None:
                        return self._address_recovery_response(
                            reserved, "These addresses are reserved."
                        )
                    self._check_address_protection_ownership(record)
                    version = self.repository.get_runtime_state().configuration_version
                    if record.phase != "saved" and version != record.prepared_version:
                        raise ValueError(
                            "Settings changed since this move was prepared. Inspect its recovery evidence."
                        )
                    if record.moved_access:
                        if state != "owned" and not (record.phase != "saved" and state == "undone"):
                            raise ValueError(
                                "Dashboard access changed or its ownership cannot be proved."
                            )
                    if record.phase != "undoing":
                        record = replace(record, phase="undoing", prepared_version=version)
                        self.repository.save_address_move(record)
                if self.scheduler and record.moved_protection:
                    self.scheduler.sample_before_control_change(repository=self.repository)
                if record.moved_access and self._address_access_state(record) != "undone":
                    try:
                        self.dashboard_access(
                            peer,
                            "undo-move-owned",
                            mac_address=record.old_mac,
                            move_id=record.move_id,
                        )
                    except HelperClientError:
                        return self._address_recovery_response(
                            record, "The helper reversal reply was uncertain."
                        )
                if record.moved_access and self._address_access_state(record) != "undone":
                    return self._address_recovery_response(
                        record, "The helper reversal could not be proved."
                    )
                with self.repository.address_move_transaction():
                    current = self.repository.get_address_move(record.old_mac)
                    if current != record:
                        raise ValueError("The move changed before Undo completed.")
                    if (
                        self.repository.get_runtime_state().configuration_version
                        != record.prepared_version
                    ):
                        raise ValueError("Settings changed during Undo. Nothing was overwritten.")
                    self._check_address_protection_ownership(record)
                    if record.moved_protection:
                        blocking = record.new_mac in self.repository.list_ad_blocking_devices()
                        self.repository.delete_unaffected_device(record.new_mac)
                        self.repository.save_unaffected_device(
                            UnaffectedDevice(record.old_mac, record.name, int(time.time()))
                        )
                        if blocking:
                            self.repository.set_ad_blocking(record.old_mac, True)
                        if any(
                            move.move_id == record.previous_protection_owner
                            for move in self.repository.list_address_moves()
                        ):
                            self.repository.claim_address_move_protection(
                                record.old_mac, record.previous_protection_owner
                            )
                    self.repository.delete_address_move(record.old_mac)
                    if record.moved_protection:
                        self._validate_saved_plan()
            except (KeyError, TypeError, ValueError, StorageError) as error:
                if record is not None and record.phase != "saved":
                    return self._address_recovery_response(record, str(error))
                return (
                    HTTPStatus.CONFLICT,
                    "application/json",
                    _json_bytes({"error": str(error), "saved": False}),
                )
            fields = {
                "saved": True,
                "undone": {"old_mac": record.old_mac, "new_mac": record.new_mac},
            }
            if not record.moved_protection:
                return HTTPStatus.OK, "application/json", _json_bytes(fields)
            return self._with_protected_access(self._address_saved_response(fields))

    def _ad_blocking_payload(self) -> dict:
        service = {
            "available": False, "ready": False, "paused": False,
            "message": "Install the current Pi services to use ad blocking.",
            "query_history_saved": False,
        }
        read_status = getattr(self.helper_client, "ad_blocking_status", None)
        if read_status is not None:
            try:
                service = read_status()
            except HelperClientError:
                service["message"] = "Ad-blocking status could not be confirmed."
        if getattr(self, "_ad_blocking_sync_pending", False):
            service["ready"] = False
            service["message"] = "Saved ad-blocking choices could not be confirmed. Retry after reconnecting the Pi helper."
        return {
            "mac_addresses": list(self.repository.list_ad_blocking_devices()),
            "allowed_domains": list(self.repository.list_ad_blocking_allowances()),
            "service": service,
        }

    def _sync_ad_blocking(self, *, update=False, resume=False, control_revision=None):
        self._ad_blocking_sync_pending = True
        configure = getattr(self.helper_client, "configure_ad_blocking", None)
        selected = self.repository.list_ad_blocking_devices()
        if configure is None:
            if selected:
                raise HelperClientError("Install the current Pi services to use ad blocking.")
            self._ad_blocking_sync_pending = False
            return None
        if control_revision is None:
            reader = getattr(self.helper_client, "control_revision", None)
            if callable(reader):
                control_revision = reader()
        try:
            arguments = {"update": update, "resume": resume}
            if control_revision is not None:
                arguments["control_revision"] = control_revision
            result = configure(selected, self.repository.list_ad_blocking_allowances(), **arguments)
        except HelperClientError as error:
            if not selected and str(error) in {
                "unknown action", "Install the current Pi release to use ad blocking.",
            }:
                self._ad_blocking_sync_pending = False
                return None
            raise
        self._ad_blocking_sync_pending = False
        return result

    def _change_ad_blocking(self, payload: dict, *, control_intent=None):
        if control_intent is None:
            generation = getattr(self.scheduler, "apply_generation", getattr(self.scheduler, "halt_count", 0))
            refused, revision = self._capture_control_intent(generation)
            if refused is not None:
                return refused
            control_intent = (generation, revision)
        generation, revision = control_intent
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            refused, current_revision = self._capture_control_intent(generation)
            if refused is not None:
                return refused
            if revision is not None and current_revision != revision:
                return self._control_rejected(HelperClientError(
                    "Network control changed before saving ad choices", reason="control-changed"))
            try:
                fields = set(payload)
                if not fields or fields - {"mac_address", "enabled", "allowed_domains", "update_list"}:
                    raise ValueError("Choose a protected device or change the allowed domains")
                if ("mac_address" in fields) != ("enabled" in fields):
                    raise ValueError("Choose a protected device and whether to block ads")
                update = payload.get("update_list", False)
                if type(update) is not bool:
                    raise ValueError("The list update choice must be true or false")
                def save_choices():
                    with self.repository.transaction():
                        if "mac_address" in fields:
                            self.repository.set_ad_blocking(payload["mac_address"], payload["enabled"])
                        if "allowed_domains" in fields:
                            self.repository.set_ad_blocking_allowances(payload["allowed_domains"])

                accepted, _ = self._publish_apply(generation, save_choices)
                if not accepted:
                    return self._apply_refused(generation)
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            try:
                service = self._sync_ad_blocking(
                    update=update, resume=payload.get("enabled") is True, control_revision=revision,
                )
            except HelperClientError as error:
                refused = self._control_rejected(error)
                if refused is not None:
                    return refused
                service = None
            if service is None:
                message = "Saved but ad blocking could not be confirmed. Start saved settings after reconnecting the Pi helper."
            elif service.get("paused"):
                message = "Saved. Ad blocking is paused after Reset or Panic. Start saved settings to resume."
            elif not self.repository.list_ad_blocking_devices():
                message = (
                    "Saved. The ad list will update when Block ads is enabled for a device."
                    if update else "Saved. Ad blocking is off."
                )
            elif service.get("ready"):
                message = "Ad-blocking settings applied."
            else:
                message = "Saved. Ad blocking will start when the domain list and DNS service are ready."
            return HTTPStatus.OK, "application/json", _json_bytes({
                "ad_blocking": self._ad_blocking_payload(), "message": message,
            })

    def _change_unaffected_device(self, payload: dict, *, remove: bool):
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            try:
                item = UnaffectedDevice(
                    payload["mac_address"], payload.get("display_name", ""), int(time.time())
                )
                purge = set()
                if not remove:
                    purge = {
                        str(station["ipv4_address"])
                        for station in self._station_devices()
                        if station_mac(station) == item.mac_address
                        and station.get("ipv4_address")
                    } | {
                        str(device.ipv4_address)
                        for device in self.repository.list_devices()
                        if device.mac_address == item.mac_address
                    }
                if self.scheduler:
                    self.scheduler.sample_before_control_change(repository=self.repository)
                with self.repository.transaction():
                    if remove:
                        if not self.repository.delete_unaffected_device(item.mac_address):
                            return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                                {"error": f"{item.mac_address} is not on the protected list"}
                            )
                    else:
                        self.repository.save_unaffected_device(item)
                        self.repository.delete_domain_observations(purge, [item.mac_address])
                    self._validate_saved_plan()
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            # Traffic protection first, then the dashboard access that follows from it.
            return self._with_protected_access(self._address_saved_response(
                {
                    "devices": [
                        asdict(device) for device in self.repository.list_unaffected_devices()
                    ]
                }
            ))

    def _save_connected_group(self, payload: dict, *, existing: bool):
        if existing and "group_id" in payload:
            try:
                _text_identifier(payload["group_id"], "group_id")
            except ValueError as error:
                return _bad_request("invalid group request", error)
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            try:
                if existing:
                    group = self.repository.get_device_group(payload["group_id"])
                    if group is None:
                        raise ValueError("The selected group no longer exists")
                    connected = [payload]
                    device_ids = list(group.device_ids)
                else:
                    connected = payload.get("connected_clients", [])
                    device_ids = list(payload.get("device_ids", []))
                    group = None
                if not isinstance(connected, list) or len(connected) > 15:
                    raise ValueError("Select at most 15 connected clients")
                snapshot = station_snapshot(self.helper_client) if connected else None
                if self.scheduler:
                    self.scheduler.sample_before_control_change(repository=self.repository)
                with self.repository.transaction():
                    profile_id = group.profile_id if existing else payload.get("profile_id") or None
                    protected_macs = {
                        item.mac_address for item in self.repository.list_unaffected_devices()
                    }
                    for client in connected:
                        mac = normalize_exempt_mac(client["mac_address"])
                        if mac in protected_macs:
                            raise ValueError(
                                "This device is protected from profiles. Stop protecting it before adding it to a group."
                            )
                        saved = self.repository.get_device_by_mac_address(mac)
                        base_profile = saved.profile_id if saved else profile_id
                        if base_profile is None:
                            raise ValueError(
                                "Choose a group profile before adding an unassigned client"
                            )
                        device, saved_row = _connected_device_from_payload(
                            {**client, "profile_id": base_profile},
                            self.repository,
                            hostname=_snapshot_hostname(snapshot, client["mac_address"]),
                        )
                        self._verify_selected_client(device, snapshot)
                        self.repository.save_device(
                            device, expected=saved_row, create_only=saved_row is None
                        )
                        if device.id not in device_ids:
                            device_ids.append(device.id)
                    group = DeviceGroup(
                        id=group.id if existing else payload["id"],
                        name=group.name if existing else payload["name"],
                        device_ids=tuple(device_ids),
                        profile_id=profile_id,
                    )
                    for prior in self.repository.list_device_groups():
                        if prior.id != group.id and set(prior.device_ids) & set(group.device_ids):
                            raise ValueError(
                                f"A selected device already belongs to {prior.name}. Remove it from that group first."
                            )
                    self.repository.save_device_group(group)
                    self._validate_saved_plan()
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            return self._saved_control_response(
                {
                    "group": _device_group_payload(
                        group,
                        repository=self.repository,
                        labels_by_mac=self.repository.list_connected_device_labels(),
                    )
                },
                snapshot,
            )

    def _base_profile_feedback(self, device: Device, *, everyone_paused: bool = False) -> str:
        saved_profile = self.repository.get_profile(device.profile_id)
        profile_label = saved_profile.name if saved_profile is not None else device.profile_id
        general = None if everyone_paused else self.repository.get_general_profile()
        if general is not None:
            return (
                f"Profile {profile_label} saved for this device. Everyone uses {general.name}, which "
                "takes priority while profiles are on. Stop it to use the saved device profile."
            )
        now = int(time.time())
        group = next(
            (item for item in self.repository.list_device_groups() if device.id in item.device_ids),
            None,
        )
        data_limit = self.repository.get_data_limit(device.id)
        group_limit = self.repository.get_group_data_limit(group.id) if group else None
        progression = next(
            (
                item
                for item in self.repository.list_active_progressions(now=now)
                if item.device_id == device.id
            ),
            None,
        )
        timer = next(
            (
                item
                for item in self.repository.list_active_timers(now=now)
                if item.device_id == device.id
            ),
            None,
        )
        schedule = active_schedule(
            schedules=self.repository.list_schedules(),
            device_id=device.id,
            moment=datetime.fromtimestamp(now),
        )
        if data_limit and data_limit.enabled and data_limit.used_bytes >= data_limit.limit_bytes:
            current = f"The device data limit currently uses {data_limit.profile_id}."
        elif (
            group_limit
            and group_limit.enabled
            and group_limit.used_bytes >= group_limit.limit_bytes
        ):
            current = (
                f"The shared data limit for {group.name} currently uses {group_limit.profile_id}."
            )
        elif progression:
            current = f"Progression {progression.id} is currently active."
        elif timer:
            current = f"Timer {timer.id} currently uses {timer.profile_id}."
        elif schedule:
            current = f"Schedule {schedule.id} currently uses {schedule.profile_id}."
        elif group and group.profile_id:
            current = f"Group {group.name} currently uses {group.profile_id}."
        else:
            current = "This is the current device profile."
        return f"Profile {profile_label} saved for this device. " + current

    # Counts Reset and Panic requests and those still running, so a device Apply can
    # tell that one arrived before it or runs while it waits.
    _reset_requests = 0
    _resets_pending = 0
    # Waits before each retry of a failed device-only cleanup. Retries stop after the last.
    cleanup_retry_delays = (2.0, 5.0, 15.0, 30.0, 60.0)
    _cleanup_worker = None

    def note_reset_request(self) -> None:
        with _RESET_REQUESTS_LOCK:
            self._reset_requests += 1
            self._resets_pending += 1

    def note_reset_finished(self) -> None:
        with _RESET_REQUESTS_LOCK:
            self._resets_pending -= 1

    def _apply_profile_to_connected_device(self, payload: dict, *, control_intent=None,
                                           resets_at_entry=None):
        """Save one device's profile and start it for that device.

        A running saved plan is applied again as before. After a Reset only this
        device starts: Everyone, other saved devices, everyone else, service
        lists and ad-blocking stay stopped until Start or Apply to everyone. While
        Everyone runs, this device leaves it and runs its own profile. Everyone keeps
        running for every other device.
        """

        if self.helper_client is None:
            return (
                HTTPStatus.SERVICE_UNAVAILABLE,
                "application/json",
                _json_bytes({"error": "network helper is not configured"}),
            )
        if control_intent is None:
            generation = getattr(self.scheduler, "apply_generation", getattr(self.scheduler, "halt_count", 0))
            refused, revision = self._capture_control_intent(generation)
            if refused is not None:
                return refused
            control_intent = (generation, revision)
        if resets_at_entry is None:
            resets_at_entry = (self._reset_requests, self._resets_pending)
        overtaken = (HTTPStatus.CONFLICT, "application/json", _json_bytes({
            "error": "Reset or Panic ran while this Apply waited. Nothing was saved or "
                     "started. Apply the profile again if you still want it.",
        }))
        if resets_at_entry[1] > 0:
            return overtaken
        operation = self.scheduler.exclusive() if self.scheduler else nullcontext()
        with operation:
            refused = self._apply_refused(control_intent[0])
            if refused is not None:
                return refused
            if self._reset_requests != resets_at_entry[0]:
                return overtaken
            try:
                snapshot = station_snapshot(self.helper_client)
                device, existing = _connected_device_from_payload(
                    payload,
                    self.repository,
                    hostname=_snapshot_hostname(snapshot, payload.get("mac_address")),
                )
                if device.mac_address in {
                    item.mac_address for item in self.repository.list_unaffected_devices()
                }:
                    raise ValueError(
                        "This device is protected from profiles. Stop protecting it before applying a profile."
                    )
                self._verify_selected_client(device, snapshot)
                last_action = self.repository.get_runtime_state().last_action
                stopped = last_action == "reset"
                general = self.repository.get_general_profile()
                beside_everyone = False
                if general is None and not stopped and self.scheduler is not None:
                    applied = self.scheduler.applied_plan()
                    if applied is not None and applied.general_profile is not None:
                        # A Stop for everyone that was saved but failed. Applying the saved
                        # settings now would finish it and start every paused device setting.
                        raise ValueError(
                            "Everyone still runs because Stop for everyone did not finish. "
                            "Nothing was saved. Choose Start to finish it, then apply this "
                            "profile."
                        )
                if general is not None and not stopped:
                    if not self._everyone_runs():
                        # An Everyone that does not run, for example after a failed Start,
                        # would start for every device as a side effect of this Apply.
                        raise ValueError(
                            f"Everyone is saved as {general.name} but is not running. "
                            "Nothing was saved. Choose Start to run it, Reset network first to "
                            "start only this device, or stop Everyone."
                        )
                    # Everyone runs. This device leaves it for its own profile.
                    beside_everyone = True
                if stopped or beside_everyone:
                    stop_state = self._stop_state()
                    if stop_state != "clear":
                        raise ValueError(
                            "Panic or a router outage stopped profiles. Nothing was saved. Choose "
                            "Start when it is safe, then apply this profile."
                            if stop_state == "latched" else
                            "The Pi did not say whether Panic or an outage stopped profiles. "
                            "Nothing was saved. Try again in a moment."
                        )
                previous_scope = self.repository.get_device_start_scope() or frozenset()
                previous_exceptions = self.repository.get_everyone_exceptions()
                # What runs now, for the case that this start fails at the helper.
                ran_before = None
                if beside_everyone and self.scheduler is not None:
                    applied, sources = self.scheduler.applied_snapshot()
                    if applied is not None:
                        ran_before = (applied, sources, self.scheduler.roll_snapshot())
                if self.scheduler:
                    self.scheduler.sample_before_control_change(repository=self.repository)
                # Refuse a Panic or a changed helper intent before saving, on both paths.
                refused, current_revision = self._capture_control_intent(control_intent[0])
                if refused is not None:
                    return refused
                if control_intent[1] is not None and current_revision != control_intent[1]:
                    return self._control_rejected(HelperClientError(
                        "Network control changed before saving the device profile",
                        reason="control-changed"))

                def save():
                    with self.repository.transaction():
                        self.repository.save_device(
                            device, expected=existing, create_only=existing is None
                        )
                        self._validate_saved_plan()
                        if stopped:
                            self.repository.add_device_to_start_scope(device.id)
                            # The scoped plan must compile before the device joins it.
                            with without_dns():
                                compile_stored_plan(
                                    repository=self.repository,
                                    device_scope=previous_scope | {device.id},
                                )
                        elif beside_everyone:
                            self.repository.add_everyone_exception(device.id)
                            # The classes and the plan with Everyone and this device must
                            # fit before anything is saved.
                            self._validate_saved_plan()

                # Panic cannot land between the last check and the commit.
                accepted, _ = self._publish_apply(control_intent[0], save)
                if not accepted:
                    return self._apply_refused(control_intent[0]) or overtaken
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            fields = {
                "device": _device_payload(
                    device, labels_by_mac=self.repository.list_connected_device_labels()
                ),
                "message": self._base_profile_feedback(
                    device, everyone_paused=stopped or beside_everyone),
            }
            if beside_everyone:
                return self._start_beside_everyone(
                    fields, snapshot, device, general, control_intent, previous_exceptions,
                    ran_before, existing)
            if not stopped:
                return self._saved_control_response(fields, snapshot)
            return self._start_device_only(fields, snapshot, device, control_intent, previous_scope)

    def _everyone_runs(self) -> bool:
        """Whether the helper really runs the saved Everyone profile right now.

        A saved Everyone that never started, or whose apply failed, does not run. A device
        Apply must not start it for every other device as a side effect. A saved change
        that waits for its apply leaves the state unknown, so the applied plan decides.
        """

        if self.repository.get_general_profile() is None:
            return False
        state = self.repository.get_runtime_state().observed_state
        if self.scheduler is None:
            return state == "applied"
        if state not in ("applied", "unknown"):
            return False
        # The profile that runs must be the one saved. After a failed Apply to all the old
        # one still runs, and this Apply must not switch every other device to the new one.
        applied = self.scheduler.applied_plan()
        saved = self.repository.get_general_profile()
        return (
            applied is not None
            and applied.general_profile is not None
            and applied.general_profile.profile_id == saved.id
            # An edit of the saved profile that never reached the network is not running.
            and self.scheduler.applied_general_profile() == saved
        )

    def _stop_state(self) -> str:
        """Whether a Panic, outage or state error keeps profiles stopped: latched, clear or unknown."""

        try:
            gateway_status = self.helper_client.gateway_status()
        except (HelperClientError, AttributeError, TypeError):
            return "unknown"
        if not isinstance(gateway_status, dict):
            return "unknown"
        latched = any(gateway_status.get(name) is True for name in (
            "outage_stop_latched", "panic_stop_latched", "state_error"))
        return "latched" if latched else "clear"

    def _start_beside_everyone(self, fields, snapshot, device, general, control_intent,
                               previous_exceptions, ran_before=None, existing=None):
        """Apply Everyone for all and this device's own profile. Everyone is not cleared."""

        status, content_type, body = self._apply_configuration_locked(
            connected_snapshot=snapshot, sample_usage=False, control_intent=control_intent,
            keep_exceptions=True,
        )
        response = json.loads(body)
        response.update(fields)
        response["live_applied"] = status == HTTPStatus.OK
        if status == HTTPStatus.OK:
            response["message"] = (
                fields["message"]
                + f" Started for {device.name} while Everyone keeps using {general.name} "
                "for every other device. Start or Apply to all puts this device back on Everyone."
            )
            response["state"] = self._runtime_state_with_running()
            return status, content_type, _json_bytes(response)

        # The device did not start, so nothing changes: it keeps the assignment it had, or
        # none, and stays with Everyone, and the earlier exceptions stay. Saved and running
        # then agree, so a later Apply or retry never installs this failed choice. The check
        # and the restore are one step under the Panic fence: a Panic that ended the
        # exceptions is never undone.
        def restore():
            failed = self.repository.get_runtime_state()
            if failed.last_action not in ("apply", "schedule"):
                return False
            try:
                with self.repository.transaction():
                    if existing is None:
                        self.repository.delete_device(device.id)
                    else:
                        self.repository.save_device(existing, expected=device)
                    self.repository.restore_everyone_exceptions(previous_exceptions)
                    # Saving marks the state unknown. The failure stays what it was.
                    self.repository.update_runtime_state(
                        observed_state=failed.observed_state, last_action=failed.last_action,
                        last_result=failed.last_result, last_error=failed.last_error,
                        keep_everyone_exceptions=bool(previous_exceptions),
                    )
            except StorageError:
                LOGGER.exception("could not put back the device assignment after a failed start")
                self.repository.restore_everyone_exceptions(previous_exceptions)
            return True

        accepted, restored = self._publish_apply(control_intent[0], restore)
        note = ""
        if status == HTTPStatus.BAD_GATEWAY and accepted and restored and ran_before is not None:
            # A failed apply may have removed the rules that ran. Put back the plan that ran,
            # not the saved settings: the device's new profile may be what failed.
            put_back, _, _ = self._apply_configuration_locked(
                connected_snapshot=snapshot, sample_usage=False, control_intent=control_intent,
                keep_exceptions=True, restore_plan=ran_before,
            )
            note = (
                " What ran before was put back."
                if put_back == HTTPStatus.OK else
                " What ran before could not be put back, so the dashboard keeps trying."
            )
        response["error"] = (
            "The profile did not start, so nothing changed: the device keeps what it had and "
            "Everyone keeps running for every device. " + response.get("error", "") + note
        ).strip()
        return status, content_type, _json_bytes(response)

    def _start_device_only(self, fields, snapshot, device, control_intent, previous_scope):
        """Start the device-only scope after a Reset. Everything else stays stopped."""

        status, content_type, body = self._apply_configuration_locked(
            connected_snapshot=snapshot, sample_usage=False, control_intent=control_intent,
            device_scope=True,
        )
        if status != HTTPStatus.OK and self.repository.get_device_start_scope() is not None:
            # Refused before the helper ran: the scope stays what it was.
            self.repository.restore_device_start_scope(previous_scope)
        response = json.loads(body)
        response.update(fields)
        response["live_applied"] = status == HTTPStatus.OK
        if status == HTTPStatus.OK:
            others = len(self.repository.get_device_start_scope() or ()) - 1
            started = (
                f" Started for {device.name} only." if others <= 0 else
                f" Started for {device.name} too, beside {others} "
                f"{'device' if others == 1 else 'devices'} already started on their own."
            )
            response["message"] = (
                fields["message"] + started
                + " Everyone and other saved settings stay paused. Start resumes all saved settings."
            )
        else:
            response["error"] = (
                "The profile is saved for this device but it did not start. Other saved "
                "settings stay stopped. " + response.get("error", "")
            ).strip()
        return status, content_type, _json_bytes(response)

    def _quota_counter(self, kind: str, identifier: str) -> Optional[int]:
        """Read one current helper counter for a manual quota reset."""

        if self.helper_client is None:
            return None
        traffic_counters = getattr(self.helper_client, "traffic_counters", None)
        if traffic_counters is None:
            return None
        try:
            counters = traffic_counters()
        except HelperClientError:
            LOGGER.warning("could not read quota counter before reset", exc_info=True)
            return None
        values = counters.get(kind, {})
        value = values.get(identifier) if isinstance(values, dict) else None
        return value if isinstance(value, int) and value >= 0 else None

    def _with_counter_baselines(self, settings: SavedSettings) -> SavedSettings:
        """Count imported limits from the helper's current counters, not from zero."""

        traffic_counters = getattr(self.helper_client, "traffic_counters", None)
        if traffic_counters is None:
            return settings
        try:
            counters = traffic_counters()
        except HelperClientError:
            LOGGER.warning("could not read quota counters before an import", exc_info=True)
            return settings
        devices = counters.get("devices", {})
        services = counters.get("services", {})
        members = {group.id: group.device_ids for group in settings.groups}

        def counter(values: Any, key: str) -> int:
            value = values.get(key) if isinstance(values, dict) else None
            return value if isinstance(value, int) and value >= 0 else 0

        return replace(
            settings,
            data_limits=tuple(
                replace(limit, counter_bytes=counter(devices, limit.device_id))
                for limit in settings.data_limits
            ),
            group_data_limits=tuple(
                replace(
                    limit,
                    counter_bytes=sum(
                        counter(devices, device_id)
                        for device_id in members.get(limit.group_id, ())
                    ),
                )
                for limit in settings.group_data_limits
            ),
            service_quotas=tuple(
                replace(quota, counter_bytes=counter(services, quota.id))
                for quota in settings.service_quotas
            ),
        )

    def _group_quota_counter(self, group_id: str) -> Optional[int]:
        """Return the current aggregate device counter for one group."""

        if self.helper_client is None:
            return None
        group = self.repository.get_device_group(group_id)
        if group is None:
            return None
        eligible_ids = (
            self.scheduler.counter_device_ids(repository=self.repository)
            if self.scheduler
            else {
                device.id
                for device in eligible_clients(
                    self.repository,
                    station_snapshot(self.helper_client)
                    if self.repository.list_unaffected_devices()
                    else None,
                ).devices
            }
        )
        member_ids = tuple(device_id for device_id in group.device_ids if device_id in eligible_ids)
        try:
            counters = self.helper_client.traffic_counters().get("devices", {})
        except HelperClientError:
            LOGGER.warning("could not read group quota counter before reset", exc_info=True)
            return None
        if not isinstance(counters, dict) or any(
            device_id not in counters for device_id in member_ids
        ):
            return None
        values = [counters[device_id] for device_id in member_ids]
        return (
            sum(values) if all(isinstance(value, int) and value >= 0 for value in values) else None
        )

    def _start_scenario(self, payload: Dict[str, Any]) -> Tuple[int, str, bytes]:
        """Start playback only when the network is already applied."""

        try:
            scenario = self.repository.get_scenario(_required_text(payload, "scenario_id"))
            if scenario is None:
                raise ValueError("scenario does not exist")
            run = ScenarioRun(
                id=f"run-{time.time_ns()}",
                scenario_id=scenario.id,
                started_at=int(time.time()),
                device_id=payload.get("device_id"),
                group_id=payload.get("group_id"),
                everyone_else=payload.get("everyone_else", False),
            )
            if run.device_id is not None and self.repository.get_device(run.device_id) is None:
                raise ValueError("device does not exist")
            if (
                run.group_id is not None
                and self.repository.get_device_group(run.group_id) is None
            ):
                raise ValueError("group does not exist")
        except (KeyError, TypeError, ValueError) as error:
            return _bad_request("invalid scenario start", error)
        if self.helper_client is None or self.scheduler is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "Starting a scenario requires hardware mode."}
            )
        if (
            self.repository.get_runtime_state().last_action == "reset"
            or self.scheduler.applied_plan() is None
        ):
            return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                {"error": "Choose Start before starting a scenario."}
            )
        if run.everyone_else and not self._everyone_else_counted():
            capacity_error = self._capacity_error(adding=1)
            if capacity_error is not None:
                return capacity_error
        self.repository.start_scenario_run(run)
        status, content_type, body = self._apply_after_change()
        response = json.loads(body)
        if status != HTTPStatus.OK or response.get("live_applied") is False:
            # Do not let a failed Start count down or resume during recovery.
            self.repository.stop_scenario_run(run.id)
            response["live_applied"] = False
            response["message"] = "Scenario did not start. Choose Start, then start it again."
            if status == HTTPStatus.OK:
                status = HTTPStatus.CONFLICT
                response["error"] = "Choose Start before starting a scenario."
        else:
            response["run"] = asdict(run)
            response["live_applied"] = True
        return status, content_type, _json_bytes(response)

    def _apply_saved_change(
        self,
        key: str,
        value: Any,
    ) -> Tuple[int, str, bytes]:
        """Return a saved change and apply it immediately when hardware is available."""

        if key in ("imported", "restored") and self.helper_client is not None:
            try:
                self._sync_ad_blocking()
            except HelperClientError:
                return HTTPStatus.OK, "application/json", _json_bytes({
                    key: value, "live_applied": False,
                    "message": "Settings saved but ad-blocking choices could not be confirmed. Start saved settings after reconnecting the Pi helper.",
                })
        if self.helper_client is None:
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes(
                    {
                        key: value,
                        "live_applied": False,
                        "message": "settings saved; choose Start in hardware mode to start them",
                    }
                ),
            )
        status, content_type, body = self._apply_after_change()
        response = json.loads(body.decode("utf-8"))
        response[key] = value
        response.setdefault("live_applied", status == HTTPStatus.OK)
        return status, content_type, _json_bytes(response)

    def newly_protected_station_addresses(self, settings: SavedSettings) -> set:
        """Return the current addresses of clients that settings about to be saved protect.

        Stored DNS observations of those clients go in the same transaction as the new
        settings. Saved devices and recorded hardware addresses are matched by the
        storage itself. This adds connected clients that are not saved devices.
        """

        newly = {item.mac_address for item in settings.unaffected_devices} - {
            item.mac_address for item in self.repository.list_unaffected_devices()
        }
        if not newly:
            return set()
        return {
            str(station["ipv4_address"])
            for station in self._station_devices()
            if station_mac(station) in newly and station.get("ipv4_address")
        }

    def _everyone_else_counted(self) -> bool:
        """Return whether everyone else already holds a traffic class."""

        # One class serves the saved profile, every schedule and a running
        # scenario, whichever applies.
        return (
            self.repository.get_everyone_else_profile_id() is not None
            or any(
                schedule.everyone_else and schedule.enabled
                for schedule in self.repository.list_schedules()
            )
            or any(
                run.everyone_else
                for run, _, _ in active_scenario_runs(self.repository, now=time.time())
            )
        )

    def _capacity_error(self, *, adding: int) -> Optional[Tuple[int, str, bytes]]:
        """Refuse a change that needs more traffic classes than the kernel allows."""

        in_use = (
            len(self.repository.list_devices())
            + sum(quota.enabled for quota in self.repository.list_service_quotas())
            + sum(quota.enabled for quota in self.repository.list_time_quotas())
            + sum(assignment.enabled for assignment in self.repository.list_service_assignments())
            + self._everyone_else_counted()
        )
        if in_use + adding <= MAX_SHAPED_MARKS_PER_PLAN:
            return None
        return HTTPStatus.CONFLICT, "application/json", _json_bytes(
            {
                "error": (
                    f"JitterSim can shape at most {MAX_SHAPED_MARKS_PER_PLAN} devices and "
                    "service rules together; delete one first"
                )
            }
        )

    def _apply_after_change(self) -> Tuple[int, str, bytes]:
        """Re-apply the plan for a saved change unless the network was reset."""

        scoped = False
        if self.repository.get_runtime_state().last_action == "reset":
            # Only an explicit Apply may undo a Reset. A device-only start keeps
            # applying its own devices; the scope is read again under the lock.
            scoped = self.repository.get_device_start_scope() is not None
            if not scoped:
                return HTTPStatus.OK, "application/json", _json_bytes(_RESET_SAVED_ONLY)
        if self.helper_client is not None:
            try:
                gateway_status = self.helper_client.gateway_status()
            except HelperClientError:
                gateway_status = None
            if gateway_status and (
                gateway_status.get("outage_stop_latched") is True
                or gateway_status.get("panic_stop_latched") is True
                or gateway_status.get("state_error") is True
            ):
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "live_applied": False,
                        "message": (
                            "saved; automatic recovery is paused. Check gateway status "
                            "then choose Start when it is safe"
                        ),
                    }
                )
        # A saved change applies again what runs. Devices that run their own profile
        # beside Everyone keep doing so.
        return self._apply_configuration(device_scope=scoped, keep_exceptions=True)

    def _apply_configuration(self, *, explicit_apply: bool = False, control_intent=None,
                             device_scope: bool = False,
                             keep_exceptions: bool = False) -> Tuple[int, str, bytes]:
        if control_intent is None:
            generation = getattr(self.scheduler, "apply_generation", getattr(self.scheduler, "halt_count", 0))
            refused, revision = self._capture_control_intent(generation)
            if refused is not None:
                return refused
            control_intent = (generation, revision)
        # Scheduled applies, manual applies and resets never interleave.
        operation = self.scheduler.exclusive() if self.scheduler is not None else nullcontext()
        with operation:
            return self._apply_configuration_locked(
                explicit_apply=explicit_apply, control_intent=control_intent,
                device_scope=device_scope, keep_exceptions=keep_exceptions)

    @recorded("apply", "dashboard")
    def _apply_configuration_locked(
        self, connected_snapshot=None, *, sample_usage=True, explicit_apply: bool = False, control_intent=None,
        device_scope: bool = False, keep_exceptions: bool = False, restore_plan=None,
    ) -> Tuple[int, str, bytes]:
        """Apply the whole saved plan, or with `device_scope` only a device-only start.

        `restore_plan` is a plan and its source descriptions that ran before: a failed
        apply puts exactly that back, not what the saved settings say now.

        A device-only start is read here, under the shared lock, so a Reset that ran
        while this call waited leaves nothing to start. Start and Apply to everyone cover
        every device. A saved change passes `keep_exceptions`, so the devices that run
        their own profile beside Everyone keep doing so. They are read here too.
        """
        if self.helper_client is None:
            return (
                HTTPStatus.SERVICE_UNAVAILABLE,
                "application/json",
                _json_bytes({"error": "network helper is not configured"}),
            )
        if control_intent is None:
            halts_at_start = getattr(self.scheduler, "apply_generation", getattr(self.scheduler, "halt_count", 0))
            refused, control_revision = self._capture_control_intent(halts_at_start)
        else:
            halts_at_start, control_revision = control_intent
            refused = self._apply_refused(halts_at_start)
        if refused is not None:
            return refused
        scope = None
        if device_scope:
            if explicit_apply:
                raise ValueError("a device-only start is never an explicit Start")
            scope = self.repository.get_device_start_scope()
            if scope is None:
                return HTTPStatus.OK, "application/json", _json_bytes(_RESET_SAVED_ONLY)
        # A device-only start stays a Reset for everything outside its devices.
        runtime_action = "reset" if scope is not None else "apply"
        keep = {"keep_device_scope": True} if scope is not None else {}
        planning = {}
        applied_keep = keep
        if scope is not None:
            planning["device_scope"] = scope
        else:
            if self.repository.everyone_exception_rows():
                # The devices that run beside Everyone stay recorded until the helper accepted
                # the new plan, whichever action asked for it. A failed Start, Apply to all
                # or Stop keeps the running policy, and the usage of those devices is still
                # sampled before the counters restart. Only a plan without them ends them.
                keep = {"keep_everyone_exceptions": True}
            if self.repository.get_general_profile() is not None:
                # Everyone is saved: name the devices beside it, or none for a whole-scope plan.
                exceptions = (
                    self.repository.get_everyone_exceptions() if keep_exceptions else frozenset()
                )
                planning["everyone_exceptions"] = exceptions
                if exceptions:
                    applied_keep = keep
            if restore_plan is not None and keep:
                # The plan that ran comes back with the record that went with it. The next
                # accepted plan of the saved settings decides whether they still count.
                applied_keep = keep
        # A whole-scope update while devices run beside Everyone: if it fails at the helper the
        # plan that ran is applied again, as for a failed device Apply.
        ran_before = None
        if (scope is None and not keep_exceptions and restore_plan is None
                and self.scheduler is not None and self.repository.everyone_exception_rows()):
            applied, sources = self.scheduler.applied_snapshot()
            if applied is not None:
                ran_before = (applied, sources, self.scheduler.roll_snapshot())
        try:
            if restore_plan is not None:
                # The randomization state of the plan that ran comes back with it.
                if self.scheduler is not None:
                    self.scheduler.restore_rolls(restore_plan[2])
                prepared = (
                    PreparedRuntimePlan(plan=restore_plan[0], states=dict(restore_plan[1] or {}))
                    if self.scheduler is not None else None
                )
            else:
                prepared = (
                    self.scheduler.prepare_plan(
                        repository=self.repository, connected_snapshot=connected_snapshot,
                        **planning,
                    )
                    if self.scheduler is not None else None
                )
            plan = (
                restore_plan[0]
                if restore_plan is not None
                else prepared.plan
                if prepared is not None
                else compile_stored_plan(
                    repository=self.repository,
                    connected_snapshot=(
                        connected_snapshot
                        if connected_snapshot is not None
                        else station_snapshot(self.helper_client)
                    ),
                    **planning,
                )
            )
        except (PlanError, StorageError):
            return (
                HTTPStatus.CONFLICT,
                "application/json",
                _json_bytes({"error": "stored configuration cannot be applied"}),
            )
        accepted, _ = self._publish_apply(halts_at_start, lambda: self.repository.update_runtime_state(
            observed_state="applying", last_action=runtime_action, last_result=None, last_error=None,
            **keep,
        ))
        if not accepted:
            return self._apply_refused(halts_at_start)
        if self.scheduler is not None and sample_usage:
            # Applying recreates the helper counters, so record their traffic first.
            self.scheduler.sample_usage(repository=self.repository)
        refused = self._apply_refused(halts_at_start)
        if refused is not None:
            return refused
        try:
            arguments = {"plan": plan}
            if control_revision is not None:
                arguments["control_revision"] = control_revision
            if explicit_apply:
                arguments["explicit_apply"] = True
            self._sync_ad_blocking(control_revision=control_revision)
            result = self.helper_client.apply(**arguments)
        except HelperClientError as error:
            rejected = self._control_rejected(error)
            if rejected is not None:
                return rejected
            LOGGER.error("network helper apply failed: %s", error)
            # A household release that left a restriction in force carries the
            # helper's own text, which names it and asks for a retry (review S1).
            # Every other failure keeps the generic text, which hides internals.
            failure = (str(error) if error.reason == "release-incomplete"
                       else "network helper apply failed")
            if self.scheduler is not None:
                self.scheduler.invalidate_applied_plan()
            if scope is not None:
                # A failed device-only start ends its scope and resets what it left behind.
                accepted, state = self._clean_up_failed_device_start(halts_at_start)
            else:
                accepted, state = self._publish_apply(halts_at_start, lambda: self.repository.update_runtime_state(
                    observed_state="error", last_action=runtime_action, last_result="failure",
                    last_error=failure, **keep,
                ))
            if not accepted:
                return self._apply_refused(halts_at_start, undo=True)
            if ran_before is not None:
                put_back, _, _ = self._apply_configuration_locked(
                    connected_snapshot=connected_snapshot, sample_usage=False,
                    control_intent=(halts_at_start, control_revision), keep_exceptions=True,
                    restore_plan=ran_before,
                )
                failure += (
                    " What ran before was put back."
                    if put_back == HTTPStatus.OK else
                    " What ran before could not be put back, so the dashboard keeps trying."
                )
                state = self.repository.get_runtime_state()
            return (
                HTTPStatus.BAD_GATEWAY,
                "application/json",
                _json_bytes(
                    {
                        "error": failure,
                        "hint": (
                            "On the Pi, run sudo journalctl -u jittersim-helper.service "
                            "-n 80 --no-pager to see which network step failed and "
                            "whether cleanup completed."
                        ),
                        "state": _runtime_state_payload(state),
                    }
                ),
            )
        def publish():
            if result.get("counters_preserved") is not True:
                self.repository.rebase_quota_counters(now=int(time.time()))
            state = self.repository.update_runtime_state(
                observed_state="applied", last_action=runtime_action, last_result="success",
                last_error=None, **applied_keep,
            )
            if self.scheduler is not None:
                self.scheduler.activate_plan(repository=self.repository, prepared=prepared,
                                             counters_preserved=result.get("counters_preserved") is True)
            return state

        accepted, state = self._publish_apply(halts_at_start, publish)
        if not accepted:
            return self._apply_refused(halts_at_start, undo=True)
        return (
            HTTPStatus.OK,
            "application/json",
            _json_bytes(
                {
                    "result": result,
                    "state": self._runtime_state_with_running(state),
                    "applied": self._applied_summary(plan),
                    "unverified_device_ids": list(plan.unverified_device_ids),
                    "warning": _apply_warning(
                        plan.unverified_device_ids,
                        prepared.unresolved_names if prepared is not None else (),
                        _upstream_warning(plan, result.get("upload_corruption_applied")),
                    ),
                }
            ),
        )

    def _clean_up_failed_device_start(self, generation):
        """Reset the helper after a failed device-only start and record the result.

        A failed cleanup stays pending: it is retried with bounded waits, reset only,
        whether or not the scheduler runs.
        """

        if self.scheduler is not None:
            self.scheduler.pause()
        try:
            self.helper_client.reset()
        except HelperClientError:
            LOGGER.error("cleanup after a failed device-only start failed; retrying")
            result = self._publish_apply(generation, lambda: self.repository.update_runtime_state(
                observed_state="error", last_action="reset", last_result="failure",
                last_error=DEVICE_CLEANUP_ERROR,
            ))
            self._retry_device_cleanup()
            return result
        return self._publish_apply(generation, lambda: self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None,
        ))

    def _cleanup_is_pending(self) -> bool:
        # A settings save marks the state unknown but keeps the marker, so the
        # marker alone says the cleanup is still owed.
        state = self.repository.get_runtime_state()
        return state.last_action == "reset" and state.last_error == DEVICE_CLEANUP_ERROR

    def _finish_device_cleanup(self, generation) -> bool:
        """Record a finished cleanup unless Panic or anything newer decided the state."""

        def publish():
            if not self._cleanup_is_pending():
                return False
            self.repository.update_runtime_state(
                observed_state="reset", last_action="reset", last_result="success",
                last_error=None,
            )
            return True

        accepted, recorded = self._publish_apply(generation, publish)
        return bool(accepted and recorded)

    def _retry_device_cleanup(self) -> None:
        """Retry the reset of a failed device-only start a bounded number of times."""

        worker = self._cleanup_worker
        if worker is not None and worker.is_alive():
            return

        def run():
            for delay in self.cleanup_retry_delays:
                if self._stop_connections.wait(delay):
                    return
                operation = self.scheduler.exclusive() if self.scheduler is not None else nullcontext()
                with operation:
                    generation = getattr(self.scheduler, "halt_count", 0)
                    try:
                        if not self._cleanup_is_pending():
                            return  # Something newer decided the state.
                        self.helper_client.reset()
                        if not self._finish_device_cleanup(generation):
                            return  # Panic or a newer action owns the state now.
                    except HelperClientError:
                        LOGGER.warning("cleanup after a failed device-only start failed again")
                        continue
                    except (StorageError, sqlite3.Error):
                        LOGGER.exception("could not record the device-only cleanup")
                        return
                self._record_activity("reset", "Cleanup after a failed device-only start completed")
                return
            LOGGER.error("cleanup after a failed device-only start is still not confirmed. Retry Panic")

        self._cleanup_worker = threading.Thread(
            target=run, name="jittersim-device-cleanup", daemon=True)
        self._cleanup_worker.start()

    def _panic_since(self, halts_at_start: int) -> bool:
        return getattr(self.scheduler, "halt_count", 0) != halts_at_start

    def _control_rejected(self, error):
        if error.reason != "control-changed":
            return None
        if self.scheduler is not None:
            self.scheduler.halt()
            self.scheduler.require_recovery()
        return HTTPStatus.CONFLICT, "application/json", _json_bytes({
            "error": "Network control changed before this action. Reload and retry Panic before choosing Start.",
        })

    def _capture_control_intent(self, generation):
        refused = self._apply_refused(generation)
        if refused is not None:
            return refused, None
        reader = getattr(self.helper_client, "control_revision", None)
        try:
            revision = reader() if callable(reader) else None
        except HelperClientError as error:
            return (HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes({"error": str(error)})), None
        return self._apply_refused(generation), revision

    def _publish_apply(self, generation, publish):
        if self.scheduler is not None and hasattr(self.scheduler, "publish_if_current"):
            return self.scheduler.publish_if_current(generation, publish)
        return True, publish()

    def _apply_refused(self, generation, *, undo=False):
        if self._panic_since(generation):
            return self._stopped_by_panic(undo=undo)
        if self.scheduler is not None and hasattr(self.scheduler, "can_apply"):
            if not self.scheduler.can_apply(generation):
                message = (
                    "The dashboard is stopping. Retry after it restarts."
                    if self.scheduler.shutting_down else
                    "Normal access recovery is not confirmed. Retry Panic before choosing Start."
                )
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes({"error": message})
        return None

    def _stopped_by_panic(self, *, undo: bool) -> Tuple[int, str, bytes]:
        """Preserve Panic's result and report unconfirmed late cleanup honestly."""

        error = None
        state = None
        if undo and self.helper_client is not None:
            ticket = self.scheduler.begin_recovery(cancel=False) if self.scheduler else None
            try:
                restore = getattr(self.helper_client, "normal_access", self.helper_client.reset)
                restore()
            except HelperClientError:
                LOGGER.error("could not undo an apply that Panic overtook")
                error = "Normal access was not fully restored after Panic. Retry Panic before choosing Start."
            except BaseException:
                if ticket:
                    self.scheduler.abandon_recovery(ticket)
                raise

            def publish(overlap_failed=False):
                nonlocal error
                if overlap_failed and error is None:
                    error = "Another Panic cleanup failed during this recovery. Retry Panic before choosing Start."
                try:
                    state = self.repository.update_runtime_state(
                        observed_state="error" if error else "reset", last_action="reset",
                        last_result="failure" if error else "success", last_error=error,
                    )
                except (StorageError, sqlite3.Error):
                    error = "Panic cleanup could not be saved. Retry Panic before choosing Start."
                    return None, False
                return state, error is None

            state = self.scheduler.finish_recovery(ticket, publish, failed=error is not None) if ticket else publish()[0]
        if state is None:
            try:
                state = self.repository.get_runtime_state()
            except (StorageError, sqlite3.Error):
                pass
        body = {"error": error or "Panic stopped this apply. Choose Start with saved settings when it is safe."}
        if state is not None:
            body["state"] = self._runtime_state_with_running(state)
        return (HTTPStatus.SERVICE_UNAVAILABLE if error else HTTPStatus.CONFLICT,
                "application/json", _json_bytes(body))

    def _reset_network(self, *, automatic_recovery=False) -> Tuple[int, str, bytes]:
        operation = self.scheduler.exclusive() if self.scheduler is not None else nullcontext()
        with operation:
            return self._reset_network_locked(automatic_recovery=automatic_recovery)

    @recorded("reset", "dashboard")
    def _reset_network_locked(self, *, automatic_recovery=False) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return (
                HTTPStatus.SERVICE_UNAVAILABLE,
                "application/json",
                _json_bytes({"error": "network helper is not configured"}),
            )
        if self.scheduler is not None:
            # Resetting removes the helper counters, so record their traffic first.
            self.scheduler.sample_usage(repository=self.repository)
            self.scheduler.pause()
        self.repository.update_runtime_state(
            observed_state="resetting",
            last_action="reset",
            last_result=None,
            last_error=None,
        )
        try:
            recover = getattr(self.helper_client, "recover_reset", None) if automatic_recovery else None
            result = recover() if callable(recover) else self.helper_client.reset()
        except HelperClientError as error:
            LOGGER.error("network helper reset failed: %s", error)
            state = self.repository.update_runtime_state(
                observed_state="error",
                last_action="reset",
                last_result="failure",
                last_error="network helper reset failed",
            )
            return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                {"error": "network helper reset failed", "state": _runtime_state_payload(state)}
            )
        state = self.repository.update_runtime_state(
            observed_state="reset",
            last_action="reset",
            last_result="success",
            last_error=None,
        )
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"result": result, "state": self._runtime_state_with_running(state)}
        )

    def is_authorized(self, authorization_header: Optional[str]) -> bool:
        """Return whether an Authorization header matches the configured admin token."""

        return _authorization_matches(
            expected_token=self.admin_token,
            authorization_header=authorization_header,
        )

    def recover_saved_network_state(self) -> None:
        """Restore the last explicit apply or reset after the service restarts."""

        if self.helper_client is None:
            return
        self._ad_blocking_sync_pending = True
        if not self._wait_for_helper():
            LOGGER.error("network helper did not answer, so network state was not recovered")
            return
        try:
            self._sync_ad_blocking()
        except (HelperClientError, StorageError):
            LOGGER.warning("saved ad-blocking choices could not be confirmed during dashboard startup")
        # Grants dashboard access to every saved protected device (also the upgrade step).
        self.recover_protected_access()
        if not self._sync_gateway_persistence(busy_attempts=STARTUP_HELPER_BUSY_ATTEMPTS):
            return
        try:
            gateway_status = self.helper_client.gateway_status()
        except HelperClientError:
            LOGGER.error("gateway recovery state was unavailable during dashboard startup")
            return
        if (gateway_status.get("outage_stop_latched") is True
                or gateway_status.get("panic_stop_latched") is True
                or gateway_status.get("state_error") is True):
            if self.scheduler is not None:
                saved = self.repository.get_runtime_state()
                if (gateway_status.get("panic_stop_latched") is True
                        and (saved.last_action != "reset" or saved.last_result != "success"
                             or gateway_status.get("panic_recovery_failed") is True)):
                    self.scheduler.require_recovery()
                else:
                    self.scheduler.pause()
            self._record_activity(
                "outage",
                "Profiles remain stopped after a router gateway outage",
            )
            return
        # Rows an older dashboard left beside a completed Reset never start again.
        self.repository.discard_stale_device_start_scope()
        state = self.repository.get_runtime_state()
        if state.last_action not in {"reset", "apply", "schedule"}:
            return
        if state.last_action == "reset":
            scoped = self.repository.get_device_start_scope() is not None
            if not scoped and self._cleanup_is_pending():
                # A failed device-only cleanup is a reset still to finish. A failure
                # keeps the marker and the bounded retries carry on.
                try:
                    self.helper_client.reset()
                except HelperClientError:
                    LOGGER.error("device-only cleanup failed again after the dashboard restarted")
                    self._retry_device_cleanup()
                    return
                if self._finish_device_cleanup(getattr(self.scheduler, "halt_count", 0)):
                    self._record_activity(
                        "reset", "Cleanup after a failed device-only start completed after a restart")
                return
            if state.last_result != "success" and not scoped:
                if self.scheduler is not None:
                    self.scheduler.require_recovery()
                LOGGER.error("saved recovery remains unconfirmed. Retry Panic before Start")
                return
            if self.repository.get_device_start_scope() is not None:
                # Only the devices of a device-only start run again.
                status, _, _ = self._apply_configuration(device_scope=True)
                kind, message = "apply", "Device-only start applied again after the dashboard restarted"
            else:
                status, _, _ = self._reset_network(automatic_recovery=True)
                kind, message = "reset", "Network reset again after the dashboard restarted"
        else:
            # Devices that run their own profile beside Everyone run again with it.
            status, _, _ = self._apply_configuration(keep_exceptions=True)
            kind, message = "apply", "Configuration applied again after the dashboard restarted"
        if status != HTTPStatus.OK:
            LOGGER.error("network state recovery did not complete successfully")
        else:
            self._record_activity(kind, message)

    def _sync_gateway_persistence(self, *, busy_attempts: int = 1) -> bool:
        """Give the helper the saved gateway persistence preference.

        A helper-busy answer is asked again up to busy_attempts times, one
        helper poll apart, so a helper still busy at boot does not skip the restore.
        """

        if self.helper_client is None:
            return False
        for attempt in range(busy_attempts):
            try:
                self.helper_client.set_gateway_persistence(
                    enabled=self.repository.get_persist_profiles_after_gateway_outage()
                )
                return True
            except HelperClientError as error:
                if error.reason != "helper-busy" or attempt + 1 >= busy_attempts:
                    break
            time.sleep(self.helper_poll_seconds)
        LOGGER.error("gateway persistence setting did not reach the networking helper")
        return False

    def _record_activity(self, kind: str, message: str) -> None:
        try:
            self.repository.record_activity(kind=kind, message=message, now=time.time())
        except StorageError:
            LOGGER.warning("could not record activity", exc_info=True)

    def _wait_for_helper(self) -> bool:
        """Return whether the helper answers before the startup wait ends."""

        deadline = time.monotonic() + self.helper_startup_wait_seconds
        while True:
            try:
                self.helper_client.ping()
                return True
            except HelperClientError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(self.helper_poll_seconds)


class ControlRequestHandler(BaseHTTPRequestHandler):
    """Handle the control-plane routes."""

    server: ControlServer
    # A neutral name: response headers do not advertise the product or Python.
    server_version = "server"
    rbufsize = 0

    def setup(self) -> None:
        super().setup()
        prefix, deadline = self.server._request_inputs.pop(
            self.connection, (b"", time.monotonic() + REQUEST_INPUT_TIMEOUT_SECONDS))
        self.rfile = io.BufferedReader(_DeadlineReader(self.rfile, self.connection, deadline, prefix))

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionError, TimeoutError, socket.timeout, ssl.SSLError):
            self.close_connection = True
            LOGGER.info("dashboard client disconnected")

    def version_string(self) -> str:
        return self.server_version

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if self.server.is_reserved_request(self.connection) and not (
            self.command == "POST" and urlsplit(self.path).path in PRIORITY_WRITE_PATHS
        ):
            # Every ordinary slot is busy and this one is kept for Panic.
            self.close_connection = True
            self._send_response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                content_type="application/json",
                body=_json_bytes({"error": "the dashboard is busy, try again"}),
                headers={"Retry-After": "1"},
            )
            return False
        return True

    def do_GET(self) -> None:
        parsed_path = urlsplit(self.path)
        path = parsed_path.path
        if path.startswith("/api/") and path not in PUBLIC_READ_PATHS:
            if self.server.admin_token is None:
                self._send_response(
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                    content_type="application/json",
                    body=_json_bytes({"error": "protected reads are disabled"}),
                )
                return
            if not self.server.is_authorized(self.headers.get("Authorization")):
                self._send_response(
                    status=HTTPStatus.UNAUTHORIZED,
                    content_type="application/json",
                    body=_json_bytes({"error": "valid bearer authentication is required"}),
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return
            if path not in ("/api/dashboard-access/status", "/api/dashboard-access/moves"
                            ) and not self._check_device_access():
                return
        try:
            if path == "/api/dashboard-access/moves":
                identity = self.server.dashboard_access(self.client_address[0])
                status, content_type, body = (HTTPStatus.OK, "application/json",
                                              _json_bytes(self.server.address_move_offer(identity)))
            elif path in ("/api/dashboard-access/status", "/api/dashboard-access"):
                data = self.server.dashboard_access(
                    self.client_address[0], "status" if path.endswith("/status") else "list")
                if not path.endswith("/status"):
                    data = {**data, "protected_access": self.server.protected_access_status()}
                status, content_type, body = HTTPStatus.OK, "application/json", _json_bytes(data)
            else:
                status, content_type, body = self.server.response_for(path, query=parsed_path.query)
        except HelperClientError as error:
            if path == "/api/dashboard-access/status" and error.reason == "helper-busy":
                status, content_type, body = (
                    HTTPStatus.CONFLICT,
                    "application/json",
                    _json_bytes({"error": str(error), "code": "helper-busy"}),
                )
            else:
                status, content_type, body = (HTTPStatus.SERVICE_UNAVAILABLE, "application/json",
                                              _json_bytes({"error": str(error)}))
        except (PlanError, StorageError):
            LOGGER.exception("could not build control-plane response for %s", path)
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            content_type = "application/json"
            body = _json_bytes({"error": "stored configuration could not be read"})
        except (ConnectionError, TimeoutError, socket.timeout, ssl.SSLError):
            raise
        except Exception:
            LOGGER.exception("control-plane response for %s failed", path)
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            content_type = "application/json"
            body = _json_bytes({"error": "request could not be processed"})
        downloads = {
            "/api/backup": "jittersim-backup.db",
            "/api/export": "jittersim-settings.json",
            **{trust_path: trust_file for trust_path, (_, trust_file) in TRUST_DOWNLOADS.items()},
        }
        headers = (
            {"Content-Disposition": f'attachment; filename="{downloads[path]}"'}
            if path in downloads and status == HTTPStatus.OK
            else None
        )
        self._send_response(status=status, content_type=content_type, body=body, headers=headers)

    def do_POST(self) -> None:
        self._handle_write()

    def do_PUT(self) -> None:
        self._handle_write()

    def do_HEAD(self) -> None:
        # Only the public health check answers HEAD, so a monitor can ask for the status alone.
        if urlsplit(self.path).path == "/api/health":
            self.do_GET()
            return
        self._method_not_allowed("this address does not answer HEAD requests")

    def do_DELETE(self) -> None:
        self._method_not_allowed("delete actions are not enabled")

    def do_OPTIONS(self) -> None:
        self._method_not_allowed("this request method is not allowed")

    def do_PATCH(self) -> None:
        self._method_not_allowed("this request method is not allowed")

    def _method_not_allowed(self, message: str) -> None:
        self._send_response(
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            content_type="application/json",
            body=_json_bytes({"error": message}),
            headers={"Allow": "GET, HEAD, POST, PUT"},
        )

    def send_error(self, code: int, message: Optional[str] = None, explain: Optional[str] = None) -> None:
        # The standard library answers requests it refuses itself (a request line or header that is
        # too long, an unknown method or HTTP version) with an HTML page that can echo the request.
        # Say the same in the JSON every other refusal uses and echo nothing.
        try:
            phrase = HTTPStatus(code).phrase.lower()
        except ValueError:
            phrase = "request refused"
        sentence = REFUSED_REQUEST_SENTENCES.get(code, phrase)
        self.log_error("request refused with status %s", code)
        if getattr(self, "command", None) is None and getattr(self, "request_version", "") == "HTTP/0.9":
            # The request line was refused before its version was read, and the standard library
            # sends an answer to a request of that kind with no status line and no headers.
            self.request_version = "HTTP/1.0"
        self._send_response(
            status=code,
            content_type="application/json",
            body=_json_bytes({"error": sentence}),
            headers={"Connection": "close"},
        )

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        # Addresses and request lines stay out of the journal. Routine polling
        # is kept quiet while changes and every refused request are recorded.
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 0
        if status >= 400:
            level = logging.WARNING
        elif getattr(self, "command", None) == "GET":
            level = logging.DEBUG
        else:
            level = logging.INFO
        LOGGER.log(level, "dashboard response status=%s", status)

    def log_error(self, format_string: str, *args: Any) -> None:
        LOGGER.warning("dashboard request rejected or timed out")

    def log_message(self, format_string: str, *args: Any) -> None:
        LOGGER.info("dashboard connection event")

    def _handle_write(self) -> None:
        path = urlsplit(self.path).path
        # Check the token before the route, so that the answers don't reveal which
        # routes exist to someone without it.
        if self.server.admin_token is None:
            self._send_response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                content_type="application/json",
                body=_json_bytes({"error": "state-changing actions are disabled"}),
            )
            return
        if not self.server.is_authorized(self.headers.get("Authorization")):
            self._send_response(
                status=HTTPStatus.UNAUTHORIZED,
                content_type="application/json",
                body=_json_bytes({"error": "valid bearer authentication is required"}),
                headers={"WWW-Authenticate": "Bearer"},
            )
            return
        if path not in WRITE_PATHS:
            self._send_response(
                status=HTTPStatus.NOT_FOUND,
                content_type="application/json",
                body=_json_bytes({"error": "not found"}),
            )
            return
        if path not in ("/api/dashboard-access/register-self", "/api/dashboard-access/move"
                        ) and not self._check_device_access(emergency=path in EMERGENCY_WRITE_PATHS):
            return
        if path == RELEASE_UPLOAD_PATH:
            handle_release_upload(self)
            return

        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if (path.startswith(("/api/router/", "/api/homepage/")) or path in ("/api/access-point/reset-name",
                    "/api/dashboard-access/register-self", "/api/dashboard-access/remove",
                    "/api/dashboard-access/move", "/api/dashboard-access/move-undo",
                    "/api/dashboard-access/sync-protected")) and (
            self.headers.get("Sec-Fetch-Site") == "cross-site"
            or (origin is not None and origin not in ("http://" + host, "https://" + host))
        ):
            self._send_response(
                status=HTTPStatus.FORBIDDEN,
                content_type="application/json",
                body=_json_bytes({"error": "Wi-Fi recovery must use the dashboard's own site"}),
            )
            return
        try:
            payload = self._read_json_body(
                required=path not in ACTION_PATHS,
                max_bytes=(
                    MAX_IMPORT_BODY_BYTES
                    if path in ("/api/import", "/api/test-sessions/save")
                    else MAX_AD_BLOCKING_BODY_BYTES if path == "/api/ad-blocking"
                    else (8192 if path.startswith("/api/homepage/") else MAX_JSON_BODY_BYTES)
                ),
            )
            if path in ("/api/dashboard-access/move", "/api/dashboard-access/move-undo"):
                # Only the old address is named. The new one is the verified connection.
                if set(payload) != {"from_mac"} or not isinstance(payload["from_mac"], str):
                    raise RequestError("Choose which device this is. Its new address comes from the connection.")
                try:
                    old = normalize_exempt_mac(payload["from_mac"])
                except ValueError as error:
                    raise RequestError("Choose a saved device.") from error
                if path.endswith("-undo"):
                    status, content_type, body = self.server.undo_device_address_move(
                        old, self.client_address[0])
                else:
                    status, content_type, body = self.server.move_device_address_for_peer(
                        old, self.client_address[0])
            elif path == "/api/dashboard-access/sync-protected":
                if payload:
                    raise RequestError("Retrying protected access takes no settings.")
                state = self.server.sync_protected_access()
                status = HTTPStatus.OK if state.get("synced") else HTTPStatus.SERVICE_UNAVAILABLE
                content_type, body = "application/json", _json_bytes(state)
            elif path == "/api/unaffected-devices/delete":
                status, content_type, body = self.server.remove_protection_for_peer(
                    payload, self.client_address[0])
            elif path == "/api/import":
                status, content_type, body = self.server.import_for_peer(
                    payload, self.client_address[0])
            elif path == "/api/history/restore":
                status, content_type, body = self.server.history_restore_for_peer(
                    payload, self.client_address[0])
            elif path.startswith("/api/dashboard-access/"):
                register = path.endswith("/register-self")
                if set(payload) - ({"name"} if register else {"mac_address"}):
                    raise RequestError("Device identity must come from the connection.")
                data = self.server.dashboard_access(
                    self.client_address[0], "register-self" if register else "remove", **payload)
                status, content_type, body = HTTPStatus.OK, "application/json", _json_bytes(data)
            else:
                status, content_type, body = self.server.write_response_for(path, payload)
        except HelperClientError as error:
            status, content_type, body = (HTTPStatus.SERVICE_UNAVAILABLE, "application/json",
                                          _json_bytes({"error": str(error)}))
        except RequestError as error:
            status = HTTPStatus.BAD_REQUEST
            content_type = "application/json"
            body = _json_bytes({"error": str(error)})
        except StorageError:
            LOGGER.exception("could not persist control-plane request for %s", path)
            status = HTTPStatus.CONFLICT
            content_type = "application/json"
            body = _json_bytes({"error": "configuration could not be saved"})
        except (ConnectionError, TimeoutError, socket.timeout, ssl.SSLError):
            raise
        except Exception:
            LOGGER.exception("control-plane request for %s failed", path)
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            content_type = "application/json"
            body = _json_bytes({"error": "request could not be processed"})
        if path not in ACTION_PATHS and path not in PRIORITY_WRITE_PATHS:
            # Any other write that changed saved protection (a restore, a group action) is
            # reconciled here. Unchanged protection costs one read and no helper call.
            try:
                self.server.reconcile_protected_access()
            except Exception:
                LOGGER.exception("protected devices' dashboard access could not be reconciled")
        try:
            if path not in ("/api/router/status", "/api/router/operation") and status != HTTPStatus.ACCEPTED:
                with (self.server.bounded_repository(PANIC_REPOSITORY_WAIT_SECONDS)
                      if path in PRIORITY_WRITE_PATHS else nullcontext()):
                    self.server.repository.record_action(action=path, status_code=int(status))
        except StorageError:
            LOGGER.exception("could not record action history for %s", path)
        self._send_response(status=status, content_type=content_type, body=body)

    def _check_device_access(self, *, emergency: bool = False) -> bool:
        peer = self.client_address[0]
        try:
            if emergency:
                with deadline_scope(time.monotonic() + EMERGENCY_CHECK_BUDGET_SECONDS):
                    try:
                        allowed = self.server.dashboard_access(peer, "emergency-status")["allowed"]
                    except HelperClientError as error:
                        if error.reason == "helper-busy":
                            raise
                        # An older helper does not know the emergency check. The
                        # ordinary check decides then, as before.
                        allowed = self.server.dashboard_access(peer)["allowed"]
            else:
                allowed = self.server.dashboard_access(peer)["allowed"]
        except HelperClientError as error:
            self._send_response(status=HTTPStatus.SERVICE_UNAVAILABLE, content_type="application/json",
                                body=_json_bytes({"error": str(error)}))
            return False
        if not allowed:
            self._send_response(status=HTTPStatus.FORBIDDEN, content_type="application/json",
                                body=_json_bytes({"error": "Allow this device to use the dashboard.",
                                                  "registration_required": True}))
        return allowed

    def _read_json_body(
        self, *, required: bool = True, max_bytes: int = MAX_JSON_BODY_BYTES
    ) -> Dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            if not required:
                return {}
            raise RequestError("request body is required")
        try:
            length = int(length_header)
        except ValueError as error:
            raise RequestError("request body length is invalid") from error
        if length < 0 or length > max_bytes:
            raise RequestError("request body is too large")
        if length == 0 and not required:
            return {}
        raw_body = self.rfile.read(length)
        if len(raw_body) != length:
            raise RequestError("request body is incomplete")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise RequestError("request body must be valid JSON") from error
        except ValueError as error:
            # Python refuses an integer literal of more than 4300 digits with a plain ValueError.
            raise RequestError("a number in the request body has too many digits") from error
        if not isinstance(payload, dict):
            raise RequestError("request body must contain a JSON object")
        if _has_lone_surrogate(payload):
            raise RequestError("request body has a character that is not valid text (an unpaired surrogate)")
        return payload

    def send_response(self, code: int, message: Optional[str] = None) -> None:
        self.connection.settimeout(RESPONSE_SEND_TIMEOUT_SECONDS)
        super().send_response(code, message)

    def _send_response(
        self,
        *,
        status: int,
        content_type: str,
        body: bytes,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        cache_control = "no-store"
        page_headers: Dict[str, str] = {}
        page_html = getattr(getattr(self, "server", None), "dashboard_page_html", None)
        raw_page, packed_page, page_etag = _dashboard_page(page_html) if page_html is not None else _dashboard_page()
        if status == HTTPStatus.OK and content_type.startswith("text/html") and body == raw_page:
            # The page only changes with a new release: a reload asks with its ETag and an
            # unchanged page answers 304. Browsers that accept gzip get about a fifth of it.
            request_headers = getattr(self, "headers", None) or {}
            cache_control = "no-cache"
            page_headers = {"ETag": page_etag, "Vary": "Accept-Encoding"}
            asked = [tag.strip() for tag in (request_headers.get("If-None-Match") or "").split(",")]
            if "*" in asked or any(tag.removeprefix("W/") == page_etag.removeprefix("W/")
                                    for tag in asked):
                status, body = HTTPStatus.NOT_MODIFIED, b""
            elif _accepts_gzip(request_headers.get("Accept-Encoding") or ""):
                body = packed_page
                page_headers["Content-Encoding"] = "gzip"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if status != HTTPStatus.NOT_MODIFIED:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        for name, value in page_headers.items():
            self.send_header(name, value)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", DASHBOARD_CONTENT_SECURITY_POLICY)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)


class RequestError(ValueError):
    """Raised when an HTTP request body cannot be safely decoded."""


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _has_lone_surrogate(value: Any) -> bool:
    """Say whether a decoded JSON value holds a UTF-16 surrogate with no partner.

    json.loads joins a valid pair of escapes into one character but keeps a lone one, and a lone
    one fails later in sqlite3 or str.encode with a UnicodeEncodeError. Looking once, where the
    body is read, covers every route. The walk is iterative so deep nesting cannot recurse.
    """

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if _LONE_SURROGATE.search(item):
                return True
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def handle_release_upload(handler: "ControlRequestHandler") -> None:
    """Receive a signed release archive and its signature, then stage them through the helper.

    Both files come in one body: the archive bytes, then the signature bytes. The header
    X-Jittersim-Archive-Bytes says where the archive ends. Content-Length is required so a
    request is refused before it is read. Nothing here decides whether the release is good:
    the root update manager verifies the signature of a trusted key and the release order
    so the admin token alone can only stage a release the owner signed.
    """

    server = handler.server

    def refuse(status: int, message: str) -> None:
        # The unread body must not be taken for the next request.
        handler.close_connection = True
        handler._send_response(
            status=status, content_type="application/json", body=_json_bytes({"error": message}),
            headers={"Connection": "close"},
        )

    if server.helper_client is None:
        refuse(HTTPStatus.SERVICE_UNAVAILABLE, "Release controls require the Pi network helper.")
        return
    if handler.headers.get("Transfer-Encoding") is not None:
        refuse(HTTPStatus.LENGTH_REQUIRED, "Send the release with a Content-Length.")
        return
    length_header = handler.headers.get("Content-Length")
    archive_header = handler.headers.get(RELEASE_UPLOAD_HEADER)
    if length_header is None:
        refuse(HTTPStatus.LENGTH_REQUIRED, "Send the release with a Content-Length.")
        return
    try:
        if not (length_header.isascii() and length_header.isdigit()
                and archive_header is not None and archive_header.isascii()
                and archive_header.isdigit()):
            raise ValueError
        total = int(length_header)
        archive_size = int(archive_header)
    except ValueError:
        refuse(HTTPStatus.BAD_REQUEST,
               "Send the size of the archive in " + RELEASE_UPLOAD_HEADER + ".")
        return
    signature_size = total - archive_size
    if archive_size < 1 or signature_size < 1:
        refuse(HTTPStatus.BAD_REQUEST, "The upload needs an archive and its signature.")
        return
    if archive_size > updates.MAX_ARCHIVE:
        refuse(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
               "The release archive is larger than the 25 MiB limit.")
        return
    if signature_size > updates.MAX_SIGNATURE:
        refuse(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
               "The release signature is larger than the 16 KiB limit.")
        return

    spool = Path(server.release_spool)
    reader = handler.rfile.raw if hasattr(handler.rfile, "raw") else None
    deadline = time.monotonic() + server.release_upload_seconds
    if isinstance(reader, _DeadlineReader):
        reader.extend_to(deadline)
    problem: Optional[Tuple[int, str]] = None
    receipt = None
    try:
        with release_uploads.receiving(spool, server.release_upload_lock,
                                       seconds=server.release_upload_seconds) as receipt:
            for name, size in ((updates.UPLOAD_ARCHIVE, archive_size),
                               (updates.UPLOAD_SIGNATURE, signature_size)):
                descriptor = os.open(
                    str(receipt.folder / name),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    remaining = size
                    while remaining:
                        if time.monotonic() > deadline:
                            raise TimeoutError("release upload took too long")
                        block = handler.rfile.read(min(65536, remaining))
                        if not block:
                            problem = (HTTPStatus.BAD_REQUEST,
                                       "The upload ended before all of the release arrived.")
                            break
                        output.write(block)
                        remaining -= len(block)
                    output.flush()
                    os.fsync(output.fileno())
                if problem:
                    break
            if not problem:
                receipt.ready()
    except release_uploads.UploadBusy as error:
        problem = (HTTPStatus.CONFLICT, str(error))
    except (TimeoutError, socket.timeout):
        problem = (HTTPStatus.REQUEST_TIMEOUT,
                   "The upload was too slow. It must finish within "
                   + str(int(server.release_upload_seconds)) + " seconds.")
    except (ConnectionError, ssl.SSLError):
        handler.close_connection = True
        raise
    except (OSError, updates.UpdateError):
        LOGGER.exception("could not write the uploaded release")
        problem = (HTTPStatus.INTERNAL_SERVER_ERROR, "The Pi could not store the upload.")
    if problem:
        refuse(*problem)
        return
    try:
        result = server.helper_client.stage_uploaded_release(upload_id=receipt.identifier)
        status, body = HTTPStatus.OK, {"uploaded": True, **result}
        body.pop("ok", None)
    except HelperClientError as error:
        status = (HTTPStatus.UNPROCESSABLE_ENTITY if error.reason == "release-refused" else
                  HTTPStatus.CONFLICT if error.reason == "helper-busy" else HTTPStatus.SERVICE_UNAVAILABLE)
        body = {"error": str(error)}
    finally:
        try:
            release_uploads.discard(receipt.identifier, spool=spool, lock=server.release_upload_lock)
        except (OSError, updates.UpdateError):
            LOGGER.exception("could not clean up the completed release upload")
    try:
        server.repository.record_action(action=RELEASE_UPLOAD_PATH, status_code=int(status))
    except StorageError:
        LOGGER.exception("could not record action history for the release upload")
    handler._send_response(
        status=status, content_type="application/json", body=_json_bytes(body))


def _wifi_failure(error: HelperClientError, failed: str, fallback: str) -> WifiOutcome:
    """Turn a helper failure into one of the safe texts. Nothing else is passed on."""

    safe_errors = {
        "wifi-restored": failed + ". The previous Wi-Fi settings were restored.",
        "wifi-restore-failed": failed + " and recovery failed. Use management access to repair jittersim-ap.",
        "wifi-busy": "Another Pi Wi-Fi change is running. Try again later.",
        "helper-busy": "The networking helper is busy. Try again later.",
    }
    status = (HTTPStatus.CONFLICT if error.reason in ("wifi-busy", "helper-busy")
              else HTTPStatus.BAD_GATEWAY)
    return WifiOutcome(int(status), safe_errors.get(error.reason, failed + ". " + fallback))


def _safe_wifi_copy_message(error: HelperClientError) -> str:
    """Name the two copy-only answers and let nothing else through but the router messages."""

    if error.reason == "helper-busy":
        return WIFI_COPY_BUSY_MESSAGE
    if str(error) == WIFI_DETAILS_MISSING_MESSAGE:
        return WIFI_DETAILS_MISSING_MESSAGE
    return safe_router_check_message(str(error))


# The helper creates this ifb device only while upload corruption runs before
# the Pi forwards. sysfs is readable without privileges.
UPLOAD_DEVICE_PATH = Path("/sys/class/net") / UPLOAD_DEVICE


def _upload_device_present() -> bool:
    try:
        return UPLOAD_DEVICE_PATH.exists()
    except OSError:
        return False


def _upstream_warning(plan: NetworkPlan, corruption_applied: Optional[bool] = None) -> Optional[str]:
    """Name the upload settings left out so the household router stays safe.

    `corruption_applied` is the helper's own report when it sends one. Without
    it the ifb device's presence tells whether corruption ran.
    """

    uploads = [queue.impairment for queue in plan.queues if queue.direction.value == "upload"]
    if not any(i.corrupt_percent or i.duplicate_percent or i.reorder_percent for i in uploads):
        return None
    if not isinstance(corruption_applied, bool):
        corruption_applied = _upload_device_present()
    withheld = set()
    for impairment in uploads:
        withheld.update(withheld_upload_settings(impairment, corrupt=corruption_applied))
    parts = []
    copied = [name for name in ("duplication", "reordering") if name in withheld]
    if copied:
        parts.append(
            "Upload " + " and ".join(copied) + (" are" if len(copied) == 2 else " is")
            + " not applied so the household router never receives copied or reordered packets"
        )
    if "corruption" in withheld:
        parts.append(
            "Upload corruption is not applied because this Pi cannot damage packets before it "
            "forwards them (the ifb kernel module or tc connmark action is missing)"
        )
    return ". ".join(parts) if parts else None


def _apply_warning(
    unverified_device_ids: Sequence[str], unresolved_names: Sequence[str],
    upstream: Optional[str] = None,
) -> Optional[str]:
    """Say in plain words what an apply left out, or None when it left nothing out."""

    parts = []
    if unverified_device_ids:
        parts.append(
            "Profiles were skipped for devices whose current Wi-Fi identity could not be verified: "
            + ", ".join(unverified_device_ids)
        )
    if unresolved_names:
        parts.append(
            "These website names could not be looked up in time and are left out of the rules "
            "until their lookup works: " + describe_timed_out_names(unresolved_names)
        )
    if upstream:
        parts.append(upstream)
    return ". ".join(parts) if parts else None


def _bad_request(message: str, error: Exception) -> Tuple[int, str, bytes]:
    """Reject a request, adding the validation reason when there is one."""

    if isinstance(error, KeyError) and error.args:
        message = f"{message}: missing {error.args[0]}"
    elif isinstance(error, ValueError) and str(error):
        message = f"{message}: {error}"
    return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes({"error": message})


def _from_known_fields(model: Any, value: Any, label: str = "") -> Any:
    """Build a settings dataclass from an object, naming the field that is wrong.

    model(**value) alone answers with Python's own "got an unexpected keyword argument" text, or,
    when the caller only catches ValueError, with no reason at all.
    """

    where = f"{label}: " if label else ""
    if not isinstance(value, dict):
        raise ValueError(f"{where}must be an object")
    known = [spec.name for spec in dataclass_fields(model) if spec.init]
    unknown = [key for key in value if key not in known]
    if unknown:
        shown = ", ".join(json.dumps(key if len(key) <= 40 else key[:40] + "...") for key in unknown[:3])
        raise ValueError(
            f"{where}unknown field {shown}{' and others' if len(unknown) > 3 else ''}"
            f" (known fields: {', '.join(known)})")
    for spec in dataclass_fields(model):
        if spec.init and spec.default is MISSING and spec.default_factory is MISSING and spec.name not in value:
            raise KeyError(f"{label}.{spec.name}" if label else spec.name)
    return model(**value)


def _text_identifier(value: Any, name: str) -> str:
    """Check that an identifier named in a request is text before it is looked up.

    A list, an object or a huge integer would otherwise reach sqlite as a bound value and fail there
    with a ProgrammingError or an OverflowError, which the dashboard answers with 500.
    """

    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    return value


def _required_text(payload: Dict[str, Any], key: str) -> str:
    """Read one required non-empty string field."""

    value = payload[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _list_field(payload: Dict[str, Any], key: str, *, required: bool = False) -> tuple:
    """Read a JSON list; tuple() alone would split a string into characters."""

    if key not in payload:
        if required:
            raise KeyError(key)
        return ()
    value = payload[key]
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return tuple(value)


def _impairment_from_payload(payload: Any) -> Impairment:
    if payload is None:
        return Impairment()
    if not isinstance(payload, dict):
        raise ValueError("impairment must be an object")
    impairment_data = dict(payload)
    for field_name in (
        "delay_randomness",
        "jitter_randomness",
        "loss_randomness",
        "bandwidth_randomness",
        "reorder_randomness",
        "duplicate_randomness",
        "corrupt_randomness",
    ):
        randomness_data = impairment_data.get(field_name)
        if isinstance(randomness_data, dict):
            impairment_data[field_name] = Randomness(**randomness_data)
    return Impairment(**impairment_data)


def _randomness_from_payload(payload: Any) -> Randomness:
    if payload is None:
        return Randomness()
    if not isinstance(payload, dict):
        raise ValueError("randomness must be an object")
    return Randomness(**payload)


def _profile_from_payload(payload: Dict[str, Any]) -> Profile:
    return Profile(
        id=payload["id"],
        name=payload["name"],
        upload=_impairment_from_payload(payload.get("upload")),
        download=_impairment_from_payload(payload.get("download")),
        random_seed=payload.get("random_seed"),
        random_mode=payload.get("random_mode", "repeatable"),
        reroll_interval_seconds=payload.get("reroll_interval_seconds", 0),
        reroll_interval_randomness=_randomness_from_payload(
            payload.get("reroll_interval_randomness")
        ),
        homepage_bounce_percent=payload.get("homepage_bounce_percent", 0),
        outage_probability=payload.get("outage_probability", 0),
        outage_duration_seconds=payload.get("outage_duration_seconds", 0),
        outage_duration_randomness=_randomness_from_payload(
            payload.get("outage_duration_randomness")
        ),
        outage_interval_seconds=payload.get("outage_interval_seconds", 0),
        outage_interval_randomness=_randomness_from_payload(
            payload.get("outage_interval_randomness")
        ),
        website_mode=payload.get("website_mode", "unrestricted"),
        website_domains=_list_field(payload, "website_domains"),
        website_randomness=_randomness_from_payload(payload.get("website_randomness")),
    )


def _device_from_payload(payload: Dict[str, Any]) -> Device:
    return Device(
        id=payload["id"],
        name=payload["name"],
        ipv4_address=payload["ipv4_address"],
        profile_id=payload["profile_id"],
        mac_address=payload.get("mac_address"),
    )


def _schedule_from_payload(payload: Dict[str, Any]) -> Schedule:
    return Schedule(
        id=payload["id"],
        device_id=payload.get("device_id"),
        profile_id=payload["profile_id"],
        days_of_week=_list_field(payload, "days_of_week", required=True),
        start_minute=payload["start_minute"],
        end_minute=payload["end_minute"],
        enabled=payload.get("enabled", True),
        group_id=payload.get("group_id"),
        everyone_else=payload.get("everyone_else", False),
    )


def _scenario_from_payload(payload: Dict[str, Any]) -> Scenario:
    values = payload["steps"]
    if not isinstance(values, list):
        raise ValueError("steps must be a list")
    steps = []
    for number, step in enumerate(values, start=1):
        # Name the step, so a long scenario says which one to fix.
        try:
            steps.append(
                ScenarioStep(
                    profile_id=step["profile_id"], duration_seconds=step["duration_seconds"]
                )
            )
        except KeyError as error:
            raise ValueError(f"step {number} is missing {error.args[0]}") from error
        except (TypeError, ValueError) as error:
            raise ValueError(f"step {number}: {error}") from error
    return Scenario(
        id=payload["id"],
        name=payload["name"],
        steps=tuple(steps),
        loop=payload.get("loop", False),
    )


def _timer_from_payload(payload: Dict[str, Any]) -> Timer:
    duration_minutes = payload["duration_minutes"]
    if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
        raise ValueError("duration_minutes must be an integer")
    if duration_minutes < 1 or duration_minutes > 10_080:
        raise ValueError("duration_minutes must be between 1 and 10080")
    started_at = int(time.time())
    return Timer(
        id=payload.get("id", f"timer-{time.time_ns()}"),
        device_id=payload["device_id"],
        profile_id=payload["profile_id"],
        started_at=started_at,
        expires_at=started_at + duration_minutes * 60,
    )


def _quota_schedule_from_payload(payload: Dict[str, Any]) -> Tuple[str, int, Tuple[int, ...]]:
    """Read the shared manual, interval or daily reset settings."""

    reset_mode = payload.get("reset_mode", "manual")
    reset_interval_seconds = payload.get("reset_interval_seconds", 0)
    reset_times = _list_field(payload, "reset_times")
    return reset_mode, reset_interval_seconds, reset_times


def _data_limit_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> DataLimit:
    """Build a device quota while retaining usage when it is edited."""

    device_id = _text_identifier(payload["device_id"], "device_id")
    existing = repository.get_data_limit(device_id)
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(payload)
    return DataLimit(
        device_id=device_id,
        profile_id=payload["profile_id"],
        limit_bytes=payload["limit_bytes"],
        reset_mode=reset_mode,
        reset_interval_seconds=reset_interval_seconds,
        reset_times=reset_times,
        used_bytes=existing.used_bytes if existing is not None else 0,
        counter_bytes=existing.counter_bytes if existing is not None else 0,
        last_reset_at=existing.last_reset_at if existing is not None else int(time.time()),
        enabled=payload.get("enabled", True),
    )


def _device_group_from_payload(payload: Dict[str, Any]) -> DeviceGroup:
    """Build a device group from selected device IDs."""

    return DeviceGroup(
        id=payload.get("id", f"group-{time.time_ns()}"),
        name=payload["name"],
        device_ids=_list_field(payload, "device_ids", required=True),
        profile_id=payload.get("profile_id") or None,
    )


def _group_data_limit_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> GroupDataLimit:
    """Build a shared group quota while retaining usage when it is edited."""

    group_id = _text_identifier(payload["group_id"], "group_id")
    existing = repository.get_group_data_limit(group_id)
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(payload)
    return GroupDataLimit(
        group_id=group_id,
        profile_id=payload["profile_id"],
        limit_bytes=payload["limit_bytes"],
        reset_mode=reset_mode,
        reset_interval_seconds=reset_interval_seconds,
        reset_times=reset_times,
        used_bytes=existing.used_bytes if existing is not None else 0,
        counter_bytes=existing.counter_bytes if existing is not None else 0,
        last_reset_at=existing.last_reset_at if existing is not None else int(time.time()),
        enabled=payload.get("enabled", True),
    )


def _service_quota_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> ServiceQuota:
    """Build a service quota from selected observed DNS domains."""

    quota_id = _text_identifier(payload.get("id", f"service-{time.time_ns()}"), "id")
    existing = repository.get_service_quota(quota_id)
    normalized_domains = tuple(
        normalize_domain(domain) for domain in _list_field(payload, "domains", required=True)
    )
    device = repository.get_device(_text_identifier(payload["device_id"], "device_id"))
    if device is not None:
        observed_domains = {
            observation.domain
            for observation in repository.list_domain_observations()
            if str(observation.ipv4_address) == str(device.ipv4_address)
        }
        previous_domains = set(existing.domains) if existing is not None else set()
        if any(
            domain not in observed_domains and domain not in previous_domains
            for domain in normalized_domains
        ):
            raise ValueError("service quota domains must come from DNS observations")
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(payload)
    return ServiceQuota(
        id=quota_id,
        device_id=payload["device_id"],
        name=payload["name"],
        domains=normalized_domains,
        profile_id=payload["profile_id"],
        limit_bytes=payload["limit_bytes"],
        weight_percent=payload.get(
            "weight_percent",
            existing.weight_percent if existing is not None else 100,
        ),
        reset_mode=reset_mode,
        reset_interval_seconds=reset_interval_seconds,
        reset_times=reset_times,
        used_bytes=existing.used_bytes if existing is not None else 0,
        counter_bytes=existing.counter_bytes if existing is not None else 0,
        last_reset_at=existing.last_reset_at if existing is not None else int(time.time()),
        enabled=payload.get("enabled", True),
    )


def _time_quota_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> TimeQuota:
    """Build a timed service quota from selected observed DNS domains."""

    quota_id = _text_identifier(payload.get("id", f"time-{time.time_ns()}"), "id")
    existing = repository.get_time_quota(quota_id)
    normalized_domains = tuple(
        normalize_domain(domain) for domain in _list_field(payload, "domains", required=True)
    )
    device = repository.get_device(_text_identifier(payload["device_id"], "device_id"))
    if device is not None:
        observed_domains = {
            observation.domain
            for observation in repository.list_domain_observations()
            if str(observation.ipv4_address) == str(device.ipv4_address)
        }
        previous_domains = set(existing.domains) if existing is not None else set()
        if any(
            domain not in observed_domains and domain not in previous_domains
            for domain in normalized_domains
        ):
            raise ValueError("time quota domains must come from DNS observations")
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(payload)
    window_duration_seconds = payload["window_duration_seconds"]
    started_at = existing.started_at if existing is not None else 0
    expires_at = existing.expires_at if existing is not None else 0
    if started_at and expires_at - started_at > window_duration_seconds:
        raise ValueError("stop the timer before shortening its window")
    return TimeQuota(
        id=quota_id,
        device_id=payload["device_id"],
        name=payload["name"],
        domains=normalized_domains,
        profile_id=payload["profile_id"],
        limit_seconds=payload["limit_seconds"],
        window_duration_seconds=window_duration_seconds,
        reset_mode=reset_mode,
        reset_interval_seconds=reset_interval_seconds,
        reset_times=reset_times,
        used_seconds=existing.used_seconds if existing is not None else 0,
        counter_bytes=existing.counter_bytes if existing is not None else 0,
        last_sample_at=existing.last_sample_at if existing is not None else 0,
        last_reset_at=existing.last_reset_at if existing is not None else int(time.time()),
        started_at=started_at,
        expires_at=expires_at,
        enabled=payload.get("enabled", True),
    )


def _progression_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> Progression:
    duration_minutes = payload["duration_minutes"]
    if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
        raise ValueError("duration_minutes must be an integer")
    if duration_minutes < 1 or duration_minutes > 10_080:
        raise ValueError("duration_minutes must be between 1 and 10080")
    mode = payload.get("mode", "time")
    traffic_target_queries = payload.get("traffic_target_queries", 0)
    traffic_target_bytes = payload.get("traffic_target_bytes", 0)
    device = repository.get_device(payload["device_id"])
    if device is None:
        raise ValueError("device does not exist")
    return Progression(
        id=payload.get("id", f"progression-{time.time_ns()}"),
        device_id=payload["device_id"],
        start_profile_id=payload["start_profile_id"],
        end_profile_id=payload["end_profile_id"],
        started_at=int(time.time()),
        duration_seconds=duration_minutes * 60,
        mode=mode,
        traffic_target_queries=traffic_target_queries,
        traffic_baseline_queries=repository.total_domain_queries(str(device.ipv4_address)),
        traffic_target_bytes=traffic_target_bytes,
        website_domains=_list_field(payload, "website_domains"),
    )


def _policies_from_payload(payload: Dict[str, Any]) -> Tuple[MacPolicy, WebsitePolicy]:
    mac_payload = payload.get("mac", {})
    website_payload = payload.get("websites", {})
    if not isinstance(mac_payload, dict) or not isinstance(website_payload, dict):
        raise ValueError("policy sections must be objects")
    return (
        MacPolicy(
            mode=mac_payload.get("mode", "disabled"),
            allowlist=_list_field(mac_payload, "allowlist"),
            denylist=_list_field(mac_payload, "denylist"),
        ),
        WebsitePolicy(
            mode=website_payload.get("mode", "unrestricted"),
            allowed_domains=_list_field(website_payload, "allowed_domains"),
            blocked_domains=_list_field(website_payload, "blocked_domains"),
        ),
    )


def _imported_reset_fields(value: Dict[str, Any]) -> Dict[str, Any]:
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(value)
    return {
        "reset_mode": reset_mode,
        "reset_interval_seconds": reset_interval_seconds,
        "reset_times": reset_times,
        "enabled": value.get("enabled", True),
    }


def _unaffected_label(value: Dict[str, Any]) -> str:
    """Read the optional label of an exported unaffected device.

    Exports name the field "name" and fall back to the address, so an address
    read back as a label is treated as no label.
    """

    label = value.get("display_name") or value.get("name") or ""
    if isinstance(label, str) and label.lower() == str(value.get("mac_address", "")).lower():
        return ""
    return label


def _settings_from_payload(payload: Dict[str, Any]) -> SavedSettings:
    """Read a settings export, naming the first item that is wrong."""

    if (payload.get("format") != SETTINGS_FORMAT or type(payload.get("version")) is not int
            or payload.get("version") not in (1, 2, SETTINGS_VERSION)):
        raise ValueError("the file is not a JitterSim settings export this version can read")
    persistence = payload.get("persist_profiles_after_gateway_outage", False)
    if not isinstance(persistence, bool):
        raise ValueError("persist_profiles_after_gateway_outage must be true or false")
    from jittersim.config.adblocking import allowed_domains, blocking_macs
    ad_devices = blocking_macs(payload.get("ad_blocking_devices", []))
    ad_allowances = allowed_domains(payload.get("ad_blocking_allowances", []))
    now = int(time.time())

    def parse_items(label: str, values: Any, parse: Any) -> tuple:
        if not isinstance(values, list):
            raise ValueError(f"{label} must be a list")
        parsed = []
        for index, value in enumerate(values):
            try:
                if not isinstance(value, dict):
                    raise ValueError("must be an object")
                parsed.append(parse(value))
            except KeyError as error:
                raise ValueError(f"{label}[{index}] is missing {error.args[0]}") from error
            except (TypeError, ValueError) as error:
                raise ValueError(f"{label}[{index}]: {error}") from error
        return tuple(parsed)

    def items(key: str, parse: Any) -> tuple:
        return parse_items(key, payload.get(key, []), parse)

    policies = payload.get("policies", {})
    if not isinstance(policies, dict):
        raise ValueError("policies must be an object")
    mac_policy, website_policy = _policies_from_payload(policies)
    # Exports from before the everyone-else profile have no such section.
    everyone_else = payload.get("everyone_else", {})
    if not isinstance(everyone_else, dict):
        raise ValueError("everyone_else must be an object")
    everyone_else_profile_id = everyone_else.get("profile_id")
    if everyone_else_profile_id is not None and not isinstance(everyone_else_profile_id, str):
        raise ValueError("everyone_else.profile_id must be a profile ID or null")
    unaffected_devices = parse_items(
        "everyone_else.unaffected_devices",
        everyone_else.get("unaffected_devices", []),
        lambda value: UnaffectedDevice(
            mac_address=value["mac_address"], display_name=_unaffected_label(value)
        ),
    )
    # Files from main and from older Pi builds carry neither key. The caller then
    # keeps the saved lists and the saved general profile.
    general_profile_id = payload.get("general_profile_id")
    if general_profile_id is not None and not isinstance(general_profile_id, str):
        raise ValueError("general_profile_id must be a profile ID or null")
    service_lists = items(
        "service_lists",
        lambda value: ServiceList(
            id=value["id"],
            name=value["name"],
            profile_id=value["profile_id"],
            services=_list_field(value, "services"),
            hostnames=_list_field(value, "hostnames"),
            enabled=value.get("enabled", True),
        ),
    )
    # Usage starts again from zero on the new installation.
    return SavedSettings(
        profiles=items("profiles", _profile_from_payload),
        devices=items("devices", _device_from_payload),
        groups=items("groups", _device_group_from_payload),
        schedules=items("schedules", _schedule_from_payload),
        # Exports from before scenarios have none.
        scenarios=items("scenarios", _scenario_from_payload),
        mac_policy=mac_policy,
        website_policy=website_policy,
        site_blocks=parse_items("policies.site_blocks", policies.get("site_blocks", []),
                                lambda value: _from_known_fields(SiteBlock, value)),
        access_options=_from_known_fields(AccessOptions, policies.get("options", {}), "policies.options"),
        persist_profiles_after_gateway_outage=persistence,
        data_limits=items(
            "data_limits",
            lambda value: DataLimit(
                device_id=value["device_id"],
                profile_id=value["profile_id"],
                limit_bytes=value["limit_bytes"],
                last_reset_at=now,
                **_imported_reset_fields(value),
            ),
        ),
        group_data_limits=items(
            "group_data_limits",
            lambda value: GroupDataLimit(
                group_id=value["group_id"],
                profile_id=value["profile_id"],
                limit_bytes=value["limit_bytes"],
                last_reset_at=now,
                **_imported_reset_fields(value),
            ),
        ),
        service_quotas=items(
            "service_quotas",
            lambda value: ServiceQuota(
                id=value["id"],
                device_id=value["device_id"],
                name=value["name"],
                domains=_list_field(value, "domains", required=True),
                profile_id=value["profile_id"],
                limit_bytes=value["limit_bytes"],
                weight_percent=value.get("weight_percent", 100),
                last_reset_at=now,
                **_imported_reset_fields(value),
            ),
        ),
        time_quotas=items(
            "time_quotas",
            lambda value: TimeQuota(
                id=value["id"],
                device_id=value["device_id"],
                name=value["name"],
                domains=_list_field(value, "domains", required=True),
                profile_id=value["profile_id"],
                limit_seconds=value["limit_seconds"],
                window_duration_seconds=value.get("window_duration_seconds", 43_200),
                last_reset_at=now,
                **_imported_reset_fields(value),
            ),
        ),
        service_assignments=items(
            "service_assignments",
            lambda value: ServiceAssignment(
                id=value["id"],
                device_id=value["device_id"],
                name=value["name"],
                domains=_list_field(value, "domains", required=True),
                profile_id=value["profile_id"],
                enabled=value.get("enabled", True),
            ),
        ),
        everyone_else_profile_id=everyone_else_profile_id,
        unaffected_devices=unaffected_devices,
        general_profile_id=general_profile_id,
        service_lists=service_lists,
        ad_blocking_devices=ad_devices,
        ad_blocking_allowances=ad_allowances,
    )


def _snapshot_hostname(snapshot: Optional[Dict[str, Any]], mac_address: Any) -> Optional[str]:
    """Find the hostname a station announced in a connected-client snapshot."""

    try:
        wanted = normalize_exempt_mac(mac_address)
    except ValueError:
        return None
    stations = snapshot.get("devices") if isinstance(snapshot, dict) else None
    for station in stations if isinstance(stations, list) else ():
        if isinstance(station, dict) and station.get("mac_address") == wanted:
            hostname = station.get("hostname")
            return hostname if isinstance(hostname, str) else None
    return None


def _new_client_name(label: Optional[str], hostname: Optional[str], ipv4_address: Any) -> str:
    """Name a client saved for the first time.

    Use the saved label then the announced hostname. A candidate that is not a
    usable name is skipped and the automatic placeholder is the last resort.
    """

    for candidate in (label, hostname):
        name = candidate.strip() if isinstance(candidate, str) else ""
        if name and len(name) <= 64 and not has_hidden_characters(name):
            return name
    return f"Client {ipv4_address}"


def _connected_device_from_payload(
    payload: Dict[str, Any], repository: ConfigRepository, *, hostname: Optional[str] = None
) -> Tuple[Device, Optional[Device]]:
    """Build an assignment for a visible client while preserving existing names and IDs.

    A client saved for the first time takes its saved label or else the hostname
    it announced. An existing assignment keeps its name. The saved row that was
    read comes back too, so the save can refuse a row changed meanwhile.
    """

    ipv4_address = payload["ipv4_address"]
    profile_id = payload["profile_id"]
    mac_address = normalize_exempt_mac(payload["mac_address"])
    if repository.get_profile(profile_id) is None:
        raise ValueError("profile does not exist")

    address_only = Device(
        id="connected-client",
        name="Connected client",
        ipv4_address=ipv4_address,
        profile_id=profile_id,
        mac_address=mac_address,
    )
    existing = repository.get_device_by_mac_address(mac_address)
    occupant = repository.get_device_by_ipv4_address(str(address_only.ipv4_address))
    if occupant is not None and occupant.mac_address not in (None, mac_address):
        raise ValueError(
            "This IP belongs to a different saved device. Update its old IP assignment first."
        )
    if occupant is not None and existing is not None and occupant.id != existing.id:
        raise ValueError("This IP conflicts with another saved assignment")
    existing = existing or occupant
    generated_id = "client-" + mac_address.replace(":", "")
    if existing is None and repository.get_device(generated_id) is not None:
        raise ValueError("The detected client ID conflicts with another saved assignment")
    label = repository.list_connected_device_labels().get(mac_address, {}).get("display_name")
    device = Device(
        id=existing.id if existing else generated_id,
        name=(
            existing.name
            if existing
            else _new_client_name(label, hostname, address_only.ipv4_address)
        ),
        ipv4_address=address_only.ipv4_address,
        profile_id=profile_id,
        mac_address=mac_address,
    )
    return device, existing


def _authorization_matches(
    *, expected_token: Optional[str], authorization_header: Optional[str]
) -> bool:
    if expected_token is None or authorization_header is None:
        return False
    scheme, separator, token = authorization_header.partition(" ")
    # Compare bytes: compare_digest rejects str values with non-ASCII characters.
    return (
        separator == " "
        and scheme.lower() == "bearer"
        and hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8"))
    )


class TerminationRequested(KeyboardInterrupt):
    """The service manager asked the dashboard to stop. It takes the same exit path as Ctrl-C."""


def _stop_on_sigterm(signum: int, frame: Any) -> None:
    # Only the first SIGTERM acts. A repeat while the dashboard is already closing up must not
    # cut into that cleanup, and systemd follows up with SIGKILL when the cleanup is too slow.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise TerminationRequested()


def run_server(
    *,
    database_path: DatabasePath,
    host: str = "127.0.0.1",
    port: int = 8080,
    admin_token: Optional[str] = None,
    helper_socket_path: Optional[Path] = None,
    helper_timeout_seconds: float = 8.0,
    tls_certfile: Optional[Path] = None,
    tls_keyfile: Optional[Path] = None,
    homepage_host: Optional[str] = None,
    homepage_certfile: Path = Path("/etc/jittersim/dashboard-wifi.crt"),
    tls_wifi_certfile: Optional[Path] = None,
    trust_ca_path: Optional[Path] = None,
) -> None:
    """Run the dashboard until the process receives Ctrl-C."""

    helper_client = (
        None
        if helper_socket_path is None
        else HelperClient(
            socket_path=helper_socket_path,
            timeout_seconds=helper_timeout_seconds,
        )
    )
    repository = ConfigRepository(database_path)
    scheduler = RuntimeScheduler(database_path=database_path, helper_client=helper_client)
    try:
        server = ControlServer(
            (host, port), repository, admin_token=admin_token, helper_client=helper_client,
            scheduler=scheduler, tls_certfile=tls_certfile, tls_keyfile=tls_keyfile,
            homepage_host=homepage_host, homepage_certfile=homepage_certfile,
            tls_wifi_certfile=tls_wifi_certfile, trust_ca_path=trust_ca_path,
        )
    except BaseException:
        repository.close()
        raise
    LOGGER.info("jittersim dashboard listening on %s:%s", host, port)
    try:
        server.recover_saved_network_state()
        server.serve_forever()
    except KeyboardInterrupt as interrupt:
        LOGGER.info("jittersim dashboard stopping (%s)",
                    "SIGTERM" if isinstance(interrupt, TerminationRequested) else "interrupt")
    finally:
        scheduler.begin_shutdown()
        unresolved = []
        try:
            server.server_close()
        except WorkersStillRunning as error:
            unresolved.append(error)
        try:
            scheduler.stop()
        except WorkersStillRunning as error:
            unresolved.append(error)
        if unresolved:
            # A failing service exit lets the process stop its remaining daemon
            # workers. Do not dispose of resources that they may still be using.
            LOGGER.critical("dashboard shutdown is incomplete, shared state remains owned")
            raise unresolved[0]
        repository.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the jittersim dashboard.")
    parser.add_argument("--database", type=Path, default=Path("jittersim.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--helper-socket", type=Path)
    parser.add_argument("--helper-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--homepage-cert", type=Path, default=Path("/etc/jittersim/dashboard-wifi.crt"))
    parser.add_argument("--homepage-host", choices=("192.168.50.1",),
                        default=os.environ.get("JITTERSIM_HOMEPAGE_HOST"))
    parser.add_argument("--tls-cert-wifi", type=Path,
                        help="certificate for test Wi-Fi clients (neutral names only)")
    parser.add_argument("--trust-ca", type=Path,
                        help="public certificate of the local authority offered for download")
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Start the dashboard. SIGTERM from the service manager stops it cleanly."""

    # `systemctl stop` sends SIGTERM. Without a handler it ends the process in the middle of a
    # request, with the scheduler and the database still open.
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _stop_on_sigterm)
    try:
        return _serve(argv)
    except TerminationRequested:
        # SIGTERM arrived before the server was serving. Nothing is left running.
        return 0
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def _serve(argv: Optional[Sequence[str]] = None) -> int:
    """Parse server options and start the dashboard."""

    arguments = _build_parser().parse_args(argv)
    configure_logging(level=getattr(logging, arguments.log_level))
    run_server(
        database_path=arguments.database,
        host=arguments.host,
        port=arguments.port,
        admin_token=os.environ.get("JITTERSIM_ADMIN_TOKEN"),
        helper_socket_path=arguments.helper_socket,
        helper_timeout_seconds=arguments.helper_timeout_seconds,
        tls_certfile=arguments.tls_cert,
        tls_keyfile=arguments.tls_key,
        homepage_host=arguments.homepage_host,
        homepage_certfile=arguments.homepage_cert,
        tls_wifi_certfile=arguments.tls_cert_wifi,
        trust_ca_path=arguments.trust_ca,
    )
    return 0
