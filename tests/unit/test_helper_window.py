"""The helper steps of a forced window: a saved router login that never leaves the Pi."""

import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from jittersim.engine.forced_window import HelperWindowActions, WindowActionError
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.helper import HelperClient, HelperClientError, HelperService, UnixHelperServer
from jittersim.networking.router import RouterError, RouterWifiSettings
from jittersim.networking.topology import Topology
from tests.unit.test_helper import FakeRouter, UplinkOutputExecutor


class WindowHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.login_path = Path(self.directory.name) / "etc" / "router-login"
        self.routers = []
        self.router_class = FakeRouter

    def factory(self, address, password):
        router = self.router_class(address, password)
        self.routers.append(router)
        return router

    def service(self, *, path="default"):
        return HelperService(
            NetworkApplier(
                executor=UplinkOutputExecutor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=self.factory,
            router_login_path=self.login_path if path == "default" else path,
        )

    def test_a_working_login_is_saved_for_the_owner_only_and_never_sent_back(self) -> None:
        service = self.service()

        saved = service.handle_payload({"action": "window-login-save", "admin_password": "router-secret"})

        self.assertEqual(saved, {"ok": True, "action": "window-login-save", "saved": True})
        self.assertNotIn("router-secret", str(saved))
        self.assertEqual(self.login_path.read_text(), "router-secret")
        self.assertEqual(stat.S_IMODE(os.stat(self.login_path).st_mode), 0o600)
        status = service.handle_payload({"action": "window-login-status"})
        self.assertEqual(status, {"ok": True, "action": "window-login-status", "saved": True})
        self.assertNotIn("router-secret", str(status))
        self.assertTrue(all(router.closed for router in self.routers))

    def test_a_login_the_router_refuses_is_not_saved(self) -> None:
        class Refusing(FakeRouter):
            def login(self, **kwargs):
                raise RouterError("refused", reason="credentials")

        self.router_class = Refusing
        service = self.service()

        response = service.handle_payload({"action": "window-login-save", "admin_password": "wrong"})

        self.assertFalse(response["ok"])
        self.assertFalse(self.login_path.exists())
        self.assertFalse(service.handle_payload({"action": "window-login-status"})["saved"])

    def test_a_bad_password_is_refused_before_the_router_is_asked(self) -> None:
        service = self.service()
        for bad in ("", None, 5, "x" * 1025):
            with self.subTest(bad=bad):
                response = service.handle_payload({"action": "window-login-save", "admin_password": bad})
                self.assertFalse(response["ok"])
        self.assertEqual(self.routers, [])

    def test_the_login_can_be_cleared(self) -> None:
        service = self.service()
        service.handle_payload({"action": "window-login-save", "admin_password": "router-secret"})

        self.assertEqual(service.handle_payload({"action": "window-login-clear"})["saved"], False)

        self.assertFalse(self.login_path.exists())
        self.assertFalse(service.handle_payload({"action": "window-login-status"})["saved"])

    def test_without_a_saved_login_nothing_touches_the_router(self) -> None:
        service = self.service()

        for payload in ({"action": "window-copy"}, {"action": "window-router", "enabled": False}):
            with self.subTest(payload=payload):
                response = service.handle_payload(payload)
                self.assertFalse(response["ok"])
                self.assertEqual(response["reason"], "no-login")
        self.assertEqual(self.routers, [])

    def test_a_pi_without_the_setting_says_so(self) -> None:
        service = self.service(path=None)

        for action in ("window-login-save", "window-login-clear", "window-login-status"):
            response = service.handle_payload({"action": action, "admin_password": "x"})
            self.assertFalse(response["ok"], action)
        self.assertFalse(service.handle_payload({"action": "window-copy"})["ok"])

    def test_the_window_copies_the_router_wifi_with_the_saved_login(self) -> None:
        service = self.service()
        service.handle_payload({"action": "window-login-save", "admin_password": "router-secret"})
        self.routers.clear()

        response = service.handle_payload({"action": "window-copy"})

        self.assertEqual(response["action"], "router-sync-ap")
        self.assertEqual(response["ssid"], "Vodafone-Test")
        self.assertTrue(self.routers)
        self.assertTrue(all(router.password == "router-secret" for router in self.routers))
        self.assertTrue(all(router.closed for router in self.routers))

    def test_an_unattended_copy_refuses_older_security(self) -> None:
        class Legacy(FakeRouter):
            def read_wifi_settings(self):
                return RouterWifiSettings("Old", "password", True, "WPA", "2.4 GHz")

        service = self.service()
        service.handle_payload({"action": "window-login-save", "admin_password": "router-secret"})
        self.router_class = Legacy

        response = service.handle_payload({"action": "window-copy"})

        self.assertFalse(response["ok"])
        self.assertEqual(response["reason"], "legacy")
        self.assertIn("older Wi-Fi security mode", response["error"])

    def test_the_window_switches_the_router_wifi_with_the_saved_login(self) -> None:
        service = self.service()
        service.handle_payload({"action": "window-login-save", "admin_password": "router-secret"})
        self.routers.clear()

        off = service.handle_payload({"action": "window-router", "enabled": False})
        on = service.handle_payload({"action": "window-router", "enabled": True})

        self.assertEqual((off["enabled"], on["enabled"]), (False, True))
        self.assertTrue(all(router.password == "router-secret" for router in self.routers))
        self.assertTrue(all(router.closed for router in self.routers))

    def test_a_router_state_that_is_not_true_or_false_is_refused(self) -> None:
        service = self.service()
        service.handle_payload({"action": "window-login-save", "admin_password": "router-secret"})
        self.routers.clear()

        for bad in (None, "yes", 1):
            with self.subTest(bad=bad):
                response = service.handle_payload({"action": "window-router", "enabled": bad})
                self.assertEqual(response, {"ok": False, "error": "router Wi-Fi state is invalid"})
        self.assertEqual(self.routers, [])

    def test_the_login_file_can_be_checked_and_removed_while_a_router_step_runs(self) -> None:
        service = self.service()
        service.handle_payload({"action": "window-login-save", "admin_password": "router-secret"})
        self.assertTrue(service._router_lock.acquire(blocking=False))
        try:
            status = service.handle_payload({"action": "window-login-status"})
            self.assertEqual((status["ok"], status["saved"]), (True, True))
            self.assertEqual(service.handle_payload({"action": "window-login-clear"})["saved"], False)
        finally:
            service._router_lock.release()

    def test_the_socket_lets_the_login_file_actions_through_a_busy_helper(self) -> None:
        from jittersim.networking import helper as helper_module

        for action in ("window-login-status", "window-login-clear"):
            self.assertNotIn(action, helper_module.ROUTER_ACTIONS)
            self.assertIn(action, helper_module.WINDOW_LOGIN_FILE_ACTIONS)
        for action in ("window-login-save", "window-copy", "window-router"):
            self.assertIn(action, helper_module.ROUTER_ACTIONS)

    def test_a_busy_router_answers_busy_for_the_window_steps(self) -> None:
        service = self.service()
        service.handle_payload({"action": "window-login-save", "admin_password": "router-secret"})
        self.assertTrue(service._router_lock.acquire(blocking=False))
        try:
            for payload in ({"action": "window-copy"}, {"action": "window-router", "enabled": True}):
                self.assertEqual(service.handle_payload(payload)["reason"], "helper-busy")
        finally:
            service._router_lock.release()


class WindowSocketTests(unittest.TestCase):
    """The same steps over the real socket with a real client."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.routers = []

        def factory(address, password):
            router = FakeRouter(address, password)
            self.routers.append(router)
            return router

        service = HelperService(
            NetworkApplier(
                executor=UplinkOutputExecutor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=factory,
            router_login_path=Path(self.directory.name) / "router-login",
        )
        socket_path = Path(self.directory.name) / "helper.sock"
        self.server = UnixHelperServer(socket_path, service)
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = HelperClient(socket_path=socket_path)

    def test_a_window_works_through_the_socket(self) -> None:
        self.assertEqual(self.client.window_login_status()["saved"], False)
        self.client.window_login_save(admin_password="router-secret")
        self.assertEqual(self.client.window_login_status()["saved"], True)

        self.assertEqual(self.client.window_copy()["ssid"], "Vodafone-Test")
        self.assertEqual(self.client.window_router(enabled=False)["enabled"], False)
        self.assertEqual(self.client.window_router(enabled=True)["enabled"], True)
        self.assertTrue(all(router.password == "router-secret" for router in self.routers))

    def test_a_missing_login_is_a_client_error_with_a_message(self) -> None:
        with self.assertRaisesRegex(HelperClientError, "No router login is saved"):
            self.client.window_copy()
        with self.assertRaisesRegex(HelperClientError, "No router login is saved"):
            self.client.window_router(enabled=False)

    def test_the_login_file_actions_answer_while_the_helper_is_busy(self) -> None:
        self.client.window_login_save(admin_password="router-secret")
        self.assertTrue(self.server.operation_lock.acquire(blocking=False))
        try:
            self.assertEqual(self.client.window_login_status()["saved"], True)
            self.assertEqual(self.client.window_login_clear()["saved"], False)
        finally:
            self.server.operation_lock.release()


class WindowClientTests(unittest.TestCase):
    def test_the_client_sends_the_window_requests(self) -> None:
        sent = []

        class Recording(HelperClient):
            def _request(self, payload, *, timeout_seconds=None):
                sent.append(payload)
                return {"ok": True}

        client = Recording(socket_path=Path("/nonexistent"))
        client.window_login_save(admin_password="router-secret")
        client.window_login_clear()
        client.window_login_status()
        client.window_copy()
        client.window_router(enabled=False)

        self.assertEqual(
            sent,
            [
                {"action": "window-login-save", "admin_password": "router-secret"},
                {"action": "window-login-clear"},
                {"action": "window-login-status"},
                {"action": "window-copy"},
                {"action": "window-router", "enabled": False},
            ],
        )

    def test_the_adapter_turns_helper_errors_into_window_errors(self) -> None:
        class Failing:
            def window_copy(self):
                raise HelperClientError("router did not answer")

            def window_router(self, *, enabled):
                raise HelperClientError("router did not answer")

            def reset_wifi_name(self):
                raise HelperClientError("busy")

        actions = HelperWindowActions(Failing(), HelperClientError)
        for call in (actions.copy_router_wifi, lambda: actions.set_router_wifi(True), actions.reset_pi_wifi):
            with self.assertRaises(WindowActionError):
                call()

    def test_the_adapter_calls_the_matching_helper_steps(self) -> None:
        calls = []

        class Working:
            def window_copy(self):
                calls.append("copy")

            def window_router(self, *, enabled):
                calls.append(("router", enabled))

            def reset_wifi_name(self):
                calls.append("reset")

        actions = HelperWindowActions(Working(), HelperClientError)
        actions.copy_router_wifi()
        actions.set_router_wifi(False)
        actions.reset_pi_wifi()

        self.assertEqual(calls, ["copy", ("router", False), "reset"])


if __name__ == "__main__":
    unittest.main()
