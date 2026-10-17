"""Authenticated routes and the measurement window of the connection check.

The routes sit behind the admin token and the saved-device check like every dashboard route (the
request handler refuses before routing). A check runs on a daemon thread of the speed test
manager's run table, so a device never has a speed test and a check at once and the two together
never pass the manager's limit. See docs/implementation-plan-connection-check-2026-10-17.md.
"""

import logging
import secrets
import sqlite3
from dataclasses import asdict
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from jittersim.api.speed_tests import (
    MAX_ACTIVE_RUNS,
    SpeedTestRequestError,
    SpeedTestRun,
    _address_owner,
    _identity,
    _payload_id,
    _query_mac,
    pick_queues,
    response,
)
from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.speed_tests import ConnectionCheckStore, clean_label
from jittersim.config.storage import StorageError
from jittersim.connection_check import (
    CHECK_PROVIDER,
    DEFAULT_PROBES,
    MAX_PROBES,
    METRIC_LABELS,
    MIN_PROBES,
    PAYLOAD_BYTES,
    PROBE_INTERVAL_SECONDS,
    SERVICE_RULES_CHECK_NOTE,
    SERVICE_RULES_UNKNOWN_CHECK_NOTE,
    STATUS_LABELS,
    VERDICT_LABELS,
    build_summary,
    check_export_csv,
    check_export_json,
    grace_seconds,
    run_probe_burst,
    waits_for_late_copies,
)
from jittersim.networking.helper import HelperClientError
from jittersim.speed_test import SOURCE_LABELS, IcmpProber, NoProber

LOGGER = logging.getLogger(__name__)

CHECK_READS = frozenset(
    ("/api/connection-checks", "/api/connection-checks/status", "/api/connection-checks/export")
)
CHECK_WRITES = frozenset(
    ("/api/connection-checks/start", "/api/connection-checks/cancel",
     "/api/connection-checks/delete")
)
# The loop stops at the deadline whatever the prober does: the burst, the grace time and a
# little time for the final reads.
DEADLINE_MARGIN_SECONDS = 2.0
# The applied plan is read from memory (no helper call) this often during the burst to see a
# profile that changes while the probes are on their way.
WATCH_SECONDS = 1.0
UNAVAILABLE_MESSAGE = (
    "The dashboard cannot send ping probes to this device just now ({reason}). Use Test speed "
    "instead.")
NO_ADDRESS_MESSAGE = "This device has no address yet, so the Pi cannot send it probes."


class ConnectionCheckRun(SpeedTestRun):
    kind = "check"

    def __init__(self, **fields):
        # The base class starts a run without an identity: set it after.
        identity = fields.pop("identity", None)
        super().__init__(**fields)
        self.identity = identity

    def payload(self, now: float) -> Dict[str, Any]:
        """The run as the page sees it. `now` is on the monotonic clock, like the deadline."""

        sent, received = self.prober.counts()
        return {
            "id": self.id,
            "kind": "check",
            "mac_address": self.mac_address,
            "device_name": self.device_name,
            "label": self.label,
            "probes_planned": self.probes_total,
            "probes_sent": sent,
            "probes_received": received,
            "started_at": int(self.started_at),
            "seconds_left": (
                max(0, int(round(self.deadline - now))) if self.state == "running" else 0
            ),
            "source": self.source,
            "source_label": SOURCE_LABELS[self.source],
            "source_note": self.source_note,
            "protected": self.protected,
            "state": self.state,
            "ended_reason": self.ended_reason,
            "result": self.result,
        }


class ConnectionCheckManager:
    """Starts, ends and saves connection checks on top of the speed test manager."""

    def __init__(self, speed):
        self.speed = speed

    @property
    def repository(self):
        return self.speed.repository

    def _store(self) -> ConnectionCheckStore:
        return ConnectionCheckStore(self.repository)

    # Starting a check.

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"mac_address", "probes", "label"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise SpeedTestRequestError("Only mac_address, probes and label are allowed")
        try:
            mac_address = normalize_exempt_mac(payload.get("mac_address"))
        except (TypeError, ValueError) as error:
            raise SpeedTestRequestError("Choose a connected device") from error
        probes = payload.get("probes", DEFAULT_PROBES)
        if (isinstance(probes, bool) or not isinstance(probes, int)
                or not MIN_PROBES <= probes <= MAX_PROBES):
            raise SpeedTestRequestError(
                f"The number of probes must be {MIN_PROBES} to {MAX_PROBES}")
        try:
            label = clean_label(payload.get("label"))
        except ValueError as error:
            raise SpeedTestRequestError(str(error)) from error
        # Refuse early, before any helper read. The limits are checked again under the lock.
        self.speed._check_room(mac_address)
        # The helper reads can take seconds, so they run without the manager lock.
        run = self._new_run(mac_address, probes, label)
        # A protected device is allowed: it has no profile, so the check reports the baseline.
        return self.speed._register(
            run, self._loop, name="connection-check", refuse_protected=False)

    def _new_run(self, mac_address: str, probes: int, label: str) -> ConnectionCheckRun:
        speed = self.speed
        protected = speed._protected(mac_address)
        target = speed._resolve_target(mac_address, with_queues=not protected)
        if target.ipv4 is None:
            raise SpeedTestRequestError(NO_ADDRESS_MESSAGE, 409)
        impairment: Optional[Dict[str, Any]] = None
        burst: Optional[int] = None
        identity: Optional[Dict[str, Any]] = None
        if target.source == "device" and not protected:
            entry = target.entries.get("download")
            if entry is not None:
                impairment = dict(entry.get("impairment") or {})
                identity = _identity(entry)
                burst = _download_burst(target.plan, target.queue_id)
        notes = list(target.notes)
        if target.source == "device" and not protected:
            if target.service_rules is True:
                notes.append(SERVICE_RULES_CHECK_NOTE)
            elif target.service_rules is None:
                notes.append(SERVICE_RULES_UNKNOWN_CHECK_NOTE)
        if protected:
            notes.append("This device is protected, so no profile is applied to it.")
        elif target.reset:
            notes.append("The network is reset or the helper cannot read queues.")
        shaped = target.source == "device" and not protected
        grace = grace_seconds(impairment, shaped=shaped)
        factory = getattr(speed.server, "connection_check_prober_factory", None)
        if factory is not None:
            prober = factory(target.ipv4, probes)
        else:
            prober = IcmpProber(target.ipv4, max_probes=probes, payload_size=PAYLOAD_BYTES)
        if isinstance(prober, NoProber) or getattr(prober, "status", "measured") == "unavailable":
            reason = getattr(prober, "reason", None) or "not allowed"
            prober.close()
            raise SpeedTestRequestError(UNAVAILABLE_MESSAGE.format(reason=reason), 503)
        return ConnectionCheckRun(
            id=secrets.token_hex(8),
            mac_address=mac_address,
            device_id=target.device.id if target.device else None,
            queue_id=target.queue_id,
            device_name=target.name[:64],
            ipv4_address=target.ipv4,
            label=label,
            probes_total=probes,
            started_at=target.now,
            started_mono=target.mono,
            deadline=(target.mono + probes * PROBE_INTERVAL_SECONDS + grace
                      + DEADLINE_MARGIN_SECONDS),
            grace=grace,
            settle=waits_for_late_copies(impairment),
            drift_reason=None,
            last_watch=target.mono,
            signature=None if protected else _signature(
                speed, target.plan, target.states, target.source, target.ipv4),
            source="none" if protected else target.source,
            source_note=" ".join(notes) or None,
            service_rules=target.service_rules,
            protected=protected,
            impairment=impairment,
            burst_packets=burst,
            identity=identity,
            profile=speed._profile_snapshot(
                target.owner, target.queue_id, "none" if protected else target.source,
                {} if protected else target.entries, target.plan, target.states),
            prober=prober,
        )

    # Sampling.

    def _loop(self, run: ConnectionCheckRun) -> None:
        speed = self.speed
        try:
            finished = run_probe_burst(
                run.prober, run.probes_total, interval=PROBE_INTERVAL_SECONDS, grace=run.grace,
                clock=speed.monotonic, sleep=speed.sleep, deadline=run.deadline,
                stop=lambda: run.state != "running", settle=run.settle,
                on_tick=lambda: self._watch(run))
            if finished and run.state == "running":
                # The helper reads run outside the manager lock, once each: a slow helper must
                # not keep the window open for long.
                end = self._read_end_state(run)
                with speed.lock:
                    if run.state == "running":
                        self._end(run, "complete", end)
        except Exception:  # a fault must not leave a check open forever
            LOGGER.exception("the connection check failed")
            with speed.lock:
                if run.state == "running":
                    try:
                        self._end(run, "fault", {}, reason="The check stopped after an error.")
                    except Exception:
                        LOGGER.exception("the connection check could not be ended cleanly")
        finally:
            run.prober.close()

    def _watch(self, run: ConnectionCheckRun) -> bool:
        """Look at the applied plan once per second. True ends the burst: the profile changed."""

        speed = self.speed
        now = speed.monotonic()
        if run.signature is None or now - run.last_watch < WATCH_SECONDS:
            return False
        run.last_watch = now
        current = _signature(speed, *speed._applied_snapshot(), run.source, run.ipv4_address)
        if current is not None and current != run.signature:
            run.drift_reason = "The profile applied to this device changed during the check."
            return True
        return False

    def _read_end_state(self, run: ConnectionCheckRun) -> Dict[str, Any]:
        """What the Pi sees after the burst: is it still the same device on the same profile."""

        speed = self.speed
        end: Dict[str, Any] = {
            "associated": None, "address": None, "identity": None, "identity_known": False}
        if run.state != "running":
            return end
        helper = speed.server.helper_client
        snapshot_read = getattr(helper, "connected_device_snapshot", None) if helper else None
        if snapshot_read is not None:
            try:
                snapshot = snapshot_read()
            except HelperClientError:
                snapshot = None
            if isinstance(snapshot, dict) and snapshot.get("association_status_available") is True:
                here = [client for client in snapshot.get("devices", ())
                        if client.get("mac_address") == run.mac_address
                        and client.get("association_verified") is True]
                end["associated"] = bool(here)
                end["address"] = here[0].get("ipv4_address") if here else None
        if run.identity is not None and run.state == "running":
            try:
                queues = speed._read_queues()
            except HelperClientError:
                return end
            end["identity_known"] = True
            if queues is not None:
                _, entries = pick_queues(queues, run.queue_id, prefer=run.source)
                if "download" in entries:
                    end["identity"] = _identity(entries["download"])
        return end

    # Ending.

    def _changed_reason(self, run: ConnectionCheckRun, end: Dict[str, Any]) -> Optional[str]:
        if run.drift_reason is not None:
            return run.drift_reason
        if end.get("associated") is False:
            return "The device left the test Wi-Fi during the check."
        if end.get("associated") is True and end.get("address") != run.ipv4_address:
            # A connected device whose address is missing or different is not the one probed.
            return "The device's address changed during the check."
        if self.speed._protected(run.mac_address) != run.protected:
            return "The device's protection changed during the check."
        if run.identity is not None and end.get("identity_known"):
            if end.get("identity") != run.identity:
                return "The profile applied to this device changed during the check."
        if run.signature is not None:
            final = _signature(self.speed, *self.speed._applied_snapshot(), run.source,
                               run.ipv4_address)
            if final is not None and final != run.signature:
                return "The profile applied to this device changed during the check."
        return None

    def _end(self, run: ConnectionCheckRun, status: str, end: Dict[str, Any],
             *, reason: Optional[str] = None) -> None:
        """End a running check and save its result. The caller holds the manager lock."""

        speed = self.speed
        run.state = "ended"
        run.ended_reason = reason
        if status == "cancelled":
            run.ended_reason = "Cancelled. Nothing was saved."
            return
        changed = self._changed_reason(run, end) if status == "complete" else None
        if changed is not None:
            status = "changed"
            run.ended_reason = changed
        unchanged: Optional[bool] = None
        if run.identity is not None and end.get("identity_known"):
            unchanged = changed is None
        notes = [run.source_note] if run.source_note else []
        unverified = None
        if (run.identity is not None and status == "complete" and changed is None and not (
                end.get("identity_known") and end.get("associated") is not None)):
            # A shaped device whose end state could not be read: the settings may have changed.
            unverified = ("The Pi could not confirm at the end that the device stayed connected "
                          "on the same profile.")
        summary = build_summary(
            run.prober.records(), planned=run.probes_total, source=run.source,
            protected=run.protected, impairment=run.impairment, burst_packets=run.burst_packets,
            service_rules=run.service_rules, changed_reason=changed, unverified_reason=unverified,
            fault_reason=run.ended_reason if status == "fault" else None,
            probe_status=getattr(run.prober, "status", "measured"),
            probe_reason=getattr(run.prober, "reason", None), notes=notes,
            seconds=round(speed.monotonic() - run.started_mono, 1),
            profile_unchanged=unchanged, grace=round(run.grace, 1))
        if status == "fault":
            summary["ended_reason"] = run.ended_reason
        row = {
            "started_at": run.started_at,
            "finished_at": speed.clock(),
            "mac_address": run.mac_address,
            "device_id": run.device_id,
            "device_name": run.device_name,
            "label": run.label,
            "status": status,
            "provider": CHECK_PROVIDER,
            "source": run.source,
            "protected": run.protected,
            "profile": run.profile,
            "summary": summary,
        }
        try:
            run.result = self._store().save(row)
        except (StorageError, sqlite3.Error):
            run.ended_reason = "The result could not be saved."

    def cancel(self, run_id: Any) -> Dict[str, Any]:
        speed = self.speed
        with speed.lock:
            run = speed._running(run_id, "check")
            self._end(run, "cancelled", {})
            return run.payload(speed.monotonic())

    def status(self, run_id: Optional[str]) -> Dict[str, Any]:
        return self.speed.status(run_id, "check")


def _signature(speed, plan, states, source, ipv4):
    """What decides the shaping of one address, from the applied plan in memory.

    It names the saved device whose queue the plan marks this address into (none for a device
    nothing shapes), the impairment and source (kind, identity, profile) of that queue, whether
    shared profiles exist and the service rules. Two readings that differ mean the profile
    changed in between (a schedule, a timer, a new apply or a reset), also for a device that
    started without a profile. None when there is no readable plan. A change that went away again between two readings is not seen: the check
    promises to catch changes it observes, not every transient.
    """

    if plan is None or states is None:
        return None
    owner = _address_owner(plan, ipv4)
    spec = next((queue for queue in getattr(plan, "queues", ())
                 if owner is not None and queue.device_id == owner
                 and queue.direction.value == "download"), None)
    state = states.get(owner) if owner is not None else None
    source_key = None
    if state is not None:
        # The identity tells two sources apart. The label and the step only word the message.
        source_key = (state.source.kind, state.source.identity, state.source.profile)
    return (
        owner,
        None if spec is None else tuple(sorted(asdict(spec.impairment).items(), key=str)),
        source_key,
        # Only whether a shared profile exists: its resolved website addresses and its outage
        # flag change without any effect on the Pi's own probes, and must not mark a good check.
        getattr(plan, "general_profile", None) is not None,
        getattr(plan, "everyone_else", None) is not None,
        speed._service_rules_apply(owner, source, plan, states),
    )


def _download_burst(plan, queue_id) -> Optional[int]:
    """The burst length of the download queue in the applied plan. The helper's client drops it."""

    for queue in getattr(plan, "queues", ()):
        if queue.device_id == queue_id and queue.direction.value == "download":
            return queue.impairment.loss_burst_packets
    return None


def connection_check_manager(server) -> ConnectionCheckManager:
    # The server makes its one manager when it is built.
    return server._connection_check_manager


def read_connection_check(server, path: str, query: str = ""):
    if path not in CHECK_READS:
        return None
    try:
        fields = parse_qs(query, max_num_fields=8)
    except ValueError:
        return response({"error": "Too many query fields"}, 400)
    try:
        return _read(server, path, fields)
    except SpeedTestRequestError as error:
        return response({"error": str(error)}, error.status)
    except StorageError:
        LOGGER.exception("could not read the connection check results")
        return response({"error": "The connection check results could not be read."}, 500)


def _read(server, path, query):
    manager = connection_check_manager(server)
    store = ConnectionCheckStore(server.repository)
    if path == "/api/connection-checks":
        mac_address = _query_mac(query)
        speed = manager.speed
        runs = [run.payload(speed.monotonic()) for run in speed.active("check")
                if mac_address is None or run.mac_address == mac_address]
        return response({
            "limits": {
                "default_probes": DEFAULT_PROBES,
                "min_probes": MIN_PROBES,
                "max_probes": MAX_PROBES,
                "probes_per_second": round(1 / PROBE_INTERVAL_SECONDS),
                "max_active": MAX_ACTIVE_RUNS,
            },
            "metric_labels": METRIC_LABELS,
            "status_labels": STATUS_LABELS,
            "verdict_labels": VERDICT_LABELS,
            "source_labels": SOURCE_LABELS,
            "active": runs,
            "results": store.list(mac_address, limit=50 if mac_address else 200),
        })
    if path == "/api/connection-checks/status":
        return response(manager.status(query.get("run", [None])[0]))
    form = query.get("format", ["json"])[0]
    if form not in ("csv", "json"):
        raise SpeedTestRequestError("format must be csv or json")
    results = store.list(_query_mac(query), limit=500)
    if form == "csv":
        return response(check_export_csv(results), content_type="text/csv; charset=utf-8")
    return response(check_export_json(results), content_type="application/json")


def write_connection_check(server, path: str, payload: Dict[str, Any]):
    if path not in CHECK_WRITES:
        return None
    try:
        return _write(server, path, payload)
    except SpeedTestRequestError as error:
        return response({"error": str(error)}, error.status)
    except StorageError:
        LOGGER.exception("could not save the connection check results")
        return response({"error": "The connection check results could not be saved."}, 500)


def _write(server, path, payload):
    manager = connection_check_manager(server)
    if path == "/api/connection-checks/start":
        return response(manager.start(payload), 201)
    if path == "/api/connection-checks/cancel":
        if set(payload) != {"run_id"}:
            raise SpeedTestRequestError("Send run_id only")
        return response(manager.cancel(payload["run_id"]))
    if set(payload) != {"result_id"}:
        raise SpeedTestRequestError("Send result_id only")
    if not ConnectionCheckStore(server.repository).delete(_payload_id(payload)):
        raise SpeedTestRequestError("This result is not saved", 404)
    return response({"deleted": True})

