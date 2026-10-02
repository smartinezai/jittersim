"""One slow dashboard request must not freeze the rest of the dashboard."""

import http.client
import json
import threading
import time
import unittest
from unittest.mock import patch

from jittersim.api import server as api
from jittersim.config.models import Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler

TOKEN = "test-admin"
FAST = 1.0  # a request that is not queued behind a slow one answers well within this


class SlowApplyHelper:
    """A helper whose apply waits until the test lets it finish."""

    def __init__(self):
        self.apply_started = threading.Event()
        self.release_apply = threading.Event()
        self.normal_access_calls = 0
        self.reset_calls = 0

    def ping(self):
        return {"ok": True}

    def apply(self, *, plan, explicit_apply=False):
        self.apply_started.set()
        if not self.release_apply.wait(10):
            raise AssertionError("the test never released the apply")
        return {"commands_run": 1}

    def reset(self):
        self.reset_calls += 1
        return {"commands_run": 1}

    def normal_access(self):
        self.normal_access_calls += 1
        return {"commands_run": 1}

    def gateway_status(self):
        return {"outage_stop_latched": False, "state_error": False}

    def connected_device_snapshot(self):
        return {"devices": [], "association_status_available": True}

    def traffic_counters(self):
        return {"devices": {}, "services": {}}

    def observe_dns_queries(self):
        return []


class DashboardConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.helper = SlowApplyHelper()
        self.scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        self.server = api.ControlServer(
            ("127.0.0.1", 0), self.repository, admin_token=TOKEN,
            helper_client=self.helper, scheduler=self.scheduler,
        )
        background = patch.object(self.scheduler, "start")
        background.start()
        self.addCleanup(background.stop)
        self.serving = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.serving.start()
        self.threads = []
        self.addCleanup(self._stop)

    def _stop(self):
        self.helper.release_apply.set()
        for thread in self.threads:
            thread.join(timeout=5)
        self.server.shutdown()
        self.serving.join(timeout=5)
        self.server.server_close()

    def request(self, method, path, body=None, timeout=10):
        connection = http.client.HTTPConnection(*self.server.server_address[:2], timeout=timeout)
        try:
            headers = {"Authorization": "Bearer " + TOKEN}
            payload = None if body is None else json.dumps(body)
            if payload is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def start_slow_apply(self):
        result = {}

        def run():
            result["response"] = self.request("POST", "/api/apply", {})

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        self.assertTrue(self.helper.apply_started.wait(5), "the apply never reached the helper")
        return result, thread

    def timed(self, method, path, body=None):
        started = time.monotonic()
        status, headers, data = self.request(method, path, body, timeout=FAST * 3)
        return status, data, time.monotonic() - started

    def test_health_and_reads_answer_while_an_apply_is_running(self):
        result, thread = self.start_slow_apply()

        for path in ("/api/health", "/api/profiles", "/api/conditions", "/api/connected-devices"):
            status, _, elapsed = self.timed("GET", path)
            self.assertEqual(status, 200, path)
            self.assertLess(elapsed, FAST, path + " waited behind the apply")

        self.assertNotIn("response", result, "the apply must still be running")
        self.helper.release_apply.set()
        thread.join(5)
        self.assertEqual(result["response"][0], 200)

    def test_panic_answers_while_an_apply_is_running_and_wins(self):
        result, thread = self.start_slow_apply()

        status, data, elapsed = self.timed("POST", "/api/normal-access", {})

        self.assertEqual(status, 200, data)
        self.assertLess(elapsed, FAST, "Panic waited behind the apply")
        self.assertEqual(self.helper.normal_access_calls, 1)
        self.assertFalse(self.scheduler.is_active)
        self.assertNotIn("response", result, "the apply must still be running")

        self.helper.release_apply.set()
        thread.join(5)
        status, _, body = result["response"]
        self.assertEqual(status, 409, body)
        self.assertIn(b"Panic", body)
        self.assertGreaterEqual(self.helper.normal_access_calls, 2, "Panic must undo a late apply")
        self.assertFalse(self.scheduler.is_active)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "reset")

    def test_extra_connections_are_refused_cleanly_and_recover(self):
        with patch.object(api, "MAX_DASHBOARD_WORKERS", 2):
            first, first_thread = self.start_slow_apply()
            second = {}

            def second_apply():
                second["response"] = self.request("POST", "/api/apply", {})

            second_thread = threading.Thread(target=second_apply, daemon=True)
            self.threads.append(second_thread)
            second_thread.start()
            deadline = time.monotonic() + 5
            while self.server.active_workers < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.server.active_workers, 2)

            status, headers, data = self.request("GET", "/api/health")
            self.assertEqual(status, 503, data)
            self.assertEqual(headers.get("Retry-After"), "1")

            self.helper.release_apply.set()
            first_thread.join(5)
            second_thread.join(5)
            deadline = time.monotonic() + 5
            while self.server.active_workers and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.request("GET", "/api/health")[0], 200)

    def test_parallel_reads_and_writes_share_the_repository_safely(self):
        failures = []

        def read():
            for _ in range(15):
                status = self.request("GET", "/api/profiles")[0]
                if status != 200:
                    failures.append(("read", status))

        def write(index):
            for step in range(10):
                body = {"id": "p%d-%d" % (index, step), "name": "P %d %d" % (index, step),
                        "upload": {}, "download": {"delay_ms": 10}}
                status = self.request("POST", "/api/profiles", body)[0]
                if status not in (200, 201):
                    failures.append(("write", status))

        workers = [threading.Thread(target=read) for _ in range(4)]
        workers += [threading.Thread(target=write, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(30)
        self.assertEqual(failures, [])
        self.assertEqual(len(self.repository.list_profiles()), 1 + 2 * 10)


if __name__ == "__main__":
    unittest.main()
