"""Keep slow router work out of the dashboard request thread."""

import hmac
import json
import math
import secrets
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from .wifi_operations import valid_request_id
from jittersim.diagnostic_records import Operation, status_outcome
from jittersim.request_workers import RequestWorkers


class RouterOperations:
    """Run one operation with bounded result retention and shared read limits."""

    MAX_REQUESTS = 64
    REQUEST_RETENTION = 3600.0
    READ_INTERVAL = 15.0
    FAILURE_INTERVAL = 30.0
    RESULT_LIFETIME = 240.0
    STATE_LIFETIME = 45.0

    def __init__(self, execute: Callable, identity: str) -> None:
        self._execute = execute
        self._identity = identity
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._requests = {}
        self._active = None
        self._completed = None
        self._snapshot = None
        # One budget for every router login the dashboard starts to read or check. It is owned
        # here, so another tab, another password or a direct API call cannot get around it.
        self._next_read = 0.0
        # A password check that runs on the caller's own thread, not on a worker.
        self._direct_check = False
        self._closed = False
        self._workers = RequestWorkers("router-operation", self._uncertain_start)

    def _context(self, password: str) -> bytes:
        return hmac.digest(self._key, (self._identity + "\0" + password).encode(), "sha256")

    def _expire(self) -> None:
        now = time.monotonic()
        for identifier, operation in list(self._requests.items()):
            if operation is not self._active and now - operation["created"] > self.REQUEST_RETENTION:
                del self._requests[identifier]
        if self._completed and now - self._completed["finished"] > self.RESULT_LIFETIME:
            self._completed = None
        if self._snapshot and now - self._snapshot["observed"] > self.STATE_LIFETIME:
            self._snapshot = None

    def _view(self, context: bytes) -> Dict[str, Any]:
        self._expire()
        snapshot = self._snapshot
        data = {"enabled": None, "checked_at": None, "busy": self._active is not None or self._direct_check,
                "changing": bool(self._active and self._active["path"] == "/api/router/wifi")}
        if snapshot and hmac.compare_digest(snapshot["context"], context):
            data.update(snapshot["data"])
        if self._active or self._direct_check:
            data["retry_after"] = 1
            data["refresh_in"] = 0
        else:
            data["retry_after"] = max(1, int(self._next_read - time.monotonic()) + 1)
            # Whole seconds until the router may be contacted again, or 0 when it may be now.
            data["refresh_in"] = max(0, math.ceil(self._next_read - time.monotonic()))
        return data

    def status(self, password: str, *, refresh: bool = False) -> Dict[str, Any]:
        context = self._context(password)
        with self._lock:
            if (refresh and not self._closed and self._active is None and not self._direct_check
                    and not self._workers.has_workers and time.monotonic() >= self._next_read):
                self._next_read = time.monotonic() + self.READ_INTERVAL
                self._start("/api/router/status", {"admin_password": password}, context)
            return self._view(context)

    def start(self, path: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        context = self._context(payload["admin_password"])
        with self._lock:
            self._expire()
            identifier = payload.get("request_id")
            previous = self._requests.get(identifier) if identifier is not None else None
            if previous:
                if previous["path"] != path or not hmac.compare_digest(previous["context"], context):
                    return 409, {"error": "Request identifier belongs to a different router operation."}
                return self._result(previous)
            if identifier is not None and (not valid_request_id(identifier) or len(self._requests) >= self.MAX_REQUESTS):
                return 409, {"error": "Router outcome store is full or request identifier expired. Check current state."}
            if self._busy():
                return 409, {"error": "Another router operation is running. Try again shortly."}
            if path == "/api/router/check":
                refused = self._check_wait()
                if refused:
                    return refused
            if path == "/api/router/wifi":
                self._snapshot = None
            operation = self._start(path, dict(payload), context)
            if "status" in operation:
                return operation["status"], {**operation["data"], "operation_id": operation["id"]}
            return 202, {"operation_id": operation["id"], "pending": True}

    def _busy(self) -> bool:
        return self._closed or self._active is not None or self._direct_check or self._workers.has_workers

    def _check_wait(self) -> Optional[Tuple[int, Dict[str, Any]]]:
        """Refuse a password check that comes too soon after any router login."""
        wait = self._next_read - time.monotonic()
        if wait <= 0:
            return None
        seconds = max(1, math.ceil(wait))
        return 429, {"error": "The router was contacted a moment ago. Wait %d second%s before checking the "
                              "router password again." % (seconds, "" if seconds == 1 else "s"),
                     "retry_after": seconds}

    def begin_check(self) -> Optional[Tuple[int, Dict[str, Any]]]:
        """Admit a password check that runs on the caller's thread, or say why not.

        The caller must call end_check() when the check is over. Until then the budget is held
        as if the check had failed, so a check that never reports back cannot be repeated early.
        """
        with self._lock:
            self._expire()
            if self._busy():
                return 409, {"error": "Another router operation is running. Try again shortly."}
            refused = self._check_wait()
            if refused:
                return refused
            self._direct_check = True
            self._next_read = time.monotonic() + self.FAILURE_INTERVAL
        return None

    def end_check(self, succeeded: bool) -> None:
        with self._lock:
            self._direct_check = False
            if not self._closed:
                self._next_read = time.monotonic() + (self.READ_INTERVAL if succeeded else self.FAILURE_INTERVAL)
                if not succeeded:
                    self._snapshot = None

    def _start(self, path: str, payload: Dict[str, Any], context: bytes) -> Dict[str, Any]:
        operation = {"id": payload.get("request_id") or secrets.token_urlsafe(24), "path": path, "context": context,
                     "created": time.monotonic()}
        if "request_id" in payload:
            self._requests[operation["id"]] = operation
        self._active = operation
        try:
            self._workers.start(lambda: self._run(operation, payload),
                                abandon=payload.clear, release=lambda: None)
        except RuntimeError:
            # _start holds the lock. A partially created worker cannot pass admission yet.
            operation.update(cancelled=True, status=503,
                             data={"error": "Router worker did not start. Check current state."},
                             finished=time.monotonic(), recorded=False)
            self._active = None
            if path != "/api/router/status":
                self._completed = operation
            self._next_read = time.monotonic() + self.FAILURE_INTERVAL
        return operation

    def _run(self, operation: Dict[str, Any], payload: Dict[str, Any]) -> None:
        with self._lock:
            if self._closed or operation.get("cancelled") or self._active is not operation:
                payload.clear()
                if self._active is operation:
                    self._active = None
                return
        diagnostic = Operation("router-wifi" if operation["path"] == "/api/router/wifi" else
                               "router-copy", "router") if operation["path"] in ("/api/router/wifi", "/api/router/sync-ap") else None
        try:
            status, _, body = self._execute(operation["path"], payload)
            data = json.loads(body)
            if not isinstance(data, dict) or ("result" in data and not isinstance(data["result"], dict)):
                raise ValueError("invalid router result")
        except Exception:
            status, data = 502, {"error": "Router operation failed. Check its state before trying again."}
        finally:
            payload.clear()
        if diagnostic:
            diagnostic.finish(status_outcome(status))
        with self._lock:
            if self._closed:
                self._active = None
                return
            now = time.monotonic()
            if operation["path"] in ("/api/router/status", "/api/router/wifi"):
                enabled = data.get("result", {}).get("enabled") if status == 200 else None
                snapshot = {"enabled": enabled if type(enabled) is bool else None,
                            "checked_at": time.time() if type(enabled) is bool else None}
                if status != 200:
                    snapshot["error"] = data.get("error", "Router status is unavailable.")
                self._snapshot = {"context": operation["context"], "observed": now, "data": snapshot}
                self._next_read = now + (self.READ_INTERVAL if status == 200 else self.FAILURE_INTERVAL)
            elif status != 200:
                self._snapshot = None
                self._next_read = now + self.FAILURE_INTERVAL
            elif operation["path"] == "/api/router/check":
                # A check is a fresh router login: the next one waits like the next status read.
                self._next_read = now + self.READ_INTERVAL
            if operation["path"] != "/api/router/status":
                operation.update(status=status, data=data, finished=now, recorded=False)
                self._completed = operation
            self._active = None

    def _result(self, operation):
        if operation is self._active:
            return 202, {"operation_id": operation["id"], "pending": True}
        if "finished" not in operation or time.monotonic() - operation["finished"] > self.RESULT_LIFETIME:
            return 410, {"error": "Exact router result expired. Check current state before another change."}
        return operation["status"], {**operation["data"], "operation_id": operation["id"]}

    def result(self, identifier: str, password: str, expected_path: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        context = self._context(password)
        with self._lock:
            self._expire()
            for operation in (self._active, self._completed, self._requests.get(identifier)):
                if (operation and operation["id"] == identifier
                        and hmac.compare_digest(operation["context"], context)):
                    if expected_path is not None and operation["path"] != expected_path:
                        return 409, {"error": "Request identifier belongs to a different router operation."}
                    return self._result(operation)
            return 410, {"error": "Router result expired. Check the current state before another change."}

    def completed_action(self):
        """Claim a completed action once from the repository's owning thread."""
        with self._lock:
            operation = self._completed
            if operation and not operation["recorded"]:
                operation["recorded"] = True
                return operation["path"], operation["status"]
        return None

    def _uncertain_start(self) -> None:
        self._closed = True

    def begin_shutdown(self) -> None:
        self._workers.stop_admission()
        with self._lock:
            self._closed = True
            self._snapshot = None
            self._completed = None

    def close(self, timeout: float = 5.0) -> None:
        self.begin_shutdown()
        self._workers.close(timeout)
