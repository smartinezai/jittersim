"""Keep slow router work out of the dashboard request thread."""

import hmac
import json
import secrets
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from .wifi_operations import valid_request_id
from jittersim.request_workers import RequestWorkers


class RouterOperations:
    """Run one operation with bounded result retention and shared read limits."""

    MAX_REQUESTS = 64
    REQUEST_RETENTION = 3600.0
    READ_INTERVAL = 15.0
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
        self._next_read = 0.0
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
        data = {"enabled": None, "checked_at": None, "busy": self._active is not None,
                "changing": bool(self._active and self._active["path"] == "/api/router/wifi")}
        if snapshot and hmac.compare_digest(snapshot["context"], context):
            data.update(snapshot["data"])
        if self._active:
            data["retry_after"] = 1
        else:
            data["retry_after"] = max(1, int(self._next_read - time.monotonic()) + 1)
        return data

    def status(self, password: str) -> Dict[str, Any]:
        context = self._context(password)
        with self._lock:
            if not self._closed and self._active is None and not self._workers.has_workers and time.monotonic() >= self._next_read:
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
            if self._closed or self._active is not None or self._workers.has_workers:
                return 409, {"error": "Another router operation is running. Try again shortly."}
            if path == "/api/router/wifi":
                self._snapshot = None
            operation = self._start(path, dict(payload), context)
            if "status" in operation:
                return operation["status"], {**operation["data"], "operation_id": operation["id"]}
            return 202, {"operation_id": operation["id"], "pending": True}

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
            self._next_read = time.monotonic() + 30
        return operation

    def _run(self, operation: Dict[str, Any], payload: Dict[str, Any]) -> None:
        with self._lock:
            if self._closed or operation.get("cancelled") or self._active is not operation:
                payload.clear()
                if self._active is operation:
                    self._active = None
                return
        try:
            status, _, body = self._execute(operation["path"], payload)
            data = json.loads(body)
            if not isinstance(data, dict) or ("result" in data and not isinstance(data["result"], dict)):
                raise ValueError("invalid router result")
        except Exception:
            status, data = 502, {"error": "Router operation failed. Check its state before trying again."}
        finally:
            payload.clear()
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
                self._next_read = now + (self.READ_INTERVAL if status == 200 else 30.0)
            elif status != 200:
                self._snapshot = None
                self._next_read = now + 30.0
            elif operation["path"] == "/api/router/check":
                self._next_read = now
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
