"""Private prepared choices and strict explicit profile-use validation."""

import json
from dataclasses import asdict
from ipaddress import IPv4Address
from urllib.parse import parse_qs

from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.pending_devices import (
    MAX_PENDING_REVISION,
    PendingDeviceChoice,
    validate_pending_integer,
)
from jittersim.config.storage import StorageError

PENDING_PATH = "/api/pending-devices"
PENDING_WRITES = frozenset((PENDING_PATH, PENDING_PATH + "/delete"))
USE_FIELDS = frozenset(("pending_mac", "expected_pending_revision", "expected_configuration_version"))
CHOICE_FIELDS = frozenset(("mac_address", "action", "profile_id", "display_name", "source",
                           "observed_at", "expected_revision"))


def _response(status, payload):
    return status, "application/json", json.dumps(payload).encode("utf-8")


def _shape(payload, allowed, required):
    if not isinstance(payload, dict) or set(payload) - allowed or required - set(payload):
        raise ValueError("Invalid pending request fields")


def _revision(value, name="expected revision", minimum=1):
    return validate_pending_integer(value, name, MAX_PENDING_REVISION, minimum)


def read_pending(repository, path, query):
    if path not in (PENDING_PATH, PENDING_PATH + "/review"):
        return None
    try:
        if path == PENDING_PATH:
            if query:
                raise ValueError("Pending list takes no query")
            return _response(200, {"pending_devices": [asdict(row) for row in
                                                       repository.list_pending_device_choices()]})
        parameters = parse_qs(query, keep_blank_values=True)
        if set(parameters) != {"mac"} or len(parameters["mac"]) != 1:
            raise ValueError("Review requires exactly one MAC")
        with repository.transaction():
            pending = repository.get_pending_device_choice(parameters["mac"][0])
            if pending is None:
                raise ValueError("Pending choice no longer exists")
            profile = repository.get_profile(pending.profile_id) if pending.profile_id else None
            if pending.action == "profile" and profile is None:
                raise ValueError("Pending profile no longer exists")
            result = {"pending_device": asdict(pending),
                      "profile": asdict(profile) if profile else None,
                      "configuration_version": repository.get_runtime_state().configuration_version}
        return _response(200, result)
    except (TypeError, ValueError, StorageError) as error:
        return _response(409, {"error": str(error)})


def write_pending(repository, path, payload):
    if path not in PENDING_WRITES:
        return None
    try:
        if path.endswith("/delete"):
            fields = {"mac_address", "expected_revision"}
            _shape(payload, fields, fields)
            repository.delete_pending_device_choice(payload["mac_address"],
                expected_revision=_revision(payload["expected_revision"]))
            return _response(200, {"removed": True})
        _shape(payload, CHOICE_FIELDS, {"mac_address", "action"})
        expected = payload.get("expected_revision")
        if expected is not None:
            _revision(expected)
        pending = PendingDeviceChoice(**{key: value for key, value in payload.items()
                                         if key != "expected_revision"})
        saved = repository.save_pending_device_choice(pending, expected_revision=expected)
        return _response(200, {"pending_device": asdict(saved), "prepared": True})
    except (TypeError, ValueError, StorageError) as error:
        return _response(409, {"error": str(error)})


def pending_use_request(payload):
    """A pending marker selects a strict alternative to the legacy payload."""

    if not set(payload) & USE_FIELDS:
        return None
    _shape(payload, USE_FIELDS, USE_FIELDS)
    return {"pending_mac": normalize_exempt_mac(payload["pending_mac"]),
            "expected_pending_revision": _revision(payload["expected_pending_revision"]),
            "expected_configuration_version": _revision(payload["expected_configuration_version"],
                                                         "configuration version", 0)}


def current_pending_profile(repository, use):
    """Recheck inside the caller's final save transaction before any mutation."""

    pending = repository.get_pending_device_choice(use["pending_mac"])
    if pending is None or pending.revision != use["expected_pending_revision"]:
        raise ValueError("Pending choice changed. Refresh its review.")
    if pending.action != "profile":
        raise ValueError("Pending protection use is not available")
    profile = repository.get_profile(pending.profile_id)
    if profile is None:
        raise ValueError("Pending profile no longer exists")
    if repository.get_runtime_state().configuration_version != use["expected_configuration_version"]:
        raise ValueError("Configuration changed. Refresh the pending review.")
    return pending


def pending_target(snapshot, mac):
    """Use only one fresh associated reachable helper row with exclusive IP ownership."""

    if snapshot.get("stale", False) is not False or snapshot.get("association_status_available") is not True:
        raise ValueError("Current Wi-Fi identity is unavailable. Refresh the list.")
    rows = snapshot.get("devices")
    if not isinstance(rows, list):
        raise ValueError("Invalid current Wi-Fi client list")
    matching = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid current Wi-Fi client")
        try:
            row_mac = normalize_exempt_mac(row.get("mac_address"))
        except (TypeError, ValueError):
            row_mac = None
        if row_mac == mac:
            matching.append(row)
    if len(matching) != 1:
        raise ValueError("Pending MAC has no unique current Wi-Fi client")
    selected = matching[0]
    if selected.get("association_verified") is not True or selected.get("neighbor_state") != "reachable":
        raise ValueError("Current Wi-Fi client is not verified and reachable")
    address = selected.get("ipv4_address")
    if not isinstance(address, str):
        raise ValueError("Current Wi-Fi client has no valid IPv4")
    address = IPv4Address(address)
    for row in rows:
        if row is selected:
            continue
        try:
            other_address = IPv4Address(row.get("ipv4_address"))
        except (TypeError, ValueError):
            continue
        if other_address == address:
            raise ValueError("Current Wi-Fi IPv4 has another owner")
    return {"mac_address": mac, "ipv4_address": str(address)}
