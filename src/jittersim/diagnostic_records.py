"""Small, best-effort operation records for the local system journal."""

import functools
import json
import math
import os
import socket
import threading
import time

ACTIONS = frozenset(("apply", "reset", "panic", "schedule", "wifi-change", "wifi-reset", "router-copy", "router-wifi"))
SOURCES = frozenset(("dashboard", "helper", "scheduler", "wifi", "router"))
OUTCOMES = frozenset(("succeeded", "failed", "refused", "cancelled", "uncertain"))
MAX_MESSAGE = 4096
INTERVAL = 30.0
MAX_MESSAGES = 100
MAX_IMPORTANT_MESSAGES = 50
JOURNAL_SOCKET = "/run/systemd/journal/socket"
_clock = time.monotonic
_process_seed = ((os.getpid() << 32) ^ int(_clock() * 1_000_000_000)) & (2**64 - 1)
_sequence = 0
_gate = threading.Lock()
_window = 0.0
_count = 0
_important_count = 0
_dropped = 0


def _operation_id():
    global _sequence
    if not _gate.acquire(blocking=False):
        return None
    try:
        _sequence = (_sequence + 1) % (2**53)
        return f"{_process_seed:016x}{_sequence:016x}"
    finally:
        _gate.release()


def send_fields(fields, *, important=False):
    """Drop unavailable or busy logging without waiting on another writer."""
    global _window, _count, _important_count, _dropped
    if not _gate.acquire(blocking=False):
        return False
    try:
        if not isinstance(fields, dict) or not 1 <= len(fields) <= 5 or set(fields) - {
            "MESSAGE", "SYSLOG_IDENTIFIER", "PRIORITY", "JITTERSIM_EVENT", "JITTERSIM_DROPPED"
        }:
            return False
        if any(not isinstance(value, str) or len(value) > 2048 or "\n" in value or "\0" in value
               for value in fields.values()):
            return False
        now = _clock()
        if now < _window or now - _window >= INTERVAL:
            _window, _count, _important_count = now, 0, 0
        full = _important_count >= MAX_IMPORTANT_MESSAGES if important else _count >= MAX_MESSAGES
        if full:
            _dropped = min(_dropped + 1, 2**31 - 1)
            return False
        if important:
            _important_count += 1
        else:
            _count += 1
        if _dropped:
            fields = {**fields, "JITTERSIM_DROPPED": str(_dropped)}
        raw = ("\n".join(key + "=" + value for key, value in fields.items()) + "\n").encode("utf-8")
        if len(raw) > MAX_MESSAGE or any("\n" in value or "\0" in value for value in fields.values()):
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as journal:
            journal.setblocking(False)
            journal.sendto(raw, JOURNAL_SOCKET)
        _dropped = 0
        return True
    except Exception:
        _dropped = min(_dropped + 1, 2**31 - 1)
        return False
    finally:
        _gate.release()


def validate_event(value):
    """Return only the fixed schema. Unknown or extra fields are rejected."""
    if not isinstance(value, dict) or set(value) - {
        "format", "operation", "source", "action", "phase", "outcome", "elapsed_ms", "configuration_version"
    }:
        return None
    if value.get("format") != 1 or type(value.get("format")) is not int:
        return None
    identifier = value.get("operation")
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        return None
    if not isinstance(value.get("source"), str) or not isinstance(value.get("action"), str):
        return None
    if value.get("source") not in SOURCES or value.get("action") not in ACTIONS:
        return None
    phase = value.get("phase")
    if phase not in ("start", "outcome"):
        return None
    elapsed = value.get("elapsed_ms")
    if type(elapsed) is not int or not 0 <= elapsed <= 2**53 - 1:
        return None
    if phase == "outcome" and (not isinstance(value.get("outcome"), str) or value.get("outcome") not in OUTCOMES):
        return None
    if phase == "start" and "outcome" in value:
        return None
    version = value.get("configuration_version")
    if version is not None and (type(version) is not int or not 0 <= version <= 2**53 - 1):
        return None
    return dict(value)


def _emit(event):
    try:
        safe = validate_event(event)
        if safe is None:
            return
        send_fields({"MESSAGE": "JitterSim operation", "SYSLOG_IDENTIFIER": "jittersim-diagnostics",
                     "PRIORITY": "6", "JITTERSIM_EVENT": json.dumps(safe, separators=(",", ":"))},
                    important=safe["action"] != "schedule")
    except Exception:
        pass


class Operation:
    """One small record pair without request data or a worker thread."""

    def __init__(self, action, source, configuration_version=None):
        self.event = None
        self.started = 0.0
        self.finished = False
        try:
            self.started = _clock()
            event = {"format": 1, "operation": _operation_id(), "source": source,
                     "action": action, "phase": "start", "elapsed_ms": 0}
            if configuration_version is not None:
                event["configuration_version"] = configuration_version
            self.event = validate_event(event)
            if self.event:
                _emit(self.event)
        except Exception:
            pass

    def finish(self, outcome):
        if self.finished or self.event is None:
            return
        self.finished = True
        try:
            elapsed = max(0.0, _clock() - self.started)
            if not math.isfinite(elapsed):
                return
            event = {**self.event, "phase": "outcome", "outcome": outcome,
                     "elapsed_ms": min(int(elapsed * 1000), 2**53 - 1)}
            _emit(event)
        except Exception:
            pass


def status_outcome(status):
    if not isinstance(status, int) or isinstance(status, bool):
        return "uncertain"
    if 200 <= status < 300:
        return "succeeded" if status != 202 else "uncertain"
    return "refused" if 400 <= status < 500 else "failed"


def recorded(action, source):
    """Record a bounded operation without changing its return or exception."""
    def decorate(function):
        @functools.wraps(function)
        def call(*args, **kwargs):
            operation = Operation(action, source)
            try:
                result = function(*args, **kwargs)
            except BaseException:
                operation.finish("failed")
                raise
            if isinstance(result, tuple) and result:
                outcome = status_outcome(result[0])
            elif isinstance(result, dict):
                outcome = "succeeded" if result.get("ok") is True else "failed"
            else:
                outcome = "uncertain"
            operation.finish(outcome)
            return result
        return call
    return decorate


PAYLOAD_ACTIONS = {"apply": "apply", "apply-experiment": "schedule", "reset": "reset",
                   "recover-reset": "reset", "normal-access": "panic", "configure-ap": "wifi-change",
                   "wifi-reset": "wifi-reset", "router-sync-ap": "router-copy", "router-wifi": "router-wifi"}


def recorded_payload(function):
    @functools.wraps(function)
    def call(self, payload):
        action = payload.get("action") if isinstance(payload, dict) else None
        if not isinstance(action, str) or action not in PAYLOAD_ACTIONS:
            return function(self, payload)
        return recorded(PAYLOAD_ACTIONS[action], "helper")(function)(self, payload)
    return call
