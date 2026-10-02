"""Run Pi Wi-Fi restarts after the answer has left and remember how they ended.

Restarting the access point drops every test Wi-Fi client, including the browser that asked
for the change. The answer therefore goes out first, the restart follows a moment later on its
own thread and the outcome waits here until the browser is back and asks for it.
"""

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

ACTION_PATHS = {
    "reset-name": "/api/access-point/reset-name",
    "access-point": "/api/access-point",
    "router-copy": "/api/router/sync-ap",
}
LOST_OUTCOME = "The Wi-Fi change did not finish. Check the Pi Wi-Fi settings before trying again."


@dataclass(frozen=True)
class WifiOutcome:
    """What a finished change reports. It never carries a password."""

    status: int
    message: str
    ssid: Optional[str] = None
    security: Optional[str] = None
    password_changed: bool = False


class WifiOperations:
    """One Wi-Fi change at a time, with the last outcome kept for a while."""

    RESULT_LIFETIME = 3600.0

    def __init__(self, delay: float = 0.75, clock: Callable[[], float] = time.time) -> None:
        # Time for the answer to reach the browser before the Wi-Fi goes down.
        self.delay = delay
        self._clock = clock
        self._lock = threading.Lock()
        self._current: Optional[Dict[str, Any]] = None
        self._active = False
        self._unrecorded: Optional[Tuple[str, int]] = None
        self._closed = False

    def busy(self) -> bool:
        with self._lock:
            return self._active

    def _create(self, kind: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._closed or self._active:
                return None
            self._active = True
            self._current = {
                "id": secrets.token_urlsafe(18), "kind": kind, "state": "running",
                "status": None, "message": None, "ssid": None, "security": None,
                "password_changed": False, "started_at": self._clock(), "finished_at": None,
            }
            return dict(self._current)

    def begin(self, kind: str) -> Optional[str]:
        """Record a change that runs on a thread of its own. None means another one runs."""
        operation = self._create(kind)
        return operation["id"] if operation else None

    def finish(self, identifier: str, outcome: WifiOutcome) -> None:
        with self._lock:
            current = self._current
            if not current or current["id"] != identifier:
                return
            succeeded = outcome.status == 200
            current.update(
                state="succeeded" if succeeded else "failed", status=outcome.status,
                message=outcome.message, finished_at=self._clock(),
                ssid=outcome.ssid if succeeded else None,
                security=outcome.security if succeeded else None,
                password_changed=bool(outcome.password_changed and succeeded),
            )
            self._unrecorded = (ACTION_PATHS.get(current["kind"], current["kind"]), outcome.status)
            self._active = False

    def start(self, kind: str, work: Callable[[], WifiOutcome]) -> Optional[Dict[str, Any]]:
        """Return at once and run work after the delay. None means another change runs."""
        operation = self._create(kind)
        if operation is None:
            return None
        thread = threading.Thread(target=self._run, args=(operation["id"], work), daemon=True,
                                  name="wifi-operation")
        thread.start()
        return operation

    def _run(self, identifier: str, work: Callable[[], WifiOutcome]) -> None:
        time.sleep(max(0.0, self.delay))
        try:
            outcome = work()
        except Exception:
            # Details of a failed command can contain secrets. The log of the caller has them.
            outcome = WifiOutcome(502, LOST_OUTCOME)
        self.finish(identifier, outcome)

    def last(self, identifier: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        with self._lock:
            current = self._current
            if (current and current["finished_at"] is not None
                    and self._clock() - current["finished_at"] > self.RESULT_LIFETIME):
                self._current = current = None
            if identifier is not None and (not current or current["id"] != identifier):
                return 410, {"error": "That Wi-Fi change is no longer remembered. "
                                      "Check the Pi Wi-Fi settings before another change."}
            return 200, {"operation": dict(current) if current else None}

    def completed_action(self) -> Optional[Tuple[str, int]]:
        """Claim a finished change once, for the action history."""
        with self._lock:
            outcome, self._unrecorded = self._unrecorded, None
            return outcome

    def close(self) -> None:
        with self._lock:
            self._closed = True
