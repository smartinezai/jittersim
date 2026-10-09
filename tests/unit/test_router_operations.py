"""Protect router result ownership and bounded background work."""

import json
import threading
import time
from types import SimpleNamespace

from jittersim.api.router_operations import RouterOperations
from jittersim.api.wifi_operations import issue_request_id


def response(value, status=200):
    return status, "application/json", json.dumps(value).encode()


def finish(operations, password="correct"):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        state = operations.status(password)
        if not state["busy"]:
            return state
        time.sleep(0.005)
    raise AssertionError("router operation did not finish")


def test_slow_write_is_visible_without_blocking_other_tabs_or_repeating_it():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def execute(path, payload):
        calls.append(path)
        entered.set()
        assert release.wait(2)
        return response({"result": {"enabled": False}})

    operations = RouterOperations(execute, "fixture")
    try:
        status, started = operations.start("/api/router/wifi", {"admin_password": "correct"})
        assert status == 202 and entered.wait(1)
        state = operations.status("other password")
        assert state["changing"] and state["busy"] and state["enabled"] is None
        assert operations.start("/api/router/wifi", {"admin_password": "correct"})[0] == 409
        assert operations.result(started["operation_id"], "other password")[0] == 410
        release.set()
        assert finish(operations)["enabled"] is False
        assert operations.result(started["operation_id"], "correct")[0] == 200
        assert operations.completed_action() == ("/api/router/wifi", 200)
        assert operations.completed_action() is None
        assert calls == ["/api/router/wifi"]
    finally:
        release.set()
        operations.close()


def test_status_is_shared_without_accepting_a_different_password():
    calls = []

    def execute(path, payload):
        calls.append(payload["admin_password"])
        return response({"result": {"enabled": True}})

    operations = RouterOperations(execute, "fixture")
    try:
        operations.status("correct", refresh=True)
        assert finish(operations)["enabled"] is True
        for _ in range(50):
            assert operations.status("correct")["enabled"] is True
            assert operations.status("wrong")["enabled"] is None
        assert calls == ["correct"]
        assert operations.completed_action() is None
    finally:
        operations.close()


def test_background_reads_do_not_discard_the_write_result(monkeypatch):
    operations = RouterOperations(lambda path, payload: response({"result": {"enabled": True}}), "fixture")
    try:
        _, started = operations.start("/api/router/wifi", {"admin_password": "correct"})
        finish(operations)
        monkeypatch.setattr(operations, "_next_read", 0)
        operations.status("correct", refresh=True)
        finish(operations)
        assert operations.result(started["operation_id"], "correct")[0] == 200
    finally:
        operations.close()


def test_failed_change_invalidates_prior_state_and_cannot_look_successful():
    def execute(path, payload):
        if path == "/api/router/wifi":
            return response({"error": "The router may have changed Wi-Fi."}, 502)
        return response({"result": {"enabled": True}})

    operations = RouterOperations(execute, "fixture")
    try:
        operations.status("correct", refresh=True)
        assert finish(operations)["enabled"] is True
        _, started = operations.start("/api/router/wifi", {"admin_password": "correct"})
        assert finish(operations)["enabled"] is None
        status, result = operations.result(started["operation_id"], "correct")
        assert status == 502 and "may have changed" in result["error"]
    finally:
        operations.close()


def test_closed_coordinator_does_not_retain_late_state():
    release = threading.Event()

    def execute(path, payload):
        assert release.wait(2)
        return response({"result": {"enabled": True}})

    operations = RouterOperations(execute, "fixture")
    operations.status("correct", refresh=True)
    operations.close()
    release.set()
    assert finish(operations)["enabled"] is None


def test_malformed_result_releases_operation_and_hides_exception_details():
    operations = RouterOperations(lambda path, payload: response({"result": []}), "fixture")
    try:
        _, started = operations.start("/api/router/wifi", {"admin_password": "private-password"})
        finish(operations, "private-password")
        status, result = operations.result(started["operation_id"], "private-password")
        assert status == 502
        assert "private-password" not in str(result)
        assert "Check its state" in result["error"]
    finally:
        operations.close()


def test_ordinary_status_reads_never_contact_the_router(monkeypatch):
    calls = []
    now = [1000.0]
    monkeypatch.setattr("jittersim.api.router_operations.time",
                        SimpleNamespace(monotonic=lambda: now[0], time=time.time))

    def execute(path, payload):
        calls.append(path)
        return response({"result": {"enabled": True}})

    operations = RouterOperations(execute, "fixture")
    try:
        for _ in range(100):
            assert operations.status("correct")["enabled"] is None
            now[0] += 60
        assert calls == []
        operations.status("correct", refresh=True)
        assert finish(operations)["enabled"] is True
        now[0] += operations.STATE_LIFETIME + 1
        for _ in range(100):
            assert operations.status("correct")["enabled"] is None
            now[0] += 60
        assert calls == ["/api/router/status"]
    finally:
        operations.close()


def test_explicit_refreshes_share_limits_and_failed_reads_do_not_retry(monkeypatch):
    calls = []
    now = [1000.0]
    monkeypatch.setattr("jittersim.api.router_operations.time",
                        SimpleNamespace(monotonic=lambda: now[0], time=time.time))

    def execute(path, payload):
        calls.append(path)
        return response({"error": "Router status is unavailable."}, 502)

    operations = RouterOperations(execute, "fixture")
    try:
        operations.status("correct", refresh=True)
        assert finish(operations)["enabled"] is None
        for _ in range(100):
            operations.status("correct", refresh=True)
        assert calls == ["/api/router/status"]
        now[0] += 600
        assert operations.status("correct")["enabled"] is None
        assert calls == ["/api/router/status"]
        operations.status("correct", refresh=True)
        assert finish(operations)["enabled"] is None
        assert len(calls) == 2
    finally:
        operations.close()


CHECK = "/api/router/check"


def fake_clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("jittersim.api.router_operations.time",
                        SimpleNamespace(monotonic=lambda: now[0], time=time.time))
    return now


def check(operations, password="correct", **extra):
    status, body = operations.start(CHECK, {"admin_password": password, **extra})
    if status == 202:
        finish(operations, password)
    return status, body


def test_second_password_check_is_refused_without_contacting_the_router(monkeypatch):
    calls = []
    now = fake_clock(monkeypatch)

    def execute(path, payload):
        calls.append(payload["admin_password"])
        return response({"result": {"ok": True}})

    operations = RouterOperations(execute, "fixture")
    try:
        assert check(operations)[0] == 202
        now[0] += 5
        for password in ("correct", "another password", "correct"):
            status, body = operations.start(CHECK, {"admin_password": password, "request_id": issue_request_id()})
            assert status == 429
            assert body["retry_after"] == 10 and "10 seconds" in body["error"]
            assert password not in json.dumps(body)
        assert calls == ["correct"]
        # The wait runs out after the success interval and then lets exactly one more check through.
        now[0] += 10
        assert check(operations)[0] == 202
        assert operations.start(CHECK, {"admin_password": "correct"})[0] == 429
        assert calls == ["correct", "correct"]
    finally:
        operations.close()


def test_failed_password_check_waits_thirty_seconds_for_every_caller(monkeypatch):
    calls = []
    now = fake_clock(monkeypatch)

    def execute(path, payload):
        calls.append(payload["admin_password"])
        return response({"error": "Router login failed."}, 502)

    operations = RouterOperations(execute, "fixture")
    try:
        status, started = check(operations, request_id=issue_request_id())
        assert status == 202
        assert operations.result(started["operation_id"], "correct")[0] == 502
        now[0] += 29
        status, body = operations.start(CHECK, {"admin_password": "a different password"})
        assert status == 429 and body["retry_after"] == 1
        now[0] += 1.5
        assert check(operations, "a different password")[0] == 202
        assert calls == ["correct", "a different password"]
    finally:
        operations.close()


def test_replaying_a_check_request_returns_its_stored_result_and_does_not_count(monkeypatch):
    calls = []
    now = fake_clock(monkeypatch)

    def execute(path, payload):
        calls.append(path)
        return response({"result": {"ok": True}})

    operations = RouterOperations(execute, "fixture")
    try:
        identifier = issue_request_id()
        status, started = check(operations, request_id=identifier)
        assert status == 202
        now[0] += 2
        for _ in range(3):
            status, body = operations.start(CHECK, {"admin_password": "correct", "request_id": identifier})
            assert status == 200 and body["result"] == {"ok": True}
            assert body["operation_id"] == identifier
        assert calls == [CHECK]
        # A replay under another password or path is still refused as before.
        assert operations.start(CHECK, {"admin_password": "other", "request_id": identifier})[0] == 409
        # The replays did not extend the wait.
        now[0] += 13
        assert check(operations)[0] == 202
    finally:
        operations.close()


def test_password_check_shares_the_status_read_budget_in_both_directions(monkeypatch):
    calls = []
    now = fake_clock(monkeypatch)

    def execute(path, payload):
        calls.append(path)
        return response({"result": {"enabled": True, "ok": True}})

    operations = RouterOperations(execute, "fixture")
    try:
        assert operations.status("correct")["refresh_in"] == 0
        operations.status("correct", refresh=True)
        finish(operations)
        now[0] += 3
        # A view tells the page how long the router is off limits, so a refused read can say so.
        assert operations.status("correct", refresh=True)["refresh_in"] == 12
        assert operations.start(CHECK, {"admin_password": "correct"})[0] == 429
        now[0] += 13
        assert check(operations)[0] == 202
        operations.status("correct", refresh=True)
        finish(operations)
        assert calls == ["/api/router/status", CHECK]
        now[0] += 16
        operations.status("correct", refresh=True)
        finish(operations)
        assert calls == ["/api/router/status", CHECK, "/api/router/status"]
    finally:
        operations.close()


def test_other_router_operations_are_not_held_back_by_the_check_wait(monkeypatch):
    calls = []
    fake_clock(monkeypatch)

    def execute(path, payload):
        calls.append(path)
        return response({"error": "Router login failed."}, 502)

    operations = RouterOperations(execute, "fixture")
    try:
        assert check(operations)[0] == 202
        assert operations.start("/api/router/preview-ap", {"admin_password": "correct"})[0] == 202
        finish(operations)
        assert calls == [CHECK, "/api/router/preview-ap"]
    finally:
        operations.close()


def test_a_check_started_while_another_router_operation_runs_is_a_conflict_not_a_wait():
    release = threading.Event()

    def execute(path, payload):
        assert release.wait(2)
        return response({"result": {"enabled": True}})

    operations = RouterOperations(execute, "fixture")
    try:
        assert operations.start("/api/router/wifi", {"admin_password": "correct"})[0] == 202
        status, body = operations.start(CHECK, {"admin_password": "correct"})
        assert status == 409 and "retry_after" not in body
    finally:
        release.set()
        operations.close()


def test_a_check_on_the_callers_thread_spends_the_same_budget(monkeypatch):
    calls = []
    now = fake_clock(monkeypatch)

    def execute(path, payload):
        calls.append(path)
        return response({"result": {"ok": True}})

    operations = RouterOperations(execute, "fixture")
    try:
        assert operations.begin_check() is None
        # Nobody else may log in to the router while this check is out.
        assert operations.begin_check()[0] == 409
        assert operations.start(CHECK, {"admin_password": "correct"})[0] == 409
        assert operations.start("/api/router/wifi", {"admin_password": "correct"})[0] == 409
        assert operations.status("correct", refresh=True)["busy"] is True
        operations.end_check(False)
        status, body = operations.begin_check()
        assert status == 429 and body["retry_after"] == 30
        now[0] += 31
        assert operations.begin_check() is None
        operations.end_check(True)
        now[0] += 14
        assert operations.begin_check()[0] == 429
        assert operations.start(CHECK, {"admin_password": "correct"})[0] == 429
        now[0] += 2
        assert operations.begin_check() is None
        operations.end_check(True)
        assert calls == []
    finally:
        operations.close()


def test_a_check_that_raises_on_the_callers_thread_counts_as_failed(monkeypatch):
    now = fake_clock(monkeypatch)
    operations = RouterOperations(lambda path, payload: response({}), "fixture")
    try:
        assert operations.begin_check() is None
        operations.end_check(False)
        now[0] += 10
        assert operations.begin_check()[0] == 429
    finally:
        operations.close()
