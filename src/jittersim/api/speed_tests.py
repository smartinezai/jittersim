"""Authenticated routes for the device speed test.

The routes sit behind the admin token and the saved-device check like every dashboard route
(the request handler refuses before routing). A measurement window runs on a daemon thread
that reads the helper's read-only queue counters once per second and sends one ICMP echo to
the device. See docs/implementation-plan-device-speed-test-2026-10-09.md.
"""

import json
import secrets
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

from jittersim.config.exemptions import normalize_exempt_mac
from jittersim.config.payloads import impairment_payload
from jittersim.config.speed_tests import SpeedTestStore, clean_label, clean_reported
from jittersim.config.storage import StorageError
from jittersim.engine.plan import EVERYONE_ELSE_QUEUE_ID, GENERAL_QUEUE_ID
from jittersim.networking.helper import HelperClientError
from jittersim.speed_test import (
    DEFAULT_DURATION_SECONDS,
    DEFAULT_PROVIDER,
    DIRECTIONS,
    MAX_ACTIVE_RUNS,
    MAX_DURATION_SECONDS,
    MAX_SAMPLES,
    MIN_DURATION_SECONDS,
    PROVIDER_IDS,
    SAMPLE_INTERVAL_SECONDS,
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


class SpeedTestRequestError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def response(value, status=200, content_type="application/json"):
    # Export files arrive as finished text; everything else is encoded here.
    body = value.encode("utf-8") if isinstance(value, str) else (
        json.dumps(value, allow_nan=False) + "\n").encode()
    return status, content_type, body


class SpeedTestRun:
    def __init__(self, **fields):
        self.__dict__.update(fields)
        self.samples: List[Dict[str, Any]] = []
        self.state = "running"
        self.ended_reason: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None
        self.identity: Optional[Dict[str, Any]] = None
        self.failed_reads = 0

    def payload(self, now: float) -> Dict[str, Any]:
        live = {}
        for direction in DIRECTIONS:
            rates = per_second_rates(self.samples[-2:], direction) if self.source != "none" else []
            peak = per_second_rates(self.samples, direction) if self.source != "none" else []
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
            "seconds_left": max(0, int(round(self.deadline - now))) if self.state == "running" else 0,
            "source": self.source,
            "source_label": SOURCE_LABELS[self.source],
            "source_note": self.source_note,
            "state": self.state,
            "ended_reason": self.ended_reason,
            "samples": len(self.samples),
            "live": live,
            "result": self.result,
        }


class SpeedTestManager:
    """At most MAX_ACTIVE_RUNS windows, one per device, each with its own sampling thread."""

    def __init__(self, server, *, autostart: bool = True, clock=time.time,
                 monotonic=time.monotonic, sleep=time.sleep):
        self.server = server
        self.autostart = autostart
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.lock = threading.RLock()
        self.runs: Dict[str, SpeedTestRun] = {}

    # Helpers that read the server's state.

    @property
    def repository(self):
        return self.server.repository

    def _store(self) -> SpeedTestStore:
        return SpeedTestStore(self.repository)

    def _protected(self, mac_address: str) -> bool:
        return any(item.mac_address == mac_address for item in self.repository.list_unaffected_devices())

    def _read_queues(self) -> Optional[List[Dict[str, Any]]]:
        helper = self.server.helper_client
        read = getattr(helper, "queue_stats", None) if helper is not None else None
        if read is None or self.repository.get_runtime_state().observed_state == "reset":
            return None
        return read()

    def active(self) -> List[SpeedTestRun]:
        with self.lock:
            return [run for run in self.runs.values() if run.state == "running"]

    # Starting a window.

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"mac_address", "provider", "duration_seconds", "label"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise SpeedTestRequestError("Only mac_address, provider, duration_seconds and label are allowed")
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
        with self.lock:
            active = self.active()
            if any(run.mac_address == mac_address for run in active):
                raise SpeedTestRequestError("A speed test is already running for this device", 409)
            if len(active) >= MAX_ACTIVE_RUNS:
                raise SpeedTestRequestError(
                    f"At most {MAX_ACTIVE_RUNS} speed tests can run at once. Wait for one to end.", 409)
            run = self._new_run(mac_address, provider, duration, label)
            self.runs[run.id] = run
            self._trim()
        if self.autostart:
            threading.Thread(target=self._loop, args=(run,), name="speed-test", daemon=True).start()
        return run.payload(self.clock())

    def _new_run(self, mac_address, provider, duration, label) -> SpeedTestRun:
        repository = self.repository
        device = repository.get_device_by_mac_address(mac_address)
        labels = repository.list_connected_device_labels()
        name = (labels.get(mac_address, {}).get("display_name") or (device.name if device else "")
                or mac_address)
        ipv4 = str(device.ipv4_address) if device is not None else self._connected_address(mac_address)
        now = self.clock()
        queues, note = None, None
        try:
            queues = self._read_queues()
            if queues is None:
                note = ("The Pi helper is not connected." if self.server.helper_client is None
                        else "The network is reset or the helper cannot read queues.")
        except HelperClientError:
            note = "The queue counters could not be read."
        source, entries = pick_queues(queues or [], device.id if device else None)
        if source == "none" and note is None:
            note = ("No profile applies to this device, so the Pi has no counter for it. Type in "
                    "the numbers from the speed test page, or apply a profile with nothing set "
                    "to measure it on the Pi without limits.")
        elif source in ("everyone", "everyone-else"):
            note = "Other devices share this queue, so their traffic is counted too."
        run = SpeedTestRun(
            id=secrets.token_hex(8),
            mac_address=mac_address,
            device_id=device.id if device else None,
            device_name=name[:64],
            ipv4_address=ipv4,
            provider=provider,
            label=label,
            duration_seconds=duration,
            started_at=now,
            deadline=now + duration,
            source=source,
            source_note=note,
            profile=self._profile_snapshot(device, source, entries),
        )
        if source != "none":
            run.identity = {direction: _identity(entry) for direction, entry in entries.items()}
            run.samples.append(_sample(entries, now))
        factory = getattr(self.server, "speed_test_prober_factory", None)
        if ipv4 is None:
            run.prober = NoProber()
        else:
            run.prober = factory(ipv4) if factory is not None else IcmpProber(ipv4)
        return run

    def _connected_address(self, mac_address: str) -> Optional[str]:
        helper = self.server.helper_client
        if helper is None:
            return None
        try:
            for client in helper.connected_devices():
                if client.get("mac_address") == mac_address:
                    return client.get("ipv4_address")
        except (HelperClientError, AttributeError, TypeError):
            return None
        return None

    def _profile_snapshot(self, device, source, entries) -> Dict[str, Any]:
        repository = self.repository
        profile = None
        if source == "device" and device is not None:
            profile = repository.get_profile(device.profile_id)
        elif source == "everyone":
            profile = repository.get_general_profile()
        elif source == "everyone-else":
            identifier = repository.get_everyone_else_profile_id()
            profile = repository.get_profile(identifier) if identifier else None
        elif device is not None:
            profile = repository.get_profile(device.profile_id)
        general = repository.get_general_profile()
        return {
            "kind": source,
            "profile_id": profile.id if profile else None,
            "profile_name": profile.name if profile else "No profile",
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
        ended = [run for run in self.runs.values() if run.state != "running"]
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
            with self.lock:
                if run.state == "running":
                    self._end(run, "changed", reason="The measurement stopped after an error.")
        finally:
            run.prober.close()

    def tick(self, run_id: str) -> None:
        """Take one sample, then end the run when its time is up."""

        with self.lock:
            run = self.runs.get(run_id)
            if run is None or run.state != "running":
                return
        if self._protected(run.mac_address):
            with self.lock:
                if run.state == "running":
                    run.state = "ended"
                    run.ended_reason = PROTECTED_MESSAGE
            return
        changed = False
        if run.source != "none":
            try:
                queues = self._read_queues()
            except HelperClientError:
                # A busy or slow helper skips one sample; the next read carries on.
                queues = False
                run.failed_reads += 1
            now = self.clock()
            if queues is None:
                changed = True
            elif queues is not False:
                _, entries = pick_queues(queues, run.device_id, prefer=run.source)
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
            elif self.clock() >= run.deadline:
                self._end(run, "complete")

    # Ending.

    def _end(self, run: SpeedTestRun, status: str, *, reason: Optional[str] = None) -> None:
        run.state = "ended"
        run.ended_reason = reason
        caps = {
            direction: (run.identity or {}).get(direction, {}).get("impairment", {}).get("bandwidth_kbit")
            for direction in DIRECTIONS
        }
        summary: Dict[str, Any] = {}
        for direction in DIRECTIONS:
            if run.source == "none" or direction not in (run.identity or {}):
                summary[direction] = unmeasured_direction()
            else:
                summary[direction] = summarize_direction(run.samples, direction, caps[direction])
        summary["latency"] = run.prober.summary()
        summary["seconds"] = round(self.clock() - run.started_at, 1)
        summary["source_note"] = run.source_note
        try:
            run.result = self._store().save({
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
            })
        except StorageError:
            run.ended_reason = "The result could not be saved."

    def finish(self, run_id: str) -> Dict[str, Any]:
        with self.lock:
            run = self._running(run_id)
            self._end(run, "finished-early")
            return run.payload(self.clock())

    def cancel(self, run_id: str) -> Dict[str, Any]:
        with self.lock:
            run = self._running(run_id)
            run.state = "ended"
            run.ended_reason = "Cancelled. Nothing was saved."
            return run.payload(self.clock())

    def _running(self, run_id: Any) -> SpeedTestRun:
        run = self.runs.get(run_id) if isinstance(run_id, str) else None
        if run is None:
            raise SpeedTestRequestError("This speed test is not running", 404)
        if run.state != "running":
            raise SpeedTestRequestError("This speed test has already ended", 409)
        return run

    def status(self, run_id: Optional[str]) -> Dict[str, Any]:
        with self.lock:
            run = self.runs.get(run_id) if run_id else None
            if run is None:
                raise SpeedTestRequestError("This speed test is not known", 404)
            return run.payload(self.clock())


def pick_queues(queues, device_id, prefer=None):
    """Which queues carry this device's traffic: its own, the profile for everyone, or
    Everyone else. Returns (source, {direction: entry})."""

    choices = (
        ("device", device_id),
        ("everyone", GENERAL_QUEUE_ID),
        ("everyone-else", EVERYONE_ELSE_QUEUE_ID),
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


def _identity(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {key: entry.get(key) for key in ("generation", "interface", "handle", "kind", "impairment")}


def _sample(entries: Dict[str, Dict[str, Any]], now: float) -> Dict[str, Any]:
    sample: Dict[str, Any] = {"t": now}
    for direction, entry in entries.items():
        sample[direction] = {key: entry.get(key, 0) for key in ("bytes", "packets", "drops")}
    return sample


def speed_test_manager(server) -> SpeedTestManager:
    manager = getattr(server, "_speed_test_manager", None)
    if manager is None:
        manager = server._speed_test_manager = SpeedTestManager(server)
    return manager


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
    if not value.isdigit() or len(value) > 12:
        raise SpeedTestRequestError(f"{name} must be a result number")
    return int(value)


def _payload_id(payload: Dict[str, Any]) -> int:
    value = payload.get("result_id")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SpeedTestRequestError("result_id must be a result number")
    return value


def read_speed_test(server, path: str, query: str = ""):
    if path not in SPEED_TEST_READS:
        return None
    try:
        return _read(server, path, parse_qs(query, max_num_fields=8))
    except SpeedTestRequestError as error:
        return response({"error": str(error)}, error.status)


def _read(server, path, query):
    manager = speed_test_manager(server)
    store = SpeedTestStore(server.repository)
    if path == "/api/speed-tests":
        mac_address = _query_mac(query)
        runs = [run.payload(manager.clock()) for run in manager.active()
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
