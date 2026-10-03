"""Fresh read-only approval of controlled homepage test actions."""

import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from ipaddress import IPv4Address, ip_address
from pathlib import Path

from .browser_proxy import ProxyError, _check_mac_access, destination_allowed, select_mark
from .dashboard_access import AccessError, identify_peer
from .executor import CommandExecutionError


class DeadlineExecutor:
    """Keep every identity command within one request deadline."""

    def __init__(self, executor, deadline):
        self.executor = executor
        self.deadline = deadline

    def run(self, command):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("identity deadline expired")
        result = self.executor.run(replace(command, timeout_seconds=remaining))
        if time.monotonic() >= self.deadline:
            raise TimeoutError("identity deadline expired")
        return result


def protection_snapshot(database: Path):
    """Read a bounded consistent snapshot without creating or migrating a database."""

    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.015)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        row = connection.execute(
            "SELECT configuration_version FROM runtime_state WHERE id=1"
        ).fetchone()
        macs = connection.execute("SELECT mac_address FROM unaffected_devices LIMIT 257").fetchall()
        if row is None or type(row[0]) is not int or len(macs) > 256:
            raise ValueError("saved protection is unavailable")
        return row[0], frozenset(value[0] for value in macs)


def policy_chance(plan, peer, host, mac, addresses, access=None):
    """Use the applied winning rule and refuse blocked or unsupported traffic."""

    if plan is None or plan.homepage_policy_version != 1 or not addresses:
        return 0
    if mac in plan.protected_mac_addresses:
        return 0
    try:
        current_access = access if access is not None else plan.access
        _check_mac_access(
            current_access.mac
            if current_access and current_access.options.enabled
            else plan.mac_policy,
            mac,
        )
        if current_access and not current_access.permits_host(str(peer), host):
            return 0
        mark, source = select_mark(plan, client_ip=peer, host=host, client_mac=mac)
        listed = source.startswith("service-list:")
        if any(
            not ip_address(address).is_global
            or not destination_allowed(plan, peer, address, mac, service_list=listed)
            for address in addresses
        ):
            return 0
        # Destination quota precedence is not represented by the browser selector.
        # Keep it disabled when a matching active quota would replace the queue.
        if not listed and any(
            rule.active
            and rule.device_ip == peer
            and any(
                IPv4Address(address) in rule.destination_addresses
                for address in addresses
                if ip_address(address).version == 4
            )
            for rule in plan.service_quota_rules
        ):
            return 0
        if source == "general-profile":
            return plan.general_profile.homepage_bounce_percent
        rules = (
            plan.service_list_rules if listed else plan.service_assignment_rules + plan.mark_rules
        )
        return next((rule.homepage_bounce_percent for rule in rules if rule.mark == mark), 0)
    except (ProxyError, ValueError):
        return 0


def helper_snapshot(service, payload):
    """Prove a fresh peer outside write locks then check the approval boundary."""

    disabled = {"ok": True, "verified": False, "chance": 0}
    try:
        if set(payload) - {"action", "peer", "host", "addresses", "expected"}:
            return disabled
        peer = IPv4Address(payload["peer"])
        host = payload.get("host")
        addresses = payload.get("addresses", [])
        if not isinstance(addresses, list) or len(addresses) > 16:
            return disabled
        if host is not None:
            from jittersim.config.models import normalize_service_hostname

            host = normalize_service_hostname(host)
            if not addresses or any(not ip_address(value).is_global for value in addresses):
                return disabled
        if not service._network_lock.acquire(blocking=False):
            return disabled
        try:
            plan = service._active_plan
            generation = service._plan_count
        finally:
            service._network_lock.release()
        saved = protection_snapshot(service._homepage_database)
        from .applier import NetworkApplier

        # A read-only adapter uses the same topology and executor with a total budget.
        applier = NetworkApplier(
            executor=DeadlineExecutor(service._applier._executor, time.monotonic() + 0.45),
            topology=service._applier._topology,
        )
        identity = identify_peer(applier, str(peer), require_reachable=True)
        mac = identity.get("mac_address")
        if identity.get("network") != "test" or identity.get("can_register") is not True or not mac:
            return disabled
        if not service._network_lock.acquire(blocking=False):
            return disabled
        manager = service._access_manager
        access_locked = False
        try:
            if manager is not None:
                access_locked = manager.lock.acquire(blocking=False)
                if (
                    not access_locked
                    or manager.error
                    or manager.health_error
                    or manager.applied is None
                ):
                    return disabled
                # Detect external access restoration too without performing commands.
                if manager.generation != manager._generation():
                    return disabled
            latest = protection_snapshot(service._homepage_database)
            if (
                saved != latest
                or generation != service._plan_count
                or plan is not service._active_plan
            ):
                return disabled
            expected = payload.get("expected")
            if expected is not None and expected != service._homepage_epoch:
                return disabled
            return {
                "ok": True,
                "verified": True,
                "mac": mac,
                "epoch": service._homepage_epoch,
                "generation": generation,
                "saved_version": latest[0],
                "protected": mac in latest[1],
                "receipt": {
                    "epoch": service._homepage_epoch,
                    "plan": id(plan),
                    "generation": generation,
                    "saved_version": latest[0],
                    "mac": mac,
                    "access": manager.applied.digest() if manager else None,
                    "access_generation": list(manager.generation or ()) if manager else None,
                },
                "chance": 0
                if mac in latest[1] or host is None
                else policy_chance(
                    plan, peer, host, mac, addresses, manager.applied if manager else None
                ),
            }
        finally:
            if access_locked:
                manager.lock.release()
            service._network_lock.release()
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        sqlite3.Error,
        AccessError,
        CommandExecutionError,
        TimeoutError,
    ):
        return disabled


def policy_request(socket_path, payload):
    """Bound connect, write and read together without creating background work."""
    import json
    import socket

    from .helper import HelperClientError

    deadline = time.monotonic() + 0.55
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.connect(str(socket_path))
            encoded = json.dumps(payload).encode() + b"\n"
            if len(encoded) > 8192:
                raise ValueError("policy request is too large")
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.sendall(encoded)
            result = bytearray()
            while b"\n" not in result:
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = connection.recv(8193 - len(result))
                if not chunk or len(result) + len(chunk) > 8192 or time.monotonic() >= deadline:
                    raise ValueError("policy response unavailable")
                result.extend(chunk)
            response = json.loads(result)
            if not isinstance(response, dict) or response.get("ok") is not True:
                raise ValueError("policy response unavailable")
            return response
    except (OSError, ValueError) as error:
        raise HelperClientError("Homepage policy is unavailable") from error


def helper_confirm(service, payload):
    """Finish approval after the web session check without delaying control writes."""
    result = {"ok": True, "verified": False}
    receipt = payload.get("receipt")
    if not isinstance(receipt, dict) or set(receipt) != {
        "epoch",
        "plan",
        "saved_version",
        "mac",
        "access",
        "generation",
        "access_generation",
    }:
        return result
    if not service._network_lock.acquire(blocking=False):
        return result
    manager = service._access_manager
    locked = False
    try:
        if manager:
            locked = manager.lock.acquire(blocking=False)
            if not locked or manager.error or manager.health_error or manager.applied is None:
                return result
            if (
                manager.generation != manager._generation()
                or list(manager.generation or ()) != receipt["access_generation"]
                or manager.applied.digest() != receipt["access"]
            ):
                return result
        version, protected = protection_snapshot(service._homepage_database)
        result["verified"] = (
            receipt["epoch"] == service._homepage_epoch
            and receipt["plan"] == id(service._active_plan)
            and receipt["generation"] == service._plan_count
            and receipt["saved_version"] == version
            and receipt["mac"] not in protected
        )
        return result
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return result
    finally:
        if locked:
            manager.lock.release()
        service._network_lock.release()
