"""The forced window service and its API: ready checks, Panic and Reset, saving windows."""

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from jittersim.api.forced_window import FORCED_WINDOW_WRITES, MAX_WINDOWS
from jittersim.api.server import WRITE_PATHS, ControlServer
from jittersim.config.forced_window import ForcedWindow, WindowState
from jittersim.config.storage import ConfigRepository
from jittersim.engine.forced_window_service import (
    CLOCK_NOT_SET,
    HELPER_UNREACHABLE,
    NO_LOGIN,
    ForcedWindowService,
)
from jittersim.networking.helper import HelperClientError

SAT, SUN = 5, 6


def saturday(hour):
    # Saturday 3 October 2026 is a Saturday.
    return datetime(2026, 10, 3, hour).timestamp()


def wait_until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class FakeClient:
    def __init__(self):
        self.calls = []
        self.saved = True
        self.unreachable = False
        self.fail = set()

    def _do(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise HelperClientError(f"{name} failed")
        return {"ok": True}

    def window_login_status(self):
        if self.unreachable:
            raise HelperClientError("no helper")
        return {"ok": True, "saved": self.saved}

    def window_login_save(self, *, admin_password):
        self.calls.append(("save", admin_password))
        return self._do("login-save")

    def window_login_clear(self):
        return self._do("login-clear")

    def window_copy(self):
        return self._do("copy")

    def reset(self):
        return {"ok": True}

    def normal_access(self):
        return {"ok": True}

    def window_router(self, *, enabled):
        return self._do("router-on" if enabled else "router-off")

    def reset_wifi_name(self):
        return self._do("pi-reset")


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "settings.db"
        self.repository = ConfigRepository(self.path)
        self.addCleanup(self.repository.close)
        self.repository.save_forced_window(ForcedWindow("night", SAT, 15 * 60, SUN, 5 * 60))
        self.client = FakeClient()
        self.now = saturday(16)

    def service(self, **options):
        return ForcedWindowService(
            database_path=self.path, helper_client=self.client, error_type=HelperClientError,
            clock=lambda: self.now, poll_seconds=0.01, **options,
        )

    def test_a_window_runs_the_helper_steps_and_logs_them(self) -> None:
        service = self.service()

        service.tick()

        self.assertEqual(self.client.calls[-2:], ["copy", "router-off"])
        self.assertTrue(service.status()["router_wifi_off"])
        activity = [event["message"] for event in self.repository.list_activity()]
        self.assertIn("Forced window: The window started. The router's Wi-Fi is off.", activity)

    def test_a_window_waits_for_a_clock_a_login_and_a_helper(self) -> None:
        service = self.service()
        cases = (
            (CLOCK_NOT_SET, lambda: setattr(self, "now", 1000.0)),
            (NO_LOGIN, lambda: setattr(self.client, "saved", False)),
            (HELPER_UNREACHABLE, lambda: setattr(self.client, "unreachable", True)),
        )
        for reason, make in cases:
            with self.subTest(reason=reason):
                self.now = saturday(16)
                self.client.saved, self.client.unreachable = True, False
                self.repository.save_forced_window_state(WindowState())
                self.client.calls.clear()
                make()
                if reason == CLOCK_NOT_SET:
                    # a window that matches the old date would not exist so only the clock check is shown
                    self.repository.save_forced_window(ForcedWindow("night", 0, 0, 6, 1439))
                service.tick()
                self.assertEqual([c for c in self.client.calls if c in ("copy", "router-off")], [])
                self.assertEqual(service.status()["error"], reason)

    def test_panic_ends_a_running_window_at_once(self) -> None:
        service = self.service()
        service.tick()
        self.client.calls.clear()

        service.request_end()

        self.assertTrue(wait_until(lambda: self.client.calls[-2:] == ["pi-reset", "router-on"]))
        self.assertTrue(wait_until(lambda: service.status()["phase"] == "idle"))

    def test_the_loop_ticks_and_stops(self) -> None:
        service = self.service()
        service.start()
        try:
            self.assertTrue(wait_until(lambda: "router-off" in self.client.calls))
        finally:
            service.stop()
        calls = len(self.client.calls)
        time.sleep(0.1)
        self.assertEqual(len(self.client.calls), calls)

    def test_without_a_helper_the_loop_does_not_start(self) -> None:
        service = ForcedWindowService(
            database_path=self.path, helper_client=None, error_type=HelperClientError, poll_seconds=0.01
        )
        service.start()
        self.assertIsNone(service._thread)


class SpyService:
    def __init__(self, fail=False):
        self.ended = 0
        self.fail = fail
        self.router_wifi_off = True

    def request_end(self):
        self.ended += 1
        if self.fail:
            raise RuntimeError("boom")

    def status(self):
        return {"phase": "active", "window_id": "night", "ends_at": datetime(2026, 10, 4, 5).timestamp(),
                "error": None, "problem": None, "attempts": 0, "router_wifi_off": self.router_wifi_off}


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.client = FakeClient()
        self.service = SpyService()
        self.server = ControlServer(
            ("127.0.0.1", 0), self.repository, helper_client=self.client, forced_window=self.service
        )
        self.addCleanup(self.server.server_close)

    def post(self, path, payload):
        status, _, body = self.server.write_response_for(path, payload)
        return status, json.loads(body)

    def get(self):
        status, _, body = self.server.response_for("/api/forced-windows")
        self.assertEqual(status, 200)
        return json.loads(body)

    WINDOW = {"id": "night", "start_day": 5, "start_minute": 900, "end_day": 6, "end_minute": 300}

    def test_every_forced_window_write_needs_the_admin_token(self) -> None:
        self.assertLessEqual(FORCED_WINDOW_WRITES, WRITE_PATHS)

    def test_a_window_is_saved_listed_changed_and_deleted(self) -> None:
        status, body = self.post("/api/forced-windows", self.WINDOW)
        self.assertEqual((status, body["window"]["enabled"]), (200, True))
        self.post("/api/forced-windows", {**self.WINDOW, "enabled": False})

        listed = self.get()
        self.assertEqual([item["enabled"] for item in listed["windows"]], [False])
        self.assertEqual(listed["state"]["phase"], "active")
        self.assertEqual(listed["notice"], "The router's Wi-Fi is off until Sunday 05:00.")
        self.assertTrue(listed["available"])

        self.assertEqual(self.post("/api/forced-windows/delete", {"id": "night"})[0], 200)
        self.assertEqual(self.post("/api/forced-windows/delete", {"id": "night"})[0], 404)
        self.assertEqual(self.get()["windows"], [])

    def test_a_bad_window_is_refused(self) -> None:
        for change in ({"start_day": 9}, {"id": "Bad Id"}, {"end_minute": 0}, {"start_minute": "x"},
                       {"end_day": 5, "end_minute": 900}):
            with self.subTest(change=change):
                self.assertEqual(self.post("/api/forced-windows", {**self.WINDOW, **change})[0], 400)
        self.assertEqual(self.post("/api/forced-windows", {"id": "x"})[0], 400)
        self.assertEqual(self.get()["windows"], [])

    def test_the_number_of_windows_is_limited(self) -> None:
        for number in range(MAX_WINDOWS):
            self.assertEqual(self.post("/api/forced-windows", {**self.WINDOW, "id": f"w{number}"})[0], 200)
        self.assertEqual(self.post("/api/forced-windows", {**self.WINDOW, "id": "extra"})[0], 400)
        # an existing window can still change
        self.assertEqual(self.post("/api/forced-windows", {**self.WINDOW, "id": "w0"})[0], 200)

    def test_the_login_goes_to_the_helper_and_never_comes_back(self) -> None:
        status, body = self.post("/api/forced-windows/login", {"admin_password": "router-secret"})
        self.assertEqual((status, body), (200, {"login_saved": True}))
        self.assertIn(("save", "router-secret"), self.client.calls)
        self.assertNotIn("router-secret", json.dumps(self.get()))
        self.assertEqual(self.get()["login_saved"], True)
        self.assertEqual(self.post("/api/forced-windows/login/clear", {}), (200, {"login_saved": False}))
        for bad in ({}, {"admin_password": ""}, {"admin_password": 5}):
            self.assertEqual(self.post("/api/forced-windows/login", bad)[0], 400)

    def test_a_helper_error_is_shown(self) -> None:
        self.client.fail.add("login-save")
        status, body = self.post("/api/forced-windows/login", {"admin_password": "wrong"})
        self.assertEqual(status, 502)
        self.assertIn("failed", body["error"])

    def test_ending_a_window_by_hand(self) -> None:
        self.assertEqual(self.post("/api/forced-windows/end", {})[0], 200)
        self.assertEqual(self.service.ended, 1)

    def test_panic_and_reset_end_the_window_even_when_ending_fails(self) -> None:
        for fail in (False, True):
            for path in ("/api/normal-access", "/api/reset"):
                with self.subTest(path=path, fail=fail):
                    self.service.ended, self.service.fail = 0, fail
                    try:
                        self.server.write_response_for(path, {})
                    except Exception:
                        pass  # what Panic or Reset does next is not under test
                    self.assertEqual(self.service.ended, 1)

    def test_saving_or_deleting_a_window_ends_the_pause_after_panic(self) -> None:
        paused = WindowState(skipped_until=9e9)
        for path, payload in (("/api/forced-windows", self.WINDOW), ("/api/forced-windows/delete", {"id": "night"})):
            with self.subTest(path=path):
                self.post("/api/forced-windows", self.WINDOW)
                self.repository.save_forced_window_state(paused)
                self.assertEqual(self.post(path, payload)[0], 200)
                self.assertEqual(self.repository.get_forced_window_state().skipped_until, 0.0)
        # a running window is not touched
        self.repository.save_forced_window_state(WindowState(phase="active", window_id="night", skipped_until=5.0))
        self.post("/api/forced-windows", self.WINDOW)
        self.assertEqual(self.repository.get_forced_window_state().skipped_until, 5.0)

    def test_a_failure_while_ending_the_window_never_stops_panic(self) -> None:
        self.service.fail = True

        self.server._end_forced_window()  # must not raise

        self.assertEqual(self.service.ended, 1)

    def test_the_notice_only_shows_while_the_router_wifi_is_off(self) -> None:
        self.service.router_wifi_off = False

        self.assertIsNone(self.get()["notice"])

    def test_a_server_without_the_service_reports_it_unavailable(self) -> None:
        bare = ControlServer(("127.0.0.1", 0), self.repository)
        self.addCleanup(bare.server_close)
        status, _, body = bare.response_for("/api/forced-windows")
        self.assertEqual(json.loads(body)["available"], False)
        self.assertIsNone(json.loads(body)["state"])


if __name__ == "__main__":
    unittest.main()
