"""Run a forced window: the router's Wi-Fi goes off and the Pi's test Wi-Fi takes its name.

Order matters. The Pi copies the router's Wi-Fi name and password first. Only when that worked
does the router's Wi-Fi go off. At the end the Pi's own name comes back first and the router's
Wi-Fi goes on after it. The state is stored after every step so a restart carries on or restores.
Every step is safe to repeat.
"""

import time
from datetime import datetime
from typing import Callable, Optional

from ..config.router_login import RouterLoginStore  # noqa: F401 (the store lives in config so the helper can use it)
from ..config.forced_window import (
    ACTIVE,
    COPIED,
    COPYING,
    IDLE,
    RESTORING,
    ForcedWindow,
    WindowState,
)

RETRY_SECONDS = 60.0


class WindowActionError(Exception):
    """A step failed. The message is safe to show on the dashboard."""


class ForcedWindowRunner:
    """Move the stored window state one step each tick.

    actions needs copy_router_wifi() and set_router_wifi(enabled) and reset_pi_wifi(). Each one
    raises WindowActionError when it fails. ready() returns None or the reason a window must not
    start now such as an unsynced clock or no Ethernet path.
    """

    def __init__(
        self,
        *,
        repository,
        actions,
        clock: Callable[[], float] = time.time,
        ready: Callable[[], Optional[str]] = lambda: None,
        on_event: Callable[[str], None] = lambda message: None,
        interrupted: Callable[[], bool] = lambda: False,
    ) -> None:
        self._repository = repository
        self._actions = actions
        self._clock = clock
        self._ready = ready
        self._on_event = on_event
        # True while Panic or Reset waits to end the window. A start stops between its steps.
        self._interrupted = interrupted

    def status(self) -> dict:
        state = self._repository.get_forced_window_state()
        return {
            "phase": state.phase,
            "window_id": state.window_id,
            "ends_at": state.ends_at,
            "error": state.error,
            "problem": state.problem,
            "attempts": state.attempts,
            "router_wifi_off": state.phase in (ACTIVE,),
        }

    def request_end(self) -> None:
        """Panic and Reset call this. The window restores at the next tick and does not start again."""

        state = self._repository.get_forced_window_state()
        if state.phase == IDLE:
            return
        self._save(state.but(stop_requested=True, skipped_until=state.ends_at or 0.0))

    def tick(self) -> WindowState:
        state = self._repository.get_forced_window_state()
        now = self._clock()
        if state.phase == IDLE:
            return self._maybe_start(state, now)
        if state.phase == COPYING:
            # A restart in the middle of the copy. What happened to the Pi's Wi-Fi is unknown.
            return self._begin_restore(state, None, "The window stopped during the copy and was undone.")
        if state.phase == COPIED:
            if self._keep_going(state, now):
                return self._switch_off(state, now)
            return self._begin_restore(state)
        if state.phase == ACTIVE:
            if self._keep_going(state, now):
                return state
            return self._begin_restore(state)
        return self._restore_step(state, now)

    def _windows(self):
        return {window.id: window for window in self._repository.list_forced_windows()}

    def _keep_going(self, state: WindowState, now: float) -> bool:
        window = self._windows().get(state.window_id)
        return (
            window is not None
            and window.enabled
            and not state.stop_requested
            and state.ends_at is not None
            and now < state.ends_at
        )

    def _save(self, state: WindowState) -> WindowState:
        self._repository.save_forced_window_state(state)
        return state

    def _maybe_start(self, state: WindowState, now: float) -> WindowState:
        moment = datetime.fromtimestamp(now)
        window = next(
            (item for item in self._windows().values() if item.enabled and item.contains(moment)), None
        )
        if window is None or now < state.skipped_until:
            return state
        ends_at = window.ends_at(moment).timestamp()
        reason = self._ready()
        if reason is not None:
            if state.error != reason:
                self._on_event(reason)
                return self._save(state.but(error=reason))
            return state
        running = self._save(WindowState(phase=COPYING, window_id=window.id, ends_at=ends_at))
        try:
            self._actions.copy_router_wifi()
        except WindowActionError as error:
            # Nothing was switched off. Put the Pi's own name back in case the copy half worked.
            self._on_event(f"The window could not start: {error}")
            return self._begin_restore(
                running.but(skipped_until=ends_at), None, f"The window could not start: {error}"
            )
        running = self._save(running.but(phase=COPIED))
        if self._interrupted():
            message = "The window was ended while it started."
            self._on_event(message)
            return self._begin_restore(running.but(skipped_until=ends_at), None, message)
        return self._switch_off(running, now)

    def _switch_off(self, state: WindowState, now: float) -> WindowState:
        try:
            self._actions.set_router_wifi(False)
        except WindowActionError as error:
            message = f"The router's Wi-Fi did not switch off: {error}"
            self._on_event(message)
            return self._begin_restore(state.but(skipped_until=state.ends_at or 0.0), None, message)
        self._on_event("The window started. The router's Wi-Fi is off.")
        return self._save(state.but(phase=ACTIVE, error=None, attempts=0))

    def _begin_restore(
        self, state: WindowState, error: Optional[str] = None, problem: Optional[str] = None
    ) -> WindowState:
        return self._restore_step(
            self._save(
                state.but(
                    phase=RESTORING,
                    error=error,
                    problem=problem or state.problem,
                    attempts=0,
                    next_attempt_at=0.0,
                    pi_reset_done=False,
                    router_on_done=False,
                )
            ),
            self._clock(),
        )

    def _restore_step(self, state: WindowState, now: float) -> WindowState:
        """Bring the Pi's own name back and the router's Wi-Fi on. Neither waits for the other.

        If the Pi's own name does not come back the router's Wi-Fi still goes on so that nobody is
        left without Wi-Fi. A step that worked is not repeated.
        """

        if now < state.next_attempt_at:
            return state
        problems = []
        if not state.pi_reset_done:
            try:
                self._actions.reset_pi_wifi()
                state = self._save(state.but(pi_reset_done=True))
            except WindowActionError as error:
                problems.append(f"The Pi's own Wi-Fi did not come back: {error}")
        if not state.router_on_done:
            try:
                self._actions.set_router_wifi(True)
                state = self._save(state.but(router_on_done=True))
            except WindowActionError as error:
                problems.append(f"The router's Wi-Fi did not switch back on: {error}")
        if problems:
            return self._retry(state, now, " ".join(problems) + " Trying again.")
        self._on_event("The window ended. The router's Wi-Fi is on.")
        return self._save(WindowState(skipped_until=state.skipped_until, problem=state.problem))

    def _retry(self, state: WindowState, now: float, message: str) -> WindowState:
        if state.error != message:
            self._on_event(message)
        return self._save(
            state.but(error=message, attempts=state.attempts + 1, next_attempt_at=now + RETRY_SECONDS)
        )


class HelperWindowActions:
    """The window steps as the networking helper runs them. The router login stays on the Pi.

    client is a HelperClient. It raises an error with a message that is safe to show.
    """

    def __init__(self, client, error_type) -> None:
        self._client = client
        self._error_type = error_type

    def _run(self, call):
        try:
            return call()
        except self._error_type as error:
            raise WindowActionError(str(error)) from error

    def copy_router_wifi(self) -> None:
        self._run(self._client.window_copy)

    def set_router_wifi(self, enabled: bool) -> None:
        self._run(lambda: self._client.window_router(enabled=enabled))

    def reset_pi_wifi(self) -> None:
        self._run(self._client.reset_wifi_name)
