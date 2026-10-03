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

    def test_panic_that_lands_just_before_the_scheduler_is_activated_still_wins(self):
        self.helper.release_apply.set()
        original = self.scheduler.activate_plan

        def racing(**values):
            # Panic arrives after the apply's last check and before it starts the scheduler.
            self.scheduler.halt()
            return original(**values)

        with patch.object(self.scheduler, "activate_plan", side_effect=racing):
            status, _, body = self.request("POST", "/api/apply", {})

        self.assertEqual(status, 409, body)
        self.assertFalse(self.scheduler.is_active, "the late apply restarted the scheduler")
        self.assertGreaterEqual(self.helper.normal_access_calls, 1, "the late apply was not undone")
        self.assertEqual(self.repository.get_runtime_state().observed_state, "reset")

    def test_panic_still_gets_a_slot_when_every_worker_is_busy(self):
        with patch.object(api, "MAX_DASHBOARD_WORKERS", 1):
            result, thread = self.start_slow_apply()
            deadline = time.monotonic() + 5
            while self.server.active_workers < 1 and time.monotonic() < deadline:
                time.sleep(0.01)

            health, headers, _ = self.request("GET", "/api/health")
            panic, _, data = self.request("POST", "/api/normal-access", {})

            self.assertEqual(health, 503)
            self.assertEqual(headers.get("Retry-After"), "1")
            self.assertEqual(panic, 200, data)
            self.assertEqual(self.helper.normal_access_calls, 1)
            self.helper.release_apply.set()
            thread.join(5)

    def test_the_reserved_slot_serves_only_panic(self):
        with patch.object(api, "MAX_DASHBOARD_WORKERS", 1):
            result, thread = self.start_slow_apply()

            status, headers, _ = self.request("POST", "/api/profiles", {"id": "x", "name": "X"})

            self.assertEqual(status, 503)
            self.assertEqual(headers.get("Retry-After"), "1")
            self.helper.release_apply.set()
            thread.join(5)

    def test_a_refused_client_that_sent_one_byte_does_not_stall_the_accept_loop(self):
        import socket

        with patch.object(api, "MAX_DASHBOARD_WORKERS", 1), patch.object(
            api, "RESERVED_PANIC_WORKERS", 0
        ):
            result, thread = self.start_slow_apply()
            idle = []
            for _ in range(6):
                connection = socket.create_connection(self.server.server_address[:2])
                connection.sendall(b"G")
                idle.append(connection)
            try:
                started = time.monotonic()
                health = self.request("GET", "/api/health", timeout=FAST * 3)[0]
                elapsed = time.monotonic() - started

                self.assertEqual(health, 503)
                self.assertLess(elapsed, FAST, "refusing idle clients stalled the accept loop")
                self.assertNotIn("response", result, "the apply must still be running")
            finally:
                for connection in idle:
                    connection.close()
                self.helper.release_apply.set()
                thread.join(5)

    def _assert_refusal_needs_no_client_progress(self, *, full_buffer=False):
        import socket

        connection, peer = socket.socketpair()
        worker = None
        finished = threading.Event()
        errors = []
        try:
            if full_buffer:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                connection.setblocking(False)
                for _ in range(256):
                    try:
                        connection.send(b"x" * 4096)
                    except BlockingIOError:
                        break
                else:
                    self.fail("could not fill the bounded test send buffer")
            # A refused client has sent no unread bytes and need not read its reply.
            connection.settimeout(FAST * 3)

            def refuse():
                try:
                    self.server._refuse_request(connection)
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()

            worker = threading.Thread(target=refuse)
            worker.start()
            self.assertTrue(finished.wait(FAST), "refusal waited for client progress")
            self.assertEqual(errors, [])
            if not full_buffer:
                peer.settimeout(FAST)
                self.assertTrue(peer.recv(4096).startswith(b"HTTP/1.1 503 Service Unavailable"))
        finally:
            peer.close()
            connection.close()
            if worker is not None:
                worker.join(FAST * 3)
                self.assertFalse(worker.is_alive(), "refusal worker did not stop")

    def test_refusal_does_not_wait_for_another_client_byte(self):
        self._assert_refusal_needs_no_client_progress()

    def test_refusal_does_not_wait_for_a_client_to_read_its_reply(self):
        self._assert_refusal_needs_no_client_progress(full_buffer=True)

    def test_mixed_requests_with_apply_and_panic_finish_without_errors_or_deadlock(self):
        self.helper.release_apply.set()
        bad = []
        paths = ("/api/health", "/api/profiles", "/api/conditions", "/api/connected-devices",
                 "/api/general-profile", "/api/devices", "/api/plan")

        def reader(offset):
            for step in range(12):
                path = paths[(offset + step) % len(paths)]
                status = self.request("GET", path)[0]
                if status not in (200, 503):
                    bad.append((path, status))

        def applier():
            for _ in range(6):
                status = self.request("POST", "/api/apply", {})[0]
                if status not in (200, 409, 503):
                    bad.append(("apply", status))

        def panicker():
            for _ in range(6):
                status = self.request("POST", "/api/normal-access", {})[0]
                if status not in (200, 503):
                    bad.append(("panic", status))

        def writer(index):
            for step in range(6):
                body = {"id": "m%d-%d" % (index, step), "name": "M %d %d" % (index, step),
                        "upload": {}, "download": {"delay_ms": 10}}
                status = self.request("POST", "/api/profiles", body)[0]
                if status not in (200, 201, 503):
                    bad.append(("write", status))

        workers = [threading.Thread(target=reader, args=(index,)) for index in range(4)]
        workers += [threading.Thread(target=applier), threading.Thread(target=panicker)]
        workers += [threading.Thread(target=writer, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(60)
        self.assertEqual([worker.name for worker in workers if worker.is_alive()], [])
        self.assertEqual(bad, [])
        self.assertEqual(self.request("GET", "/api/health")[0], 200)


if __name__ == "__main__":
    unittest.main()
