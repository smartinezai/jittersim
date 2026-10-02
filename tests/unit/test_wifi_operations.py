"""Wi-Fi restarts must answer first, restart later and leave an outcome to read afterwards."""

import json
import threading
import time
import unittest

from jittersim.api.server import ControlServer
from jittersim.api.wifi_operations import WifiOperations, WifiOutcome
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError

WAIT = 5.0


def wait_until(condition, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class WifiOperationsTests(unittest.TestCase):
    def test_start_answers_before_the_work_runs_and_the_outcome_is_kept(self):
        entered, release = threading.Event(), threading.Event()
        order = []

        def work():
            order.append("work")
            entered.set()
            release.wait(WAIT)
            return WifiOutcome(200, "Done.", ssid="Lab", security="WPA3")

        operations = WifiOperations(delay=0.2)
        started = operations.start("access-point", work)
        order.append("returned")
        self.assertTrue(started["id"])
        self.assertTrue(entered.wait(WAIT))
        self.assertEqual(order[0], "returned")
        status, data = operations.last()
        self.assertEqual(status, 200)
        self.assertEqual(data["operation"]["state"], "running")
        self.assertEqual(data["operation"]["id"], started["id"])
        release.set()
        self.assertTrue(wait_until(lambda: operations.last()[1]["operation"]["state"] != "running"))
        done = operations.last(started["id"])[1]["operation"]
        self.assertEqual(done["state"], "succeeded")
        self.assertEqual((done["ssid"], done["security"], done["message"]), ("Lab", "WPA3", "Done."))

    def test_the_work_waits_for_the_delay_so_the_answer_can_leave_first(self):
        times = {}
        operations = WifiOperations(delay=0.3)
        began = time.monotonic()
        operations.start("reset-name", lambda: times.setdefault("ran", time.monotonic()) and
                         WifiOutcome(200, "ok"))
        returned = time.monotonic()
        self.assertTrue(wait_until(lambda: "ran" in times))
        self.assertLess(returned - began, 0.2)
        self.assertGreaterEqual(times["ran"] - began, 0.25)

    def test_only_one_change_runs_at_a_time(self):
        release = threading.Event()
        operations = WifiOperations(delay=0)
        operations.start("access-point", lambda: (release.wait(WAIT), WifiOutcome(200, "ok"))[1])
        self.assertTrue(operations.busy())
        self.assertIsNone(operations.start("reset-name", lambda: WifiOutcome(200, "ok")))
        release.set()
        self.assertTrue(wait_until(lambda: not operations.busy()))
        self.assertIsNotNone(operations.start("reset-name", lambda: WifiOutcome(200, "ok")))

    def test_a_crashing_change_is_stored_as_a_plain_failure(self):
        def broken():
            raise RuntimeError("secret detail from a command")

        operations = WifiOperations(delay=0)
        started = operations.start("access-point", broken)
        self.assertTrue(wait_until(lambda: not operations.busy()))
        failed = operations.last(started["id"])[1]["operation"]
        self.assertEqual(failed["state"], "failed")
        self.assertNotIn("secret", json.dumps(failed))
        self.assertTrue(failed["message"])

    def test_unknown_or_replaced_ids_and_old_results_are_gone(self):
        now = [1000.0]
        operations = WifiOperations(delay=0, clock=lambda: now[0])
        self.assertEqual(operations.last(), (200, {"operation": None}))
        first = operations.start("reset-name", lambda: WifiOutcome(200, "one"))
        self.assertTrue(wait_until(lambda: not operations.busy()))
        second = operations.start("reset-name", lambda: WifiOutcome(200, "two"))
        self.assertTrue(wait_until(lambda: not operations.busy()))
        self.assertEqual(operations.last(first["id"])[0], 410)
        self.assertEqual(operations.last(second["id"])[0], 200)
        now[0] += WifiOperations.RESULT_LIFETIME + 1
        self.assertEqual(operations.last(), (200, {"operation": None}))
        self.assertEqual(operations.last(second["id"])[0], 410)

    def test_a_finished_change_is_handed_to_the_action_history_once(self):
        operations = WifiOperations(delay=0)
        operations.start("reset-name", lambda: WifiOutcome(502, "failed"))
        self.assertTrue(wait_until(lambda: not operations.busy()))
        self.assertEqual(operations.completed_action(), ("/api/access-point/reset-name", 502))
        self.assertIsNone(operations.completed_action())

    def test_changes_that_run_on_their_own_thread_can_be_recorded(self):
        operations = WifiOperations(delay=0)
        identifier = operations.begin("router-copy")
        self.assertTrue(operations.busy())
        self.assertIsNone(operations.begin("router-copy"))
        operations.finish(identifier, WifiOutcome(200, "Copied.", ssid="Home", security="WPA2",
                                                  password_changed=True))
        view = operations.last(identifier)[1]["operation"]
        self.assertEqual((view["state"], view["ssid"], view["password_changed"]),
                         ("succeeded", "Home", True))
        self.assertEqual(operations.completed_action(), ("/api/router/sync-ap", 200))


class GatedHelper:
    """Records when the restart happens and holds it until the test lets go."""

    def __init__(self):
        self.events = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failure = None

    def _restart(self, name, result):
        self.events.append(name)
        self.entered.set()
        self.release.wait(WAIT)
        if self.failure:
            raise self.failure
        return result

    def reset_wifi_name(self):
        return self._restart("reset", {"ok": True, "ssid": "JitterSim", "security": "WPA2"})

    def configure_access_point(self, *, ssid, password):
        self.events.append(("configure", ssid))
        return self._restart("configure", {"ok": True, "action": "configure-ap", "commands_run": 3})

    def sync_access_point_from_router(self, *, admin_password, expected_fingerprint,
                                      confirm_legacy=False):
        return self._restart("sync", {"ok": True, "ssid": "Home 5G", "security": "WPA2",
                                      "band": "5 GHz", "commands_run": 3})


class ServerWifiRestartTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.addCleanup(self.server.server_close)
        self.server.wifi_operations.delay = 0
        self.helper = GatedHelper()
        self.server.helper_client = self.helper
        self.addCleanup(self.helper.release.set)

    def post(self, path, payload=None):
        status, _, body = self.server.write_response_for(path, payload or {})
        return status, json.loads(body), body

    def last(self, identifier=None):
        query = "id=" + identifier if identifier else ""
        status, _, body = self.server.response_for("/api/wifi-operation", query=query)
        return status, json.loads(body)

    def finish(self):
        self.helper.release.set()
        self.assertTrue(wait_until(lambda: not self.server.wifi_operations.busy()))

    def test_reset_name_answers_before_the_restart_and_stores_the_outcome(self):
        status, data, _ = self.post("/api/access-point/reset-name")
        self.assertEqual(status, 202)
        self.assertTrue(data["pending"])
        identifier = data["operation_id"]
        # The restart has not finished when the answer is given.
        self.assertTrue(self.helper.entered.wait(WAIT))
        self.assertEqual(self.last(identifier)[1]["operation"]["state"], "running")
        self.finish()
        operation = self.last(identifier)[1]["operation"]
        self.assertEqual(operation["state"], "succeeded")
        self.assertEqual(operation["kind"], "reset-name")
        self.assertEqual(operation["ssid"], "JitterSim")
        self.assertFalse(operation["password_changed"])
        self.assertIn("Password unchanged", operation["message"])

    def test_access_point_change_answers_first_and_never_stores_the_password(self):
        status, data, body = self.post(
            "/api/access-point", {"ssid": "Lab net", "password": "hunter2-hunter2"})
        self.assertEqual(status, 202)
        self.assertNotIn(b"hunter2", body)
        self.assertTrue(self.helper.entered.wait(WAIT))
        self.finish()
        status, stored = self.last(data["operation_id"])
        self.assertEqual(status, 200)
        operation = stored["operation"]
        self.assertEqual((operation["state"], operation["ssid"], operation["password_changed"]),
                         ("succeeded", "Lab net", True))
        self.assertNotIn("hunter2", json.dumps(stored))

    def test_failures_are_stored_with_the_same_safe_text_as_before(self):
        self.helper.failure = HelperClientError("details", reason="wifi-restored")
        _, data, _ = self.post("/api/access-point/reset-name")
        self.finish()
        operation = self.last(data["operation_id"])[1]["operation"]
        self.assertEqual(operation["state"], "failed")
        self.assertEqual(operation["status"], 502)
        self.assertIn("previous Wi-Fi settings were restored", operation["message"])
        self.assertIsNone(operation["ssid"])

    def test_bad_requests_and_a_missing_helper_are_refused_without_an_operation(self):
        status, data, _ = self.post("/api/access-point", {"ssid": "x"})
        self.assertEqual(status, 400)
        self.server.helper_client = None
        self.assertEqual(self.post("/api/access-point/reset-name")[0], 503)
        self.assertEqual(self.last()[1], {"operation": None})

    def test_a_second_wifi_change_while_one_runs_is_refused(self):
        self.assertEqual(self.post("/api/access-point/reset-name")[0], 202)
        self.assertTrue(self.helper.entered.wait(WAIT))
        status, data, _ = self.post(
            "/api/access-point", {"ssid": "Other", "password": "another-password"})
        self.assertEqual(status, 409)
        self.assertIn("running", data["error"])
        self.finish()

    def test_router_copy_is_recorded_and_waits_for_a_poll_to_see_its_outcome(self):
        self.helper.release.set()
        status, data, _ = self.post("/api/router/sync-ap", {
            "admin_password": "router-secret", "expected_fingerprint": "a" * 64,
            "background": True})
        self.assertEqual(status, 202)
        self.assertTrue(wait_until(lambda: not self.server.wifi_operations.busy()))
        operation = self.last()[1]["operation"]
        self.assertEqual((operation["kind"], operation["state"], operation["ssid"]),
                         ("router-copy", "succeeded", "Home 5G"))
        self.assertTrue(operation["password_changed"])
        self.assertNotIn("router-secret", json.dumps(operation))
        # The router operation keeps its own stored result for the page to poll by id.
        polled = self.post("/api/router/operation", {
            "admin_password": "router-secret", "operation_id": data["operation_id"]})
        self.assertEqual(polled[0], 200)

    def test_a_router_copy_that_crashes_is_a_failed_change_and_frees_the_slot(self):
        self.helper.release.set()

        def crash(**_):
            raise RuntimeError("unexpected")

        self.helper.sync_access_point_from_router = crash
        self.post("/api/router/sync-ap", {"admin_password": "router-secret",
                                          "expected_fingerprint": "a" * 64, "background": True})
        self.assertTrue(wait_until(lambda: not self.server.wifi_operations.busy()))
        operation = self.last()[1]["operation"]
        self.assertEqual((operation["kind"], operation["state"]), ("router-copy", "failed"))

    def test_a_router_copy_is_refused_while_another_wifi_change_runs(self):
        self.assertEqual(self.post("/api/access-point/reset-name")[0], 202)
        self.assertTrue(self.helper.entered.wait(WAIT))
        status, data, _ = self.post("/api/router/sync-ap", {
            "admin_password": "router-secret", "expected_fingerprint": "a" * 64,
            "background": True})
        self.assertEqual(status, 409)
        self.finish()

    def test_a_change_nobody_polled_is_not_lost_from_the_history_when_a_router_copy_follows(self):
        self.helper.release.set()
        self.post("/api/access-point/reset-name")
        self.assertTrue(wait_until(lambda: not self.server.wifi_operations.busy()))
        # Nobody asked for the result, because the Mac never came back.
        self.post("/api/router/sync-ap", {"admin_password": "router-secret",
                                          "expected_fingerprint": "a" * 64, "background": True})
        self.assertTrue(wait_until(lambda: not self.server.wifi_operations.busy()))
        self.last()
        actions = [entry["action"] for entry in self.repository.list_recent_actions()]
        self.assertIn("/api/access-point/reset-name", actions)
        self.assertIn("/api/router/sync-ap", actions)

    def test_wifi_operation_is_an_authenticated_read(self):
        from jittersim.api.server import PUBLIC_READ_PATHS

        self.assertNotIn("/api/wifi-operation", PUBLIC_READ_PATHS)
        status, _, _ = self.server.response_for("/api/wifi-operation")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
