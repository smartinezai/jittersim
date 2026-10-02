"""Run the forced window on its own thread next to the dashboard and the scheduler."""

import logging
import threading
import time
from typing import Callable, Optional

from ..config.storage import ConfigRepository, StorageError
from .forced_window import ForcedWindowRunner, HelperWindowActions

LOGGER = logging.getLogger(__name__)

# A Pi that has not set its clock yet reports a date long ago. A window must not start then.
FIRST_PLAUSIBLE_TIME = 1_767_225_600  # 2026-01-01
NO_LOGIN = "No router login is saved on the Pi. Save it on the Schedules page."
CLOCK_NOT_SET = "The Pi's clock is not set yet."
HELPER_UNREACHABLE = "The networking helper does not answer."
NO_ETHERNET = "The Pi has no Ethernet connection to the router. Connect it before a window can start."


class ForcedWindowService:
    """Tick the runner every few seconds. Panic and Reset end a running window through it."""

    def __init__(
        self,
        *,
        database_path,
        helper_client,
        error_type,
        poll_seconds: float = 15.0,
        clock: Callable[[], float] = time.time,
        repository_factory=ConfigRepository,
    ) -> None:
        self._database_path = database_path
        self._client = helper_client
        self._error_type = error_type
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._repository_factory = repository_factory
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _ready(self) -> Optional[str]:
        if self._clock() < FIRST_PLAUSIBLE_TIME:
            return CLOCK_NOT_SET
        try:
            if not self._client.window_login_status().get("saved"):
                return NO_LOGIN
            # The router's Wi-Fi goes off. The Pi must keep its own way to the router and the dashboard.
            if not self._client.uplink_info().get("gateway"):
                return NO_ETHERNET
        except self._error_type:
            return HELPER_UNREACHABLE
        return None

    def _runner(self, repository) -> ForcedWindowRunner:
        def record(message: str) -> None:
            try:
                repository.record_activity(
                    kind="schedule", message=f"Forced window: {message}"[:500], now=self._clock()
                )
            except StorageError:
                LOGGER.warning("could not record the forced window event", exc_info=True)

        return ForcedWindowRunner(
            repository=repository,
            actions=HelperWindowActions(self._client, self._error_type),
            clock=self._clock,
            ready=self._ready,
            on_event=record,
        )

    def tick(self) -> None:
        with self._lock, self._repository_factory(self._database_path) as repository:
            self._runner(repository).tick()

    def request_end(self) -> None:
        """End the running window at the next tick. Panic and Reset call this."""

        with self._lock, self._repository_factory(self._database_path) as repository:
            self._runner(repository).request_end()
        # Do not wait for the next poll: the person pressed Panic. The steps can take minutes so
        # they run beside the request.
        threading.Thread(target=self._restore_now, name="jittersim-forced-window-end", daemon=True).start()

    def _restore_now(self) -> None:
        try:
            self.tick()
        except Exception:
            LOGGER.exception("could not restore the network after the window ended")

    def status(self) -> dict:
        with self._lock, self._repository_factory(self._database_path) as repository:
            return self._runner(repository).status()

    def start(self) -> None:
        if self._client is None or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="jittersim-forced-window", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            try:
                self.tick()
            except Exception:
                # The next tick tries again with fresh state.
                LOGGER.exception("forced window tick failed unexpectedly")
