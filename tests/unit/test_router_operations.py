"""Protect router result ownership and bounded background work."""

import json
import threading
import time

from jittersim.api.router_operations import RouterOperations


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
        operations.status("correct")
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
    operations.status("correct")
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
