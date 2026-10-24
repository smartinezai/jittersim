"""Pending choices remain inert until explicit fresh, reviewed profile use."""

import json
import http.client
import threading
from dataclasses import replace
from unittest.mock import patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.pending_devices import PendingDeviceChoice
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.helper import HelperClientError
from tests.unit.test_api import PlanRecordingHelper

MAC = "dc:1e:d5:3e:97:4c"
APPLY = "/api/connected-devices/apply-profile"
PENDING = "/api/pending-devices"


@pytest.fixture
def api(tmp_path):
    repository = ConfigRepository(tmp_path / "pending-api.db")
    repository.save_profile(Profile("slow", "Slow", upload=Impairment(delay_ms=50)))
    helper = PlanRecordingHelper()
    server = ControlServer(("127.0.0.1", 0), repository, helper_client=helper,
                           admin_token="test-token")
    try:
        yield server, repository, helper
    finally:
        server.server_close()
        repository.close()


def write(api, path, payload):
    status, _, body = api[0].write_response_for(path, payload)
    return status, json.loads(body)


def choice(api):
    return api[1].save_pending_device_choice(PendingDeviceChoice(MAC, "profile", "slow"))


def request(api, pending=None):
    pending = pending or choice(api)
    return {"pending_mac": MAC, "expected_pending_revision": pending.revision,
            "expected_configuration_version": api[1].get_runtime_state().configuration_version}


def test_existing_apply_route_rejects_mixed_pending_and_legacy_identity(api):
    payload = request(api)
    payload.update(mac_address=MAC, ipv4_address="192.168.50.178", profile_id="slow")
    before = tuple(api[1]._connection.iterdump())

    status, result = write(api, APPLY, payload)

    assert status == 409, result
    assert tuple(api[1]._connection.iterdump()) == before
    assert api[2].plans == []


def active_state(api):
    repository = api[1]
    return (repository.list_devices(), repository.list_unaffected_devices(),
            repository.get_runtime_state(), repository.list_device_groups(),
            repository.get_device_start_scope(), repository.get_everyone_exceptions(),
            list(api[2].plans), api[2].access_plan)


def test_crud_and_consistent_review_are_inert_and_use_server_revision(api):
    before = active_state(api)
    with patch.object(api[2], "connected_device_snapshot", side_effect=AssertionError("helper I/O")):
        status, saved = write(api, PENDING, {"mac_address": MAC.upper(), "action": "profile",
                                           "profile_id": "slow", "display_name": "Prepared"})
        assert status == 200
        row = saved["pending_device"]
        assert row["mac_address"] == MAC and row["saved_at"] > 0 and row["revision"] > 0
        status, _, body = api[0].response_for(PENDING + "/review", "mac=" + MAC)
        review = json.loads(body)
        assert status == 200 and review["profile"]["upload"]["delay_ms"] == 50
        assert review["configuration_version"] == before[2].configuration_version
        assert review["pending_device"] == row
        original_get_profile = api[1].get_profile
        def transactional_profile(profile_id):
            assert api[1]._connection.in_transaction
            return original_get_profile(profile_id)
        with patch.object(api[1], "get_profile", side_effect=transactional_profile):
            assert api[0].response_for(PENDING + "/review", "mac=" + MAC)[0] == 200
        assert json.loads(api[0].response_for(PENDING)[2])["pending_devices"] == [row]
        status, edited = write(api, PENDING, {"mac_address": MAC, "action": "protect",
                                             "expected_revision": row["revision"]})
        assert status == 200
        assert edited["pending_device"]["revision"] != row["revision"]
        assert write(api, PENDING + "/delete", {"mac_address": MAC,
                     "expected_revision": row["revision"]})[0] == 409
        assert write(api, PENDING + "/delete", {"mac_address": MAC,
                     "expected_revision": edited["pending_device"]["revision"]})[0] == 200
    assert active_state(api) == before


@pytest.mark.parametrize("extra", [{"saved_at": 1}, {"revision": 8},
    {"ipv4_address": "192.168.50.178"}, {"association_verified": True},
    {"expected_revision": True}, {"observed_at": True}])
def test_crud_rejects_forged_fields_and_boolean_revisions(api, extra):
    before = tuple(api[1]._connection.iterdump())
    assert write(api, PENDING, {"mac_address": MAC, "action": "profile",
                               "profile_id": "slow", **extra})[0] == 409
    assert tuple(api[1]._connection.iterdump()) == before


@pytest.mark.parametrize("extra", [{"hostname": "forged"}, {"profile_id": "slow"},
    {"ipv4_address": "192.168.50.178"}, {"association_verified": True},
    {"expected_pending_revision": True}, {"expected_configuration_version": True}])
def test_use_rejects_forged_fields_and_boolean_versions(api, extra):
    payload = request(api)
    before = active_state(api)
    assert write(api, APPLY, {**payload, **extra})[0] == 409
    assert active_state(api) == before


@pytest.mark.parametrize("damage", ["stale", "unknown", "truthy_available", "truthy_verified",
    "unreachable", "missing_neighbor", "wrong_spelling", "duplicate", "invalid_duplicate",
    "two_ips", "other_owner", "private_mac", "invalid_ip"])
def test_use_refuses_untrusted_or_ambiguous_helper_identity(api, damage):
    payload = request(api)
    snapshot = api[2].connected_device_snapshot()
    row = snapshot["devices"][0]
    if damage == "stale": snapshot["stale"] = True
    if damage == "unknown": snapshot["association_status_available"] = False
    if damage == "truthy_available": snapshot["association_status_available"] = 1
    if damage == "truthy_verified": row["association_verified"] = 1
    if damage == "unreachable": row["neighbor_state"] = "stale"
    if damage in ("missing_neighbor", "wrong_spelling"):
        row.pop("neighbor_state")
        if damage == "wrong_spelling": row["neighbour_state"] = "reachable"
    if damage == "duplicate": snapshot["devices"].append(dict(row))
    if damage == "invalid_duplicate": snapshot["devices"].append({"mac_address": MAC})
    if damage == "two_ips": snapshot["devices"].append({**row, "ipv4_address": "192.168.50.179"})
    if damage == "other_owner": snapshot["devices"].append({**row, "mac_address": "02:00:00:00:00:01"})
    if damage == "private_mac": row["mac_address"] = "02:00:00:00:00:01"
    if damage == "invalid_ip": row["ipv4_address"] = "invalid"
    before = active_state(api)
    with patch.object(api[2], "connected_device_snapshot", return_value=snapshot):
        assert write(api, APPLY, payload)[0] == 409
    assert active_state(api) == before
    assert api[1].get_pending_device_choice(MAC) is not None


@pytest.mark.parametrize("context", ["saved", "reset", "everyone"])
@pytest.mark.parametrize("fail", [False, True])
def test_profile_use_retains_pending_and_existing_success_or_failure_contract(api, context, fail, monkeypatch):
    if context == "reset": assert write(api, "/api/reset", {})[0] == 200
    if context == "everyone":
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=api[2])
        api[0].scheduler = scheduler
        monkeypatch.setattr(scheduler, "start", lambda: None)
        api[2].check_general_profile_support = lambda: None
        assert write(api, "/api/general-profile", {"profile_id": "slow"})[0] == 200
    pending = choice(api)
    api[2].fail_action = "apply" if fail else ""
    status, result = write(api, APPLY, request(api, pending))
    assert status == (502 if fail else 200), result
    assert result["live_applied"] is (not fail)
    saved = api[1].get_device_by_mac_address(MAC)
    if fail and context == "everyone":
        assert saved is None and not api[1].get_everyone_exceptions()
        assert "could not be put back" in result["error"]
    else:
        assert saved.profile_id == "slow"
    if fail and context == "reset":
        assert api[1].get_device_start_scope() is None
        assert api[1].get_runtime_state().observed_state == "reset"
    assert api[1].get_pending_device_choice(MAC) == pending


@pytest.mark.parametrize("conflict", ["protected", "saved_ip", "generated_id", "protect_action",
                                      "version", "pending_revision", "aba", "profile_edit"])
def test_pending_use_refuses_conflicts_without_active_change(api, conflict):
    pending = choice(api)
    payload = request(api, pending)
    repository = api[1]
    if conflict == "protected": repository.save_unaffected_device(UnaffectedDevice(MAC, "Protected"))
    if conflict == "saved_ip":
        repository.save_device(Device("other", "Other", "192.168.50.178", "slow",
                                      mac_address="02:00:00:00:00:01"))
    if conflict == "generated_id":
        repository.save_device(Device("client-" + MAC.replace(":", ""), "Other", "192.168.50.179", "slow"))
    if conflict in ("protect_action", "pending_revision"):
        repository.save_pending_device_choice(replace(pending,
            action="protect" if conflict == "protect_action" else "profile",
            profile_id=None if conflict == "protect_action" else "slow"), expected_revision=pending.revision)
    if conflict == "aba":
        repository.delete_pending_device_choice(MAC, expected_revision=pending.revision)
        choice(api)
    if conflict == "version": repository.save_profile(Profile("other", "Other"))
    if conflict == "profile_edit": repository.save_profile(Profile("slow", "Edited"))
    # Protection and saved identity conflicts must be checked independently of stale review.
    if conflict in ("protected", "saved_ip", "generated_id"):
        payload["expected_configuration_version"] = repository.get_runtime_state().configuration_version
    before = active_state(api)
    assert write(api, APPLY, payload)[0] == 409
    assert active_state(api) == before


def test_prepared_protection_is_reviewable_but_cannot_be_used(api):
    status, saved = write(api, PENDING, {"mac_address": MAC, "action": "protect"})
    assert status == 200
    pending = api[1].get_pending_device_choice(MAC)
    review = json.loads(api[0].response_for(PENDING + "/review", "mac=" + MAC)[2])
    assert review["profile"] is None
    before = active_state(api)
    status, result = write(api, APPLY, request(api, pending))
    assert status == 409 and "protection use is not available" in result["error"]
    assert active_state(api) == before
    assert api[1].get_pending_device_choice(MAC).revision == saved["pending_device"]["revision"]


def test_pending_api_capacity_and_create_edit_compare_and_swap(api):
    row = choice(api)
    assert write(api, PENDING, {"mac_address": MAC, "action": "protect"})[0] == 409
    assert write(api, PENDING, {"mac_address": MAC, "action": "protect",
                               "expected_revision": row.revision + 1})[0] == 409
    for index in range(255):
        api[1].save_pending_device_choice(PendingDeviceChoice(
            f"02:00:00:00:{index // 256:02x}:{index % 256:02x}", "protect"))
    before = active_state(api)
    assert write(api, PENDING, {"mac_address": "02:00:00:01:00:00", "action": "protect"})[0] == 409
    assert len(json.loads(api[0].response_for(PENDING)[2])["pending_devices"]) == 256
    assert active_state(api) == before


def test_missing_helper_and_scan_failure_without_cache_leave_pending_inert(api):
    payload = request(api)
    before = active_state(api)
    with patch.object(api[0], "helper_client", None):
        assert write(api, APPLY, payload)[0] == 503
    with patch.object(api[2], "connected_device_snapshot", side_effect=HelperClientError("busy")):
        assert write(api, APPLY, payload)[0] == 409
    assert active_state(api) == before


def test_use_reads_one_snapshot_and_never_calls_helper_inside_database_transaction(api):
    payload = request(api)
    original_scan, original_apply = api[2].connected_device_snapshot, api[2].apply
    def scan():
        assert not api[1]._connection.in_transaction
        return original_scan()
    def apply_plan(**kwargs):
        assert not api[1]._connection.in_transaction
        return original_apply(**kwargs)
    with patch.object(api[2], "connected_device_snapshot", side_effect=scan) as scans, \
            patch.object(api[2], "apply", side_effect=apply_plan):
        assert write(api, APPLY, payload)[0] == 200
        assert scans.call_count == 1


def test_failed_precommit_validation_rolls_back_assignment_and_scope(api):
    assert write(api, "/api/reset", {})[0] == 200
    payload = request(api)
    before = tuple(api[1]._connection.iterdump())
    with patch.object(api[0], "_validate_saved_plan", side_effect=ValueError("budget exhausted")):
        assert write(api, APPLY, payload)[0] == 409
    assert tuple(api[1]._connection.iterdump()) == before
    assert api[2].plans == []


def test_pending_list_review_and_scan_do_not_retry_failed_use(api):
    pending = choice(api)
    api[2].fail_action = "apply"
    assert write(api, APPLY, request(api, pending))[0] == 502
    before = active_state(api)
    assert api[0].response_for(PENDING)[0] == 200
    assert api[0].response_for(PENDING + "/review", "mac=" + MAC)[0] == 200
    api[2].connected_device_snapshot()
    assert active_state(api) == before


def test_panic_after_commit_does_not_restore_failed_pending_activation(api):
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=api[2])
    api[0].scheduler = scheduler
    api[2].check_general_profile_support = lambda: None
    with patch.object(scheduler, "start"):
        assert write(api, "/api/general-profile", {"profile_id": "slow"})[0] == 200
        pending = choice(api)
        attempts = []
        def panic_during_apply(**kwargs):
            attempts.append(kwargs)
            assert api[1].get_device_by_mac_address(MAC) is not None
            scheduler.halt()
            raise HelperClientError("Panic stopped this Apply", reason="control-changed")
        with patch.object(api[2], "apply", side_effect=panic_during_apply):
            status, result = write(api, APPLY, request(api, pending))
        assert status == 409 and result["live_applied"] is False
        assert len(attempts) == 1
        assert api[1].get_pending_device_choice(MAC) == pending


@pytest.mark.parametrize("change", ["pending", "profile"])
def test_cross_connection_change_after_snapshot_is_refused_in_save_transaction(api, change):
    payload = request(api)
    other = ConfigRepository(api[1]._connection.execute("PRAGMA database_list").fetchone()[2])
    original = api[0]._publish_apply
    def changed_before_publish(generation, save):
        if change == "pending":
            row = other.get_pending_device_choice(MAC)
            other.save_pending_device_choice(replace(row, display_name="Changed"), expected_revision=row.revision)
        else:
            other.save_profile(Profile("slow", "Changed"))
        return original(generation, save)
    before_devices = api[1].list_devices()
    try:
        with patch.object(api[0], "_publish_apply", side_effect=changed_before_publish):
            assert write(api, APPLY, payload)[0] == 409
        assert api[1].list_devices() == before_devices and api[2].plans == []
        assert api[1].get_pending_device_choice(MAC) is not None
    finally:
        other.close()


def test_real_http_routes_require_token_and_device_admission(api):
    choice(api)
    server = api[0]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    paths = [("GET", PENDING), ("GET", PENDING + "/review?mac=" + MAC),
             ("POST", PENDING), ("POST", PENDING + "/delete"), ("POST", APPLY)]
    before = tuple(api[1]._connection.iterdump())
    try:
        for method, path in paths:
            for token, expected in [(None, 401), ("wrong", 401), ("test-token", 403)]:
                connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                headers = {"Content-Type": "application/json"}
                if token: headers["Authorization"] = "Bearer " + token
                with patch.object(server, "dashboard_access", return_value={"allowed": False}) as admission:
                    connection.request(method, path, body="{}" if method == "POST" else None, headers=headers)
                    response = connection.getresponse()
                    assert response.status == expected, response.read()
                    response.read()
                    assert admission.call_count == (1 if token == "test-token" else 0)
                connection.close()
        assert tuple(api[1]._connection.iterdump()) == before
    finally:
        server.shutdown()
        thread.join(3)


@pytest.mark.parametrize("safety", ["reset", "panic"])
def test_pending_apply_waiting_for_control_cannot_restart_after_safety(api, safety):
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=api[2])
    api[0].scheduler = scheduler
    payload = request(api)
    captured = threading.Event()
    original = api[0]._capture_control_intent
    result = []
    def capture(generation):
        response = original(generation)
        captured.set()
        return response
    with patch.object(scheduler, "start"), patch.object(api[0], "_capture_control_intent", side_effect=capture):
        with scheduler.exclusive():
            worker = threading.Thread(target=lambda: result.append(write(api, APPLY, payload)))
            worker.start()
            assert captured.wait(3)
            assert write(api, "/api/reset" if safety == "reset" else "/api/normal-access", {})[0] == 200
        worker.join(3)
    assert not worker.is_alive() and result[0][0] == 409
    assert not api[1].list_devices() and api[2].plans == []
    assert api[1].get_pending_device_choice(MAC) is not None


def test_reset_arriving_during_helper_snapshot_refuses_pending_save(api):
    scheduler = RuntimeScheduler(database_path=":memory:", helper_client=api[2])
    api[0].scheduler = scheduler
    payload = request(api)
    scanning, release, resetting = threading.Event(), threading.Event(), threading.Event()
    original_scan, original_note = api[2].connected_device_snapshot, api[0].note_reset_request
    results = {}
    def scan():
        scanning.set()
        assert release.wait(3)
        return original_scan()
    def note():
        original_note()
        resetting.set()
    with patch.object(scheduler, "start"), patch.object(api[2], "connected_device_snapshot", side_effect=scan), \
            patch.object(api[0], "note_reset_request", side_effect=note):
        applying = threading.Thread(target=lambda: results.setdefault("apply", write(api, APPLY, payload)))
        reset = threading.Thread(target=lambda: results.setdefault("reset", write(api, "/api/reset", {})))
        applying.start()
        try:
            assert scanning.wait(3)
            reset.start()
            assert resetting.wait(3)
        finally:
            release.set()
        applying.join(3)
        reset.join(3)
    assert not applying.is_alive() and not reset.is_alive()
    assert results["apply"][0] == 409 and results["reset"][0] == 200
    assert not api[1].list_devices() and api[2].plans == []
    assert api[1].get_pending_device_choice(MAC) is not None
