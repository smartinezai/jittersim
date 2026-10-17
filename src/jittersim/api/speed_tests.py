"""Authenticated routes for the device speed test.

The routes sit behind the admin token and the saved-device check like every dashboard route
(the request handler refuses before routing). A measurement window runs on a daemon thread
that reads the helper's read-only queue counters once per second and sends one ICMP echo to
the device. See docs/implementation-plan-device-speed-test-2026-10-09.md.
"""

import json
import logging
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.payloads import impairment_payload
from jittersim.config.speed_tests import SpeedTestStore, clean_label, clean_reported
from jittersim.config.storage import StorageError
from jittersim.engine.plan import EVERYONE_ELSE_QUEUE_ID, GENERAL_QUEUE_ID
from jittersim.networking.helper import HelperClientError
from jittersim.request_workers import WorkersStillRunning
from jittersim.speed_test import (
    CAP_VERDICTS,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_PROVIDER,
    DIRECTIONS,
    MAX_ACTIVE_RUNS,
    MAX_DURATION_SECONDS,
    MAX_SAMPLES,
    MIN_DURATION_SECONDS,
    PROVIDER_IDS,
    SAMPLE_INTERVAL_SECONDS,
    SERVICE_RULES_NOTE,
    SERVICE_RULES_UNKNOWN_NOTE,
    SHARED_LATENCY_NOTE,
    SHARED_SOURCES,
    SOURCE_LABELS,
    VERDICT_LABELS,
    IcmpProber,
    NoProber,
    compare_results,
    export_csv,
    export_json,
    per_second_rates,
    provider_payload,
    summarize_direction,
    unmeasured_direction,
)

LOGGER = logging.getLogger(__name__)

SPEED_TEST_READS = frozenset(
    ("/api/speed-tests", "/api/speed-tests/status", "/api/speed-tests/compare",
     "/api/speed-tests/export")
)
SPEED_TEST_WRITES = frozenset(
    ("/api/speed-tests/start", "/api/speed-tests/finish", "/api/speed-tests/cancel",
     "/api/speed-tests/update", "/api/speed-tests/delete")
)
# Ended runs stay readable for a while so the page can show how they ended.
KEEP_ENDED_RUNS = 8
PROTECTED_MESSAGE = "Protected devices are never shaped and their traffic is not measured."
# A busy helper gets a few more tries before the start is refused as something to retry.
READ_ATTEMPTS = 3
READ_RETRY_SECONDS = 0.25
# When the applied source of a device's profile cannot be confirmed, the row says so.
UNCONFIRMED_PROFILE = "Unknown (source not confirmed)"
PROGRESSION_PROFILE = "Progression (changes during the test)"
# Ids are SQLite integers. A larger number cannot exist, so it is a bad request, not a fault.
MAX_ID = 2**63 - 1


class SpeedTestRequestError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def response(value, status=200, content_type="application/json"):
    # Export files arrive as finished text; everything else is encoded here.
    body = value.encode("utf-8") if isinstance(value, str) else (
        json.dumps(value, allow_nan=False) + "\n").encode()
    return status, content_type, body


# The two kinds of measurement that share the manager, its limits and its shutdown. A connection
# check (docs/implementation-plan-connection-check-2026-10-17.md) lives in the same run table, so
# a device never has both at once and the two together never pass MAX_ACTIVE_RUNS.
KIND_NAMES = {"speed": "speed test", "check": "connection check"}


class SpeedTestRun:
    kind = "speed"

    def __init__(self, **fields):
        self.__dict__.update(fields)
        self.samples: List[Dict[str, Any]] = []
        self.state = "running"
        self.ended_reason: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None
        self.identity: Optional[Dict[str, Any]] = None
        # Sampling ticks that tried to read the counters, and how many of those reads failed.
        self.ticks = 0
        self.failed_reads = 0
        self.thread: Optional[threading.Thread] = None
        self.startup_uncertain = False

    def payload(self, now: float) -> Dict[str, Any]:
        """The run as the page sees it. `now` is on the monotonic clock, like the deadline."""

        live = {}
        for direction in DIRECTIONS:
            # The helper can leave out a direction whose queue is missing. It has no live number.
            measured = self.source != "none" and direction in (self.identity or {})
            rates = per_second_rates(self.samples[-2:], direction) if measured else []
            peak = per_second_rates(self.samples, direction) if measured else []
            live[direction] = {
                "kbit": round(rates[-1], 1) if rates else None,
                "peak_kbit": round(max(peak), 1) if peak else None,
            }
        return {
            "id": self.id,
            "mac_address": self.mac_address,
            "device_name": self.device_name,
            "provider": self.provider,
            "label": self.label,
            "duration_seconds": self.duration_seconds,
            "started_at": int(self.started_at),
            "seconds_left": (
                max(0, int(round(self.deadline - now))) if self.state == "running" else 0
            ),
            "source": self.source,
            "source_label": SOURCE_LABELS[self.source],
            "source_note": self.source_note,
            "state": self.state,
            "ended_reason": self.ended_reason,
            "samples": len(self.samples),
            "live": live,
            "result": self.result,
        }


class ResolvedDevice:
    """What the Pi knows about one connected device at the start of a measurement."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


# Best-effort bound within the server's shared shutdown budget, not a guaranteed reserve.
SAMPLER_SHUTDOWN_TIMEOUT_SECONDS = 4.0


class SpeedTestManager:
    """At most MAX_ACTIVE_RUNS windows, one per device, each with its own sampling thread.

    Wall time (`clock`) only dates the saved rows. The deadline and the counter intervals use
    the monotonic clock, so a clock change during a window cannot stretch or shrink it.
    """

    def __init__(self, server, *, autostart: bool = True, clock=time.time,
                 monotonic=time.monotonic, sleep=time.sleep):
        self.server = server
        self.autostart = autostart
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.lock = threading.RLock()
        self.runs: Dict[str, SpeedTestRun] = {}
        self.closed = False

    # Helpers that read the server's state.

    @property
    def repository(self):
        return self.server.repository

    def _store(self) -> SpeedTestStore:
        return SpeedTestStore(self.repository)

    def _protected(self, mac_address: str) -> bool:
        protected = self.repository.list_unaffected_devices()
        return any(item.mac_address == mac_address for item in protected)

    def _read_queues(self) -> Optional[List[Dict[str, Any]]]:
        helper = self.server.helper_client
        read = getattr(helper, "queue_stats", None) if helper is not None else None
        if read is None or self.repository.get_runtime_state().observed_state == "reset":
            return None
        return read()

    def _retry(self, read):
        """Call a helper read. A busy or slow helper gets a few more tries."""

        for attempt in range(READ_ATTEMPTS):
            try:
                return read()
            except HelperClientError:
                if attempt == READ_ATTEMPTS - 1:
                    raise
                self.sleep(READ_RETRY_SECONDS)

    def active(self, kind: Optional[str] = None) -> List[SpeedTestRun]:
        with self.lock:
            return [run for run in self.runs.values()
                    if run.state == "running" and (kind is None or run.kind == kind)]

    # Starting a window.

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"mac_address", "provider", "duration_seconds", "label"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise SpeedTestRequestError(
                "Only mac_address, provider, duration_seconds and label are allowed")
        try:
            mac_address = normalize_exempt_mac(payload.get("mac_address"))
        except (TypeError, ValueError) as error:
            raise SpeedTestRequestError("Choose a connected device") from error
        provider = payload.get("provider", DEFAULT_PROVIDER)
        if provider not in PROVIDER_IDS:
            raise SpeedTestRequestError("Choose one of the listed speed test sites")
        duration = payload.get("duration_seconds", DEFAULT_DURATION_SECONDS)
        if (isinstance(duration, bool) or not isinstance(duration, int)
                or not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS):
            raise SpeedTestRequestError(
                f"Length must be {MIN_DURATION_SECONDS} to {MAX_DURATION_SECONDS} seconds")
        try:
            label = clean_label(payload.get("label"))
        except ValueError as error:
            raise SpeedTestRequestError(str(error)) from error
        if self._protected(mac_address):
            raise SpeedTestRequestError(PROTECTED_MESSAGE, 409)
        # Refuse early, before any helper read. The limits are checked again under the lock.
        self._check_room(mac_address)
        # The helper reads can take seconds, so they run without the manager lock.
        run = self._new_run(mac_address, provider, duration, label)
        return self._register(run, self._loop, name="speed-test", refuse_protected=True)

    def _register(self, run: SpeedTestRun, loop, *, name: str, refuse_protected: bool
                  ) -> Dict[str, Any]:
        """Add a built run to the table and start its sampling thread. Takes the manager lock."""

        try:
            with self.lock:
                self._check_room(run.mac_address)
                if refuse_protected and self._protected(run.mac_address):
                    raise SpeedTestRequestError(PROTECTED_MESSAGE, 409)
                self.runs[run.id] = run
                self._trim()
                if self.autostart:
                    run.thread = threading.Thread(
                        target=loop, args=(run,), name=name, daemon=True)
                    try:
                        run.thread.start()
                    except BaseException:
                        # Treat a failed start as uncertain. Python may have created the native
                        # thread before the caller was interrupted, so keep both resources owned.
                        self.closed = True
                        run.startup_uncertain = (
                            run.thread.ident is None and not run.thread.is_alive())
                        run.state = "ended"
                        run.ended_reason = (
                            "The sampler could not start cleanly. Restart the dashboard.")
                        raise
                return run.payload(self.monotonic())
        except BaseException:
            if run.thread is None:
                run.prober.close()
            raise

    def _check_room(self, mac_address: str) -> None:
        with self.lock:
            if self.closed:
                raise SpeedTestRequestError("The dashboard is stopping.", 503)
            active = self.active()
            running = next((run for run in active if run.mac_address == mac_address), None)
            if running is not None:
                raise SpeedTestRequestError(
                    f"A {KIND_NAMES[running.kind]} is already running for this device", 409)
            if len(active) >= MAX_ACTIVE_RUNS:
                raise SpeedTestRequestError(
                    f"At most {MAX_ACTIVE_RUNS} measurements (speed tests and connection "
                    "checks together) can run at once. Wait for one to end.", 409)

    def _connected_client(self, mac_address: str) -> Dict[str, Any]:
        """The station entry of a device that is on the test Wi-Fi right now.

        It uses the snapshot the Devices page uses. A device that cannot be confirmed as
        connected is refused: its saved address may belong to another device by now.
        """

        helper = self.server.helper_client
        read = getattr(helper, "connected_device_snapshot", None) if helper is not None else None
        if read is None:
            raise SpeedTestRequestError(
                "The Pi helper is not connected, so the device's Wi-Fi connection cannot be "
                "checked.", 503)
        try:
            snapshot = self._retry(read)
        except HelperClientError as error:
            raise SpeedTestRequestError(
                "The Pi could not check the Wi-Fi connection just now. Try again in a moment.",
                503) from error
        if snapshot.get("association_status_available") is not True:
            raise SpeedTestRequestError(
                "The Wi-Fi connection status is unknown right now. Try again in a moment.", 503)
        for client in snapshot.get("devices", ()):
            if client.get("mac_address") == mac_address and client.get(
                    "association_verified") is True:
                return client
        raise SpeedTestRequestError("This device is not connected to the test Wi-Fi right now", 409)

    def _resolve_target(self, mac_address: str, *, with_queues: bool = True) -> "ResolvedDevice":
        """Read what the Pi knows about a connected device right now. It takes no manager lock.

        This is the part of starting a window that a speed test and a connection check share: the
        association check, the device's current address, the queue its traffic uses and the
        applied plan and service rules. `with_queues=False` skips the queue and plan reads (a
        protected device has none): the source is then `none`.
        """

        repository = self.repository
        client = self._connected_client(mac_address)
        # The address the device has now. The saved row may still hold an older one.
        ipv4 = client.get("ipv4_address")
        device = repository.get_device_by_mac_address(mac_address)
        if device is None and isinstance(ipv4, str):
            # A saved device without a MAC address is shaped by its address in its own queue.
            # The connected-device snapshot verified this client holds that address now.
            candidate = repository.get_device_by_ipv4_address(ipv4)
            if candidate is not None and candidate.mac_address is None:
                device = candidate
        labels = repository.list_connected_device_labels()
        name = (labels.get(mac_address, {}).get("display_name") or (device.name if device else "")
                or mac_address)
        now, mono = self.clock(), self.monotonic()
        target = ResolvedDevice(
            mac_address=mac_address, client=client, ipv4=ipv4, device=device, name=name,
            now=now, mono=mono, plan=None, states=None, owner=device,
            queue_id=device.id if device else None, source="none", entries={}, notes=[],
            service_rules=None, reset=False)
        if not with_queues:
            return target
        try:
            queues = self._retry(self._read_queues)
        except HelperClientError as error:
            raise SpeedTestRequestError(
                "The Pi could not read the queue counters just now. Try again in a moment.",
                503) from error
        now, mono = self.clock(), self.monotonic()
        target.now, target.mono = now, mono
        plan, states = self._applied_snapshot()
        target.plan, target.states = plan, states
        # Which saved device's queue carries this client's traffic. The applied plan marks
        # traffic by address, so its mark rule for the client's current address decides, not
        # the saved row (the scheduler can keep an address with an older device).
        owner, queue_id = device, (device.id if device else None)
        notes: List[str] = []
        if plan is not None:
            queue_id = _address_owner(plan, ipv4)
            if queue_id is None:
                owner = None
            elif device is None or queue_id != device.id:
                owner = repository.get_device(queue_id)
                notes.append(
                    "This device's current address is shaped by the queue of the saved device "
                    f"{owner.name if owner else queue_id}, so that queue and its profile are "
                    "measured.")
        if queues is None:
            source, entries = "none", {}
        elif plan is not None and queue_id is not None:
            # The plan marks this address into a dedicated queue, whatever its id looks like.
            source, entries = pick_queues(queues or [], queue_id, prefer="device")
            if not entries:
                # Never fall back to a shared queue: the plan keeps this address out of it.
                raise SpeedTestRequestError(
                    "The Pi could not find this device's queue counters just now. Try again "
                    "in a moment.", 503)
        else:
            source, entries = pick_queues(
                queues or [], None if plan is not None else queue_id, plan=plan)
        if plan is not None and entries and not _counters_match_plan(plan, queue_id_for(
                source, queue_id), entries):
            # The helper already runs a newer apply than the scheduler has published, or the
            # other way round. The profile would not describe the counters, so do not start.
            raise SpeedTestRequestError(
                "The Pi is applying a change right now. Try again in a moment.", 503)
        target.owner, target.queue_id, target.source, target.entries = (
            owner, queue_id, source, entries)
        target.notes = notes
        target.reset = queues is None
        target.service_rules = self._service_rules_apply(queue_id, source, plan, states)
        return target

    def _new_run(self, mac_address, provider, duration, label) -> SpeedTestRun:
        """Read what the Pi knows about the device and build the run. It takes no manager lock."""

        target = self._resolve_target(mac_address)
        source, entries, notes = target.source, target.entries, list(target.notes)
        if target.reset:
            notes.append("The network is reset or the helper cannot read queues.")
        elif source == "none":
            notes.append(
                "No profile applies to this device, so the Pi has no counter for it. Type in "
                "the numbers from the speed test page, or apply a profile with nothing set "
                "to measure it on the Pi without limits.")
        elif source in SHARED_SOURCES:
            notes.append("Other devices share this queue, so their traffic is counted too. "
                         + SHARED_LATENCY_NOTE)
        if target.service_rules is True:
            notes.append(SERVICE_RULES_NOTE)
        elif target.service_rules is None and source != "none":
            notes.append(SERVICE_RULES_UNKNOWN_NOTE)
        run = SpeedTestRun(
            id=secrets.token_hex(8),
            mac_address=mac_address,
            device_id=target.device.id if target.device else None,
            queue_id=target.queue_id,
            device_name=target.name[:64],
            ipv4_address=target.ipv4,
            provider=provider,
            label=label,
            duration_seconds=duration,
            started_at=target.now,
            started_mono=target.mono,
            deadline=target.mono + duration,
            source=source,
            source_note=" ".join(notes) or None,
            service_rules=target.service_rules,
            profile=self._profile_snapshot(
                target.owner, target.queue_id, source, entries, target.plan, target.states),
        )
        if source != "none":
            run.identity = {direction: _identity(entry) for direction, entry in entries.items()}
            run.samples.append(_sample(entries, target.mono))
        factory = getattr(self.server, "speed_test_prober_factory", None)
        if target.ipv4 is None:
            run.prober = NoProber()
        else:
            run.prober = factory(target.ipv4) if factory is not None else IcmpProber(target.ipv4)
        return run

    def _applied_snapshot(self):
        """Read one coherent scheduler snapshot, or return unknown when it is unavailable."""

        read = getattr(self.server.scheduler, "applied_snapshot", None)
        if read is None:
            return None, None
        try:
            snapshot = read()
        except Exception:
            LOGGER.warning("could not read the applied plan for a speed test")
            return None, None
        if not isinstance(snapshot, tuple) or len(snapshot) != 2:
            return None, None
        plan, states = snapshot
        if plan is None or not isinstance(states, dict):
            return None, None
        return plan, states

    @staticmethod
    def _plan_has_queue(plan, key):
        return any(queue.device_id == key for queue in getattr(plan, "queues", ()))

    def _general_rule(self, plan, states):
        """The applied plan's rule for the profile for everyone, when the plan confirms it.

        The scheduler's general plan keeps no per-queue source entry. Its general profile rule
        together with the general queue is what confirms the shared profile.
        """

        if (plan is None or states is None or states.get(GENERAL_QUEUE_ID) is not None
                or not self._plan_has_queue(plan, GENERAL_QUEUE_ID)):
            return None
        return getattr(plan, "general_profile", None)

    def _service_rules_apply(self, queue_id, source, plan, states) -> Optional[bool]:
        """Whether the applied plan diverts some traffic from the measured queue."""

        if source == "none":
            return None
        key = {
            "device": queue_id,
            "everyone": GENERAL_QUEUE_ID,
            "everyone-else": EVERYONE_ELSE_QUEUE_ID,
        }.get(source)
        confirmed = (key is not None and plan is not None and states is not None
                     and states.get(key) is not None and self._plan_has_queue(plan, key))
        if source == "everyone" and self._general_rule(plan, states) is not None:
            confirmed = True
        if not confirmed:
            return None

        if getattr(plan, "service_list_rules", ()):
            return True
        if queue_id is None:
            return False
        if any(rule.device_id == queue_id for rule in getattr(
                plan, "service_assignment_rules", ())):
            return True
        return any(rule.device_id == queue_id and rule.active for rule in getattr(
            plan, "service_quota_rules", ()))

    def _applied_profile(self, key, saved, plan, states):
        """What really applies to one queue: (profile, name, source kind, source label).

        The scheduler's applied snapshot says what controls the device: its saved profile, a
        group profile, a schedule, a timer, a scenario, a progression or a data limit. The saved
        assignment is only called applied when the snapshot confirms it.
        """

        if (plan is None or states is None or states.get(key) is None
                or not self._plan_has_queue(plan, key)):
            return None, UNCONFIRMED_PROFILE, "unknown", ""
        state = states[key]
        kind, name = state.source.kind, state.source.profile
        if kind == "none":
            return None, "No profile", kind, ""
        if kind == "progression":
            return None, PROGRESSION_PROFILE, kind, state.source.label
        if not name:
            return None, UNCONFIRMED_PROFILE, kind, state.source.label
        profile = saved if saved is not None and saved.name == name else self._profile_named(name)
        return profile, name, kind, state.source.label

    def _profile_named(self, name):
        matches = [profile for profile in self.repository.list_profiles() if profile.name == name]
        return matches[0] if len(matches) == 1 else None

    def _profile_snapshot(self, owner, queue_id, source, entries, plan, states) -> Dict[str, Any]:
        repository = self.repository
        profile, name, applied_by, applied_label = None, "No profile", "profile", ""
        if source == "device" and queue_id is not None:
            profile, name, applied_by, applied_label = self._applied_profile(
                queue_id, repository.get_profile(owner.profile_id) if owner else None,
                plan, states)
        elif source == "everyone":
            rule = self._general_rule(plan, states)
            if rule is not None:
                profile = repository.get_profile(rule.profile_id)
                name = profile.name if profile is not None else UNCONFIRMED_PROFILE
                applied_by, applied_label = "general", "Profile for everyone"
            else:
                profile, name, applied_by, applied_label = self._applied_profile(
                    GENERAL_QUEUE_ID, repository.get_general_profile(), plan, states)
        elif source == "everyone-else":
            identifier = repository.get_everyone_else_profile_id()
            profile, name, applied_by, applied_label = self._applied_profile(
                EVERYONE_ELSE_QUEUE_ID, repository.get_profile(identifier) if identifier else None,
                plan, states)
        general = repository.get_general_profile()
        return {
            "kind": source,
            "profile_id": profile.id if profile else None,
            "profile_name": name,
            "applied_by": applied_by,
            "applied_by_label": applied_label,
            "saved": (
                {"upload": impairment_payload(profile.upload),
                 "download": impairment_payload(profile.download)}
                if profile else None
            ),
            "applied": {direction: entry.get("impairment") for direction, entry in entries.items()},
            "general_profile": general.name if general else None,
            "network": repository.get_runtime_state().observed_state,
        }

    def _trim(self) -> None:
        ended = [run for run in self.runs.values()
                 if run.state != "running" and
                 not run.startup_uncertain and
                 (run.thread is None or not run.thread.is_alive())]
        for run in ended[:-KEEP_ENDED_RUNS] if len(ended) > KEEP_ENDED_RUNS else []:
            self.runs.pop(run.id, None)

    # Sampling.

    def _loop(self, run: SpeedTestRun) -> None:
        next_at = self.monotonic()
        try:
            while run.state == "running":
                run.prober.send()
                next_at += SAMPLE_INTERVAL_SECONDS
                run.prober.collect(until=next_at)
                self.sleep(max(0.0, next_at - self.monotonic()))
                self.tick(run.id)
        except Exception:  # a sampling fault must not leave a window open forever
            LOGGER.exception("the speed test sampler failed")
            with self.lock:
                if run.state == "running":
                    try:
                        self._end(run, "fault", reason="The measurement stopped after an error.")
                    except Exception:
                        LOGGER.exception("the speed test could not be ended cleanly")
        finally:
            run.prober.close()

    def tick(self, run_id: str) -> None:
        """Take one sample, then end the run when its time is up."""

        with self.lock:
            run = self.runs.get(run_id)
            if run is None or run.state != "running" or run.kind != "speed":
                return
        if self._protected(run.mac_address):
            with self.lock:
                if run.state == "running":
                    self._discard(run, PROTECTED_MESSAGE)
            return
        changed = False
        if run.source != "none":
            run.ticks += 1
            try:
                queues = self._read_queues()
            except HelperClientError:
                # A busy or slow helper skips one sample; the next read carries on.
                queues = False
                run.failed_reads += 1
            now = self.monotonic()
            if queues is None:
                changed = True
            elif queues is not False:
                _, entries = pick_queues(queues, run.queue_id, prefer=run.source)
                identity = {direction: _identity(entry) for direction, entry in entries.items()}
                sample = _sample(entries, now)
                previous = run.samples[-1] if run.samples else None
                if identity != run.identity or (previous is not None and any(
                    sample[direction]["bytes"] < previous[direction]["bytes"]
                    for direction in entries
                )):
                    changed = True
                else:
                    with self.lock:
                        if run.state == "running":
                            run.samples.append(sample)
                            del run.samples[:-MAX_SAMPLES]
        with self.lock:
            if run.state != "running":
                return
            if changed:
                self._end(run, "changed", reason="The device's queues changed during the test.")
            elif self.monotonic() >= run.deadline:
                self._end(run, "complete")

    # Ending.

    def _discard(self, run: SpeedTestRun, reason: str) -> None:
        run.state = "ended"
        run.ended_reason = reason

    def _end(self, run: SpeedTestRun, status: str, *, reason: Optional[str] = None) -> None:
        """End a running window and save its result. The caller holds the lock.

        Every way a window ends comes through here, so protection is checked in the repository
        transaction that saves the result. A device that became protected is not saved.
        """

        run.state = "ended"
        run.ended_reason = reason
        if status == "cancelled":
            run.ended_reason = "Cancelled. Nothing was saved."
            return
        if status in ("complete", "finished-early") and run.failed_reads * 2 > run.ticks:
            # Most of the reads failed, so the numbers are too thin to call this complete.
            status = "fault"
            run.ended_reason = "The Pi could not read the queue counters for most of the test."
        caps = {
            direction: ((run.identity or {}).get(direction, {}).get("impairment") or {})
            .get("bandwidth_kbit")
            for direction in DIRECTIONS
        }
        summary: Dict[str, Any] = {}
        for direction in DIRECTIONS:
            if run.source == "none" or direction not in (run.identity or {}):
                summary[direction] = unmeasured_direction()
            else:
                summary[direction] = summarize_direction(run.samples, direction, caps[direction])
            if summary[direction].get("verdict") in CAP_VERDICTS:
                if run.service_rules is True:
                    summary[direction]["verdict"] = "service-rules"
                elif run.service_rules is None and run.source != "none":
                    summary[direction]["verdict"] = "service-rules-unknown"
        latency = dict(run.prober.summary())
        # Only a device's own queue sees the Pi's ping. Everything else is a plain Wi-Fi round trip.
        latency["shaped"] = run.source == "device"
        if run.source in SHARED_SOURCES:
            latency["note"] = SHARED_LATENCY_NOTE
        summary["latency"] = latency
        summary["seconds"] = round(self.monotonic() - run.started_mono, 1)
        summary["read_failures"] = run.failed_reads
        summary["source_note"] = run.source_note
        if run.ended_reason:
            summary["ended_reason"] = run.ended_reason
        if run.source != "none":
            summary["service_rules"] = run.service_rules
        row = {
            "started_at": run.started_at,
            "finished_at": self.clock(),
            "mac_address": run.mac_address,
            "device_id": run.device_id,
            "device_name": run.device_name,
            "label": run.label,
            "status": status,
            "provider": run.provider,
            "source": run.source,
            "protected": False,
            "profile": run.profile,
            "summary": summary,
        }
        try:
            # Serialize the protection check with the result insert. A concurrent request that
            # protects this device must happen entirely before the check or after the saved row.
            with self.repository.transaction():
                if self._protected(run.mac_address):
                    self._discard(run, PROTECTED_MESSAGE)
                    return
                result = self._store().save(row)
            run.result = result
        except (StorageError, sqlite3.Error):
            run.ended_reason = "The result could not be saved."

    def finish(self, run_id: str) -> Dict[str, Any]:
        with self.lock:
            run = self._running(run_id)
            self._end(run, "finished-early")
            return run.payload(self.monotonic())

    def cancel(self, run_id: str) -> Dict[str, Any]:
        with self.lock:
            run = self._running(run_id)
            self._end(run, "cancelled")
            return run.payload(self.monotonic())

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop every window without saving and wait for the sampling threads.

        The server calls this before it closes the repository, so no thread reads or writes it
        afterwards.
        """

        with self.lock:
            self.closed = True
            threads = []
            uncertain = []
            for run in self.runs.values():
                if run.state == "running":
                    self._discard(run, "The dashboard stopped. Nothing was saved.")
                if run.thread is not None:
                    threads.append(run.thread)
                    if run.startup_uncertain and run.thread.ident is None:
                        uncertain.append(run)
        deadline = time.monotonic() + min(max(timeout, 0.0), SAMPLER_SHUTDOWN_TIMEOUT_SECONDS)
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            raise WorkersStillRunning(
                "A speed test sampler did not stop. Its repository remains in use.")
        if uncertain:
            raise WorkersStillRunning(
                "A speed test sampler start is unresolved. Its repository remains in use.")

    def _running(self, run_id: Any, kind: str = "speed") -> SpeedTestRun:
        run = self.runs.get(run_id) if isinstance(run_id, str) else None
        if run is None or run.kind != kind:
            raise SpeedTestRequestError(f"This {KIND_NAMES[kind]} is not running", 404)
        if run.state != "running":
            raise SpeedTestRequestError(f"This {KIND_NAMES[kind]} has already ended", 409)
        return run

    def status(self, run_id: Optional[str], kind: str = "speed") -> Dict[str, Any]:
        with self.lock:
            run = self.runs.get(run_id) if run_id else None
            if run is None or run.kind != kind:
                raise SpeedTestRequestError(f"This {KIND_NAMES[kind]} is not known", 404)
            return run.payload(self.monotonic())


def pick_queues(queues, device_id, prefer=None, plan=None):
    """Which queues carry this device's traffic: its own, the profile for everyone, or
    Everyone else. Returns (source, {direction: entry}).

    A saved device id may look like a shared queue id, so a shared queue is only chosen when
    the applied plan (if known) has its rule, and a device queue never uses a shared id.
    """

    shared = (GENERAL_QUEUE_ID, EVERYONE_ELSE_QUEUE_ID)
    general = plan is None or getattr(plan, "general_profile", None) is not None
    everyone_else = plan is None or getattr(plan, "everyone_else", None) is not None
    # A caller that names the source knows the queue is dedicated, so its id is used as it is.
    choices = (
        ("device", None if device_id in shared and prefer is None else device_id),
        ("everyone", GENERAL_QUEUE_ID if general else None),
        ("everyone-else", EVERYONE_ELSE_QUEUE_ID if everyone_else else None),
    )
    for source, queue_id in choices:
        if queue_id is None or (prefer is not None and source != prefer):
            continue
        entries = {
            entry["direction"]: entry for entry in queues
            if entry.get("device_id") == queue_id and entry.get("direction") in DIRECTIONS
        }
        if entries:
            return source, entries
    return (prefer or "none"), {}


def queue_id_for(source: str, device_queue_id: Optional[str]) -> Optional[str]:
    return {"device": device_queue_id, "everyone": GENERAL_QUEUE_ID,
            "everyone-else": EVERYONE_ELSE_QUEUE_ID}.get(source)


def _counters_match_plan(plan, queue_id, entries) -> bool:
    """Whether the helper's queues carry the impairments the applied plan gives this queue."""

    specs = {queue.direction.value: queue for queue in getattr(plan, "queues", ())
             if queue.device_id == queue_id}
    for direction, entry in entries.items():
        spec = specs.get(direction)
        reported = entry.get("impairment")
        if spec is None or not isinstance(reported, dict):
            return False
        planned = asdict(spec.impairment)
        if any(planned.get(key) != value for key, value in reported.items()):
            return False
    return True


def _address_owner(plan, ipv4) -> Optional[str]:
    """The device id whose mark rule in the applied plan matches this address, if any."""

    for rule in getattr(plan, "mark_rules", ()):
        if ipv4 is not None and str(rule.ipv4_address) == str(ipv4):
            return rule.device_id
    return None


def _identity(entry: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("generation", "interface", "handle", "kind", "impairment")
    return {key: entry.get(key) for key in keys}


def _sample(entries: Dict[str, Dict[str, Any]], now: float) -> Dict[str, Any]:
    sample: Dict[str, Any] = {"t": now}
    for direction, entry in entries.items():
        sample[direction] = {key: entry.get(key, 0) for key in ("bytes", "packets", "drops")}
    return sample


def speed_test_manager(server) -> SpeedTestManager:
    # The server makes its one manager when it is built.
    return server._speed_test_manager


def _query_mac(query: Dict[str, List[str]]) -> Optional[str]:
    value = query.get("mac", [None])[0]
    if value in (None, ""):
        return None
    try:
        return normalize_exempt_mac(value)
    except (TypeError, ValueError) as error:
        raise SpeedTestRequestError("Choose a connected device") from error


def _query_id(query: Dict[str, List[str]], name: str) -> int:
    value = query.get(name, [""])[0]
    # Only plain ASCII digits: other scripts pass isdigit() but cannot be read as a number.
    if not (value.isascii() and value.isdigit()) or len(value) > 19 or int(value) > MAX_ID:
        raise SpeedTestRequestError(f"{name} must be a result number")
    return int(value)


def _payload_id(payload: Dict[str, Any]) -> int:
    value = payload.get("result_id")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ID:
        raise SpeedTestRequestError("result_id must be a result number")
    return value


def read_speed_test(server, path: str, query: str = ""):
    if path not in SPEED_TEST_READS:
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
        LOGGER.exception("could not read the speed test results")
        return response({"error": "The speed test results could not be read."}, 500)


def _read(server, path, query):
    manager = speed_test_manager(server)
    store = SpeedTestStore(server.repository)
    if path == "/api/speed-tests":
        mac_address = _query_mac(query)
        runs = [run.payload(manager.monotonic()) for run in manager.active("speed")
                if mac_address is None or run.mac_address == mac_address]
        return response({
            "providers": provider_payload(),
            "default_provider": "cloudflare",
            "limits": {
                "default_seconds": DEFAULT_DURATION_SECONDS,
                "min_seconds": MIN_DURATION_SECONDS,
                "max_seconds": MAX_DURATION_SECONDS,
                "max_active": MAX_ACTIVE_RUNS,
            },
            "source_labels": SOURCE_LABELS,
            "verdict_labels": VERDICT_LABELS,
            "active": runs,
            "results": store.list(mac_address, limit=50 if mac_address else 200),
        })
    if path == "/api/speed-tests/status":
        return response(manager.status(query.get("run", [None])[0]))
    if path == "/api/speed-tests/compare":
        baseline = store.get(_query_id(query, "baseline"))
        later = store.get(_query_id(query, "result"))
        if baseline is None or later is None:
            raise SpeedTestRequestError("Choose two saved results", 404)
        return response(compare_results(baseline, later))
    form = query.get("format", ["json"])[0]
    if form not in ("csv", "json"):
        raise SpeedTestRequestError("format must be csv or json")
    results = store.list(_query_mac(query), limit=500)
    if form == "csv":
        return response(export_csv(results), content_type="text/csv; charset=utf-8")
    return response(export_json(results), content_type="application/json")


def write_speed_test(server, path: str, payload: Dict[str, Any]):
    if path not in SPEED_TEST_WRITES:
        return None
    try:
        return _write(server, path, payload)
    except SpeedTestRequestError as error:
        return response({"error": str(error)}, error.status)
    except StorageError:
        LOGGER.exception("could not save the speed test results")
        return response({"error": "The speed test results could not be saved."}, 500)


def _write(server, path, payload):
    manager = speed_test_manager(server)
    if path == "/api/speed-tests/start":
        return response(manager.start(payload), 201)
    if path in ("/api/speed-tests/finish", "/api/speed-tests/cancel"):
        if set(payload) != {"run_id"}:
            raise SpeedTestRequestError("Send run_id only")
        action = manager.finish if path.endswith("finish") else manager.cancel
        return response(action(payload["run_id"]))
    store = SpeedTestStore(server.repository)
    if path == "/api/speed-tests/update":
        if set(payload) - {"result_id", "label", "reported"} or not set(payload) - {"result_id"}:
            raise SpeedTestRequestError("Send result_id with label or reported")
        identifier = _payload_id(payload)
        try:
            label = clean_label(payload["label"]) if "label" in payload else None
            reported = clean_reported(payload["reported"]) if "reported" in payload else None
        except ValueError as error:
            raise SpeedTestRequestError(str(error)) from error
        result = store.update(identifier, label=label, reported=reported)
        if result is None:
            raise SpeedTestRequestError("This result is not saved", 404)
        return response(result)
    if set(payload) != {"result_id"}:
        raise SpeedTestRequestError("Send result_id only")
    if not store.delete(_payload_id(payload)):
        raise SpeedTestRequestError("This result is not saved", 404)
    return response({"deleted": True})
