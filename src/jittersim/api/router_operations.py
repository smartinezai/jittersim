"""Keep slow router work out of the dashboard request thread."""

import hmac
import json
import secrets
import threading
import time
from typing import Any, Callable, Dict, Tuple


class RouterOperations:
    """Run one operation with bounded result retention and shared read limits."""

    READ_INTERVAL = 15.0
    RESULT_LIFETIME = 240.0
    STATE_LIFETIME = 45.0

    def __init__(self, execute: Callable, identity: str) -> None:
        self._execute = execute
        self._identity = identity
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._active = None
        self._completed = None
        self._snapshot = None
        self._next_read = 0.0
        self._closed = False

    def _context(self, password: str) -> bytes:
        return hmac.digest(self._key, (self._identity + "\0" + password).encode(), "sha256")

    def _expire(self) -> None:
        now = time.monotonic()
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
            if not self._closed and self._active is None and time.monotonic() >= self._next_read:
                self._next_read = time.monotonic() + self.READ_INTERVAL
                self._start("/api/router/status", {"admin_password": password}, context)
            return self._view(context)

    def start(self, path: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        context = self._context(payload["admin_password"])
        with self._lock:
            self._expire()
            if self._closed or self._active is not None:
                return 409, {"error": "Another router operation is running. Try again shortly."}
            if path == "/api/router/wifi":
                self._snapshot = None
            operation = self._start(path, dict(payload), context)
            return 202, {"operation_id": operation["id"], "pending": True}

    def _start(self, path: str, payload: Dict[str, Any], context: bytes) -> Dict[str, Any]:
        operation = {"id": secrets.token_urlsafe(24), "path": path, "context": context}
        self._active = operation
        worker = threading.Thread(target=self._run, args=(operation, payload), daemon=True,
                                  name="router-operation")
        worker.start()
        return operation

    def _run(self, operation: Dict[str, Any], payload: Dict[str, Any]) -> None:
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
                self._completed = {**operation, "status": status, "data": data, "finished": now,
                                   "recorded": False}
            self._active = None

    def result(self, identifier: str, password: str) -> Tuple[int, Dict[str, Any]]:
        context = self._context(password)
        with self._lock:
            self._expire()
            for operation in (self._active, self._completed):
                if (operation and operation["id"] == identifier
                        and hmac.compare_digest(operation["context"], context)):
                    if operation is self._active:
                        return 202, {"operation_id": identifier, "pending": True}
                    return operation["status"], {**operation["data"], "operation_id": identifier}
            return 410, {"error": "Router result expired. Check the current state before another change."}

    def completed_action(self):
        """Claim a completed action once from the repository's owning thread."""
        with self._lock:
            operation = self._completed
            if operation and not operation["recorded"]:
                operation["recorded"] = True
                return operation["path"], operation["status"]
        return None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._snapshot = None
            self._completed = None
