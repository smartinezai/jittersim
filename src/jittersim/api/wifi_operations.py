"""Bounded, password-free outcomes for Wi-Fi changes and exact confirmation reads."""

import re
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from jittersim.diagnostic_records import Operation, status_outcome
from jittersim.request_workers import RequestWorkers

ACTION_PATHS = {
    "reset-name": "/api/access-point/reset-name",
    "access-point": "/api/access-point",
    "router-copy": "/api/router/sync-ap",
}
LOST_OUTCOME = "The Wi-Fi change did not finish. Check the Pi Wi-Fi settings before trying again."
REQUEST_LIFETIME = 300.0
_REQUEST_KEY = secrets.token_bytes(32)
_REQUEST_EPOCH = time.time()
_REQUEST_START = time.monotonic()


def request_now():
    return _REQUEST_EPOCH + time.monotonic() - _REQUEST_START


def issue_request_id(now=None):
    """Read-only issuance. A fresh process invalidates all earlier write identifiers."""
    prefix = str(int((request_now() if now is None else now) * 1000)) + "." + secrets.token_hex(8)
    signature = hmac.new(_REQUEST_KEY, prefix.encode(), hashlib.sha256).hexdigest()[:32]
    return prefix + "." + signature



def valid_request_id(identifier, now=None):
    """Old identifiers cannot become new writes after their stored outcome expires."""
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9]{13}\.[a-f0-9]{16}\.[a-f0-9]{32}", identifier):
        return False
    prefix, signature = identifier.rsplit(".", 1)
    expected = hmac.new(_REQUEST_KEY, prefix.encode(), hashlib.sha256).hexdigest()[:32]
    age = (request_now() if now is None else now) - int(identifier.split(".")[0]) / 1000
    return hmac.compare_digest(signature, expected) and -30 <= age <= REQUEST_LIFETIME



@dataclass(frozen=True)
class WifiOutcome:
    """What a finished change reports. It never carries a password."""

    status: int
    message: str
    ssid: Optional[str] = None
    security: Optional[str] = None
    password_changed: bool = False


class WifiOperations:
    """One change at a time. Keep at most 64 exact outcomes for one hour."""

    RESULT_LIFETIME = 3600.0
    MAX_RESULTS = 64

    def __init__(self, delay: float = 0.75, clock: Callable[[], float] = time.time,
                 elapsed_clock: Callable[[], float] = time.monotonic,
                 request_clock: Callable[[], float] = request_now) -> None:
        self.delay = delay
        self._clock = clock
        self._elapsed_clock = elapsed_clock
        self._request_clock = request_clock
        self._lock = threading.Lock()
        self._current: Optional[Dict[str, Any]] = None
        self._results: Dict[str, Dict[str, Any]] = {}
        self._active = False
        self._unrecorded: Optional[Tuple[str, int]] = None
        self._closed = False
        self._closing = threading.Event()
        self._entered = False
        self._workers = RequestWorkers("wifi-operation", self._uncertain_start)

    def _expire(self):
        now = self._elapsed_clock()
        for identifier, operation in list(self._results.items()):
            if operation["finished_at"] is not None and now - operation["_finished_elapsed"] > self.RESULT_LIFETIME:
                del self._results[identifier]
        if (self._current and self._current["finished_at"] is not None
                and now - self._current["_finished_elapsed"] > self.RESULT_LIFETIME):
            self._current = None

    def busy(self) -> bool:
        with self._lock:
            return self._active or self._workers.has_workers

    def _create(self, kind: str, request_id=None) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._expire()
            if request_id is not None:
                previous = self._results.get(request_id)
                if previous:
                    return dict(previous) if previous["kind"] == kind else None
                if not valid_request_id(request_id, self._request_clock()) or len(self._results) >= self.MAX_RESULTS:
                    return None
            if self._closed or self._active or self._workers.has_workers:
                return None
            self._active = True
            self._entered = False
            self._current = {
                "id": request_id or secrets.token_urlsafe(18), "kind": kind, "state": "running",
                "status": None, "message": None, "ssid": None, "security": None,
                "password_changed": False, "started_at": self._clock(), "finished_at": None,
                "_finished_elapsed": None,
            }
            if request_id is not None:
                self._results[request_id] = self._current
            return dict(self._current)

    def reserve(self, kind, request_id=None):
        """Reserve before accepting a router or synchronous Wi-Fi change."""
        return self._create(kind, request_id)

    def begin(self, kind: str) -> Optional[str]:
        operation = self._create(kind)
        return operation["id"] if operation else None

    def ready(self, identifier, delayed=True):
        """Close cancels queued work. An admitted helper call may finish normally."""
        if delayed and self._closing.wait(max(0, self.delay)):
            return False
        with self._lock:
            if self._closed or not self._active or self._current["id"] != identifier or self._entered:
                return False
            self._entered = True
            action = {"reset-name": "wifi-reset", "access-point": "wifi-change", "router-copy": "router-copy"}.get(self._current["kind"])
            self._current["_diagnostic"] = Operation(action, "wifi")
            return True

    def finish(self, identifier: str, outcome: WifiOutcome) -> None:
        with self._lock:
            current = self._current
            if not self._active or not current or current["id"] != identifier:
                return
            diagnostic = current.get("_diagnostic")
            if diagnostic:
                diagnostic.finish(status_outcome(outcome.status))
            succeeded = outcome.status == 200
            current.update(
                state="succeeded" if succeeded else "failed", status=outcome.status,
                message=outcome.message, finished_at=self._clock(), _finished_elapsed=self._elapsed_clock(),
                ssid=outcome.ssid if succeeded else None,
                security=outcome.security if succeeded else None,
                password_changed=bool(outcome.password_changed and succeeded),
            )
            self._unrecorded = (ACTION_PATHS.get(current["kind"], current["kind"]), outcome.status)
            self._active = False

    def start(self, kind: str, work: Callable[[], WifiOutcome], request_id=None) -> Optional[Dict[str, Any]]:
        # Keep creation and thread admission under the same lock for repeat identifiers.
        operation = self._create(kind, request_id)
        if operation is None:
            return None
        with self._lock:
            current = self._current
            if operation["state"] != "running" or not current or current["id"] != operation["id"]:
                return operation
            if current.get("scheduled"):
                return operation
            current["scheduled"] = True
        try:
            self._workers.start(lambda: self._run(operation["id"], work),
                                abandon=lambda: None, release=lambda: None)
        except RuntimeError:
            with self._lock:
                # Cancel queued admission even if thread creation partly succeeded.
                # Work already admitted keeps the slot until its bounded helper call finishes.
                if self._current and self._current["id"] == operation["id"] and not self._entered:
                    self._current.update(state="failed", status=503,
                                         message="Wi-Fi worker did not start. Check current settings.",
                                         finished_at=self._clock(), _finished_elapsed=self._elapsed_clock())
                    self._active = False
                    self._unrecorded = (ACTION_PATHS.get(kind, kind), 503)
                    operation = dict(self._current)
        return operation

    def _run(self, identifier: str, work: Callable[[], WifiOutcome]) -> None:
        if not self.ready(identifier):
            return
        try:
            outcome = work()
        except Exception:
            outcome = WifiOutcome(502, LOST_OUTCOME)
        self.finish(identifier, outcome)

    def last(self, identifier: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        with self._lock:
            self._expire()
            current = self._current if identifier is None else self._results.get(identifier)
            if identifier is not None and current is None and self._current and self._current["id"] == identifier:
                current = self._current
            if identifier is not None and current is None:
                return 410, {"error": "That Wi-Fi change is no longer remembered. Check the Pi Wi-Fi settings before another change."}
            view = {key: value for key, value in current.items() if key != "scheduled" and not key.startswith("_")} if current else None
            return 200, {"operation": view}

    def completed_action(self) -> Optional[Tuple[str, int]]:
        with self._lock:
            outcome, self._unrecorded = self._unrecorded, None
            return outcome

    def _uncertain_start(self) -> None:
        self._closed = True
        self._closing.set()

    def begin_shutdown(self) -> None:
        self._workers.stop_admission()
        self._closing.set()
        with self._lock:
            self._closed = True
            if self._active and self._entered:
                diagnostic = self._current.get("_diagnostic")
                if diagnostic:
                    diagnostic.finish("uncertain")
                self._current.update(state="uncertain", message="Service stopped while Wi-Fi work was running. Check current settings before another change.")
            if self._active and not self._entered:
                self._current.update(state="failed", status=503, finished_at=self._clock(),
                                     _finished_elapsed=self._elapsed_clock(),
                                     message="Service stopped before the Wi-Fi change began. Check current settings.")
                self._active = False

    def close(self, timeout: float = 5.0) -> None:
        self.begin_shutdown()
        self._workers.close(timeout)
