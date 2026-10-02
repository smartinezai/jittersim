"""The forced window: the schedule, the stored state and the steps with their rollbacks."""

import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from jittersim.config.forced_window import ACTIVE, COPYING, IDLE, RESTORING, ForcedWindow, WindowState
from jittersim.config.storage import ConfigRepository
from jittersim.engine.forced_window import (
    RETRY_SECONDS,
    ForcedWindowRunner,
    RouterLoginStore,
    WindowActionError,
)

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def at(day, hour, minute=0):
    """A local time in the week of Monday 28 September 2026."""

    return datetime(2026, 9, 28) + timedelta(days=day, hours=hour, minutes=minute)


class WindowScheduleTests(unittest.TestCase):
    def test_friday_to_sunday_covers_the_whole_weekend(self) -> None:
        window = ForcedWindow("weekend", FRI, 0, SUN, 1440)

        for moment in (at(FRI, 0), at(SAT, 12), at(SUN, 23, 59)):
            self.assertTrue(window.contains(moment), moment)
        for moment in (at(THU, 23, 59), at(MON, 0)):
            self.assertFalse(window.contains(moment), moment)

    def test_a_window_may_cross_midnight(self) -> None:
        window = ForcedWindow("night", SAT, 15 * 60, SUN, 5 * 60)

        for moment in (at(SAT, 15), at(SAT, 23, 59), at(SUN, 4, 59)):
            self.assertTrue(window.contains(moment), moment)
        for moment in (at(SAT, 14, 59), at(SUN, 5), at(FRI, 20)):
            self.assertFalse(window.contains(moment), moment)

    def test_a_window_may_cross_the_end_of_the_week(self) -> None:
        window = ForcedWindow("late", SUN, 20 * 60, MON, 6 * 60)

        for moment in (at(SUN, 22), at(MON, 5, 59)):
            self.assertTrue(window.contains(moment), moment)
        for moment in (at(SUN, 19, 59), at(MON, 6), at(WED, 12)):
            self.assertFalse(window.contains(moment), moment)

    def test_the_end_is_the_next_end_of_the_window(self) -> None:
        self.assertEqual(ForcedWindow("night", SAT, 900, SUN, 300).ends_at(at(SAT, 16, 30)), at(SUN, 5))
        self.assertEqual(ForcedWindow("night", SAT, 900, SUN, 300).ends_at(at(SUN, 1)), at(SUN, 5))
        self.assertEqual(ForcedWindow("late", SUN, 1200, MON, 360).ends_at(at(SUN, 22, 30)), at(MON + 7, 6))
        self.assertEqual(ForcedWindow("all", FRI, 0, SUN, 1440).ends_at(at(SAT, 10)), at(MON + 7, 0))

    def test_a_bad_window_is_refused(self) -> None:
        for arguments in (
            ("Bad Id", FRI, 0, SUN, 100), ("w", 7, 0, SUN, 100), ("w", FRI, 1440, SUN, 100),
            ("w", FRI, 0, SUN, 0), ("w", FRI, 0, SUN, 1441), ("w", FRI, 600, FRI, 600),
            ("w", FRI, 0, MON, 0),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                ForcedWindow(*arguments)
        with self.assertRaises(ValueError):
            WindowState(phase="sleeping")


class WindowStorageTests(unittest.TestCase):
    def test_windows_and_state_survive_a_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.db"
            repository = ConfigRepository(path)
            repository.save_forced_window(ForcedWindow("weekend", FRI, 0, SUN, 1440))
            repository.save_forced_window(ForcedWindow("weekend", FRI, 18 * 60, SUN, 1440, enabled=False))
            repository.save_forced_window_state(
                WindowState(phase=RESTORING, window_id="weekend", ends_at=12.5, error="e", problem="p",
                            attempts=3, next_attempt_at=7.5, stop_requested=True, pi_reset_done=True,
                            router_on_done=False, skipped_until=9.0)
            )
            repository.close()

            reopened = ConfigRepository(path)
            try:
                self.assertEqual(
                    reopened.list_forced_windows(),
                    (ForcedWindow("weekend", FRI, 18 * 60, SUN, 1440, enabled=False),),
                )
                state = reopened.get_forced_window_state()
                self.assertEqual((state.phase, state.attempts, state.stop_requested, state.pi_reset_done,
                                  state.router_on_done, state.problem), (RESTORING, 3, True, True, False, "p"))
                self.assertTrue(reopened.delete_forced_window("weekend"))
                self.assertFalse(reopened.delete_forced_window("weekend"))
                self.assertEqual(reopened.list_forced_windows(), ())
            finally:
                reopened.close()

    def test_a_new_database_has_an_idle_state(self) -> None:
        with ConfigRepository(":memory:") as repository:
            self.assertEqual(repository.get_forced_window_state(), WindowState())


class FakeActions:
    def __init__(self):
        self.calls = []
        self.fail = {}

    def _do(self, name):
        self.calls.append(name)
        failures = self.fail.get(name, 0)
        if failures:
            self.fail[name] = failures - 1
            raise WindowActionError(f"{name} failed")

    def copy_router_wifi(self):
        self._do("copy")

    def set_router_wifi(self, enabled):
        self._do("router-on" if enabled else "router-off")

    def reset_pi_wifi(self):
        self._do("pi-reset")


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        # Saturday 15:00 to Sunday 05:00
        self.repository.save_forced_window(ForcedWindow("night", SAT, 15 * 60, SUN, 5 * 60))
        self.actions = FakeActions()
        self.now = at(SAT, 14).timestamp()
        self.events = []
        self.reason = None
        self.runner = self.make_runner()

    def make_runner(self):
        return ForcedWindowRunner(
            repository=self.repository, actions=self.actions, clock=lambda: self.now,
            ready=lambda: self.reason, on_event=self.events.append,
        )

    def tick_at(self, moment):
        self.now = moment.timestamp()
        return self.runner.tick()

    def test_nothing_happens_outside_the_window(self) -> None:
        self.assertEqual(self.tick_at(at(SAT, 14, 59)).phase, IDLE)
        self.assertEqual(self.actions.calls, [])

    def test_the_window_copies_first_and_switches_the_router_off_second(self) -> None:
        state = self.tick_at(at(SAT, 15))

        self.assertEqual(self.actions.calls, ["copy", "router-off"])
        self.assertEqual(state.phase, ACTIVE)
        self.assertEqual(state.ends_at, at(SUN, 5).timestamp())
        self.assertTrue(self.runner.status()["router_wifi_off"])
        self.tick_at(at(SAT, 20))
        self.assertEqual(self.actions.calls, ["copy", "router-off"])

    def test_the_end_brings_the_pi_name_back_before_the_router_wifi(self) -> None:
        self.tick_at(at(SAT, 15))
        self.actions.calls.clear()

        state = self.tick_at(at(SUN, 5))

        self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])
        self.assertEqual(state, WindowState())
        self.assertFalse(self.runner.status()["router_wifi_off"])

    def test_a_failed_copy_never_switches_the_router_off(self) -> None:
        self.actions.fail["copy"] = 1

        state = self.tick_at(at(SAT, 15))

        self.assertNotIn("router-off", self.actions.calls)
        self.assertEqual(self.actions.calls, ["copy", "pi-reset", "router-on"])
        self.assertEqual(state.phase, IDLE)
        self.assertIn("could not start", state.problem)
        self.assertEqual(self.events[0], state.problem)
        # it does not try again for the rest of that window
        self.tick_at(at(SAT, 16))
        self.assertEqual(self.actions.calls, ["copy", "pi-reset", "router-on"])
        # and runs the next one
        self.tick_at(at(SAT + 7, 15))
        self.assertEqual(self.actions.calls[-2:], ["copy", "router-off"])
        self.assertIsNone(self.runner.status()["problem"])

    def test_a_failed_switch_off_rolls_back_to_the_pi_name_and_a_router_that_is_on(self) -> None:
        self.actions.fail["router-off"] = 1

        state = self.tick_at(at(SAT, 15))

        self.assertEqual(self.actions.calls, ["copy", "router-off", "pi-reset", "router-on"])
        self.assertEqual(state.phase, IDLE)
        self.assertIn("did not switch off", state.problem)
        self.assertEqual(self.events[0], state.problem)
        self.tick_at(at(SAT, 16))
        self.assertEqual(len(self.actions.calls), 4)

    def test_a_silent_router_at_the_end_is_retried_with_an_alert_after_the_pi_name_is_back(self) -> None:
        self.tick_at(at(SAT, 15))
        self.actions.calls.clear()
        self.actions.fail["router-on"] = 2

        state = self.tick_at(at(SUN, 5))

        self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])
        self.assertEqual(state.phase, RESTORING)
        self.assertIn("did not switch back on", state.error)
        self.assertEqual(state.attempts, 1)
        self.assertTrue(any("did not switch back on" in event for event in self.events))
        # nothing happens before the retry time and the Pi name is not reset again
        self.tick_at(at(SUN, 5, 0).replace(second=int(RETRY_SECONDS) - 1))
        self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])
        state = self.tick_at(at(SUN, 6, 0))
        self.assertEqual(self.actions.calls, ["pi-reset", "router-on", "router-on"])
        self.assertEqual(state.attempts, 2)
        state = self.tick_at(at(SUN, 7, 0))
        self.assertEqual(self.actions.calls[-1], "router-on")
        self.assertEqual(state.phase, IDLE)
        self.assertEqual(self.actions.calls.count("pi-reset"), 1)

    def test_the_router_wifi_goes_on_even_when_the_pi_name_will_not_come_back(self) -> None:
        self.tick_at(at(SAT, 15))
        self.actions.calls.clear()
        self.actions.fail["pi-reset"] = 1

        state = self.tick_at(at(SUN, 5))

        self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])
        self.assertEqual(state.phase, RESTORING)
        self.assertIn("own Wi-Fi did not come back", state.error)
        state = self.tick_at(at(SUN, 6))
        self.assertEqual(self.actions.calls, ["pi-reset", "router-on", "pi-reset"])
        self.assertEqual(state.phase, IDLE)

    def test_a_restart_in_the_middle_of_the_copy_is_undone(self) -> None:
        self.repository.save_forced_window_state(
            WindowState(phase=COPYING, window_id="night", ends_at=at(SUN, 5).timestamp())
        )
        restarted = self.make_runner()
        self.now = at(SAT, 16).timestamp()

        state = restarted.tick()

        self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])
        self.assertEqual(state.phase, IDLE)
        self.assertIn("stopped during the copy", state.problem)

    def test_a_restart_after_the_copy_carries_on_without_copying_again(self) -> None:
        self.repository.save_forced_window_state(
            WindowState(phase="copied", window_id="night", ends_at=at(SUN, 5).timestamp())
        )
        self.now = at(SAT, 16).timestamp()

        state = self.make_runner().tick()

        self.assertEqual(self.actions.calls, ["router-off"])
        self.assertEqual(state.phase, ACTIVE)

    def test_a_restart_during_the_window_changes_nothing(self) -> None:
        self.tick_at(at(SAT, 15))
        self.actions.calls.clear()
        restarted = self.make_runner()
        self.now = at(SAT, 22).timestamp()

        self.assertEqual(restarted.tick().phase, ACTIVE)
        self.assertEqual(self.actions.calls, [])

    def test_a_pi_that_was_off_at_the_end_restores_when_it_starts_again(self) -> None:
        self.tick_at(at(SAT, 15))
        self.actions.calls.clear()
        restarted = self.make_runner()
        self.now = at(SUN, 9).timestamp()

        self.assertEqual(restarted.tick().phase, IDLE)
        self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])

    def test_panic_ends_the_window_and_it_does_not_start_again_that_night(self) -> None:
        self.tick_at(at(SAT, 15))
        self.actions.calls.clear()

        self.runner.request_end()
        state = self.tick_at(at(SAT, 16))

        self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])
        self.assertEqual(state.phase, IDLE)
        self.tick_at(at(SAT, 17))
        self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])

    def test_ending_an_idle_window_does_nothing(self) -> None:
        self.runner.request_end()
        self.assertEqual(self.repository.get_forced_window_state(), WindowState())

    def test_a_disabled_or_deleted_window_ends_at_once(self) -> None:
        for change in (
            lambda: self.repository.save_forced_window(ForcedWindow("night", SAT, 900, SUN, 300, enabled=False)),
            lambda: self.repository.delete_forced_window("night"),
        ):
            with self.subTest(change=change):
                self.repository.save_forced_window(ForcedWindow("night", SAT, 900, SUN, 300))
                self.repository.save_forced_window_state(WindowState())
                self.tick_at(at(SAT, 15))
                self.actions.calls.clear()
                change()
                self.assertEqual(self.tick_at(at(SAT, 16)).phase, IDLE)
                self.assertEqual(self.actions.calls, ["pi-reset", "router-on"])

    def test_a_window_does_not_start_until_the_pi_is_ready(self) -> None:
        self.reason = "The Pi's clock is not set yet."

        state = self.tick_at(at(SAT, 15))
        self.tick_at(at(SAT, 15, 1))

        self.assertEqual(self.actions.calls, [])
        self.assertEqual(state.error, self.reason)
        self.assertEqual(self.events, [self.reason])
        self.reason = None
        self.assertEqual(self.tick_at(at(SAT, 15, 2)).phase, ACTIVE)
        self.assertIsNone(self.runner.status()["error"])

    def test_the_events_tell_the_story(self) -> None:
        self.tick_at(at(SAT, 15))
        self.tick_at(at(SUN, 5))

        self.assertEqual(
            self.events,
            ["The window started. The router's Wi-Fi is off.", "The window ended. The router's Wi-Fi is on."],
        )


class RouterLoginStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private" / "router-login"
        self.store = RouterLoginStore(self.path)

    def test_the_login_is_saved_for_its_owner_only(self) -> None:
        self.store.save("first password")
        self.store.save("second password")

        self.assertEqual(self.store.load(), "second password")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual([item.name for item in self.path.parent.iterdir()], ["router-login"])

    def test_a_file_that_others_can_read_is_not_trusted(self) -> None:
        self.store.save("secret")
        os.chmod(self.path, 0o644)

        self.assertIsNone(self.store.load())

    def test_nothing_saved_loads_nothing_and_clearing_twice_is_fine(self) -> None:
        self.assertIsNone(self.store.load())
        self.store.save("secret")
        self.store.clear()
        self.store.clear()
        self.assertIsNone(self.store.load())

    def test_a_bad_password_is_refused(self) -> None:
        for bad in ("", None, 5, "x" * 1025):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.save(bad)


if __name__ == "__main__":
    unittest.main()
