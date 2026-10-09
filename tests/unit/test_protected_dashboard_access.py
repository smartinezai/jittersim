"""Protected devices may open the dashboard: a separate, derived grant that never widens access."""

import json
import logging
import threading
import unittest

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.storage import ConfigRepository
from jittersim.networking.dashboard_access import (
    MAX_ACCESS_DEVICES,
    MAX_PROTECTED_ACCESS_DEVICES,
    AccessError,
    AccessStore,
)
from jittersim.networking.helper import HelperClient, HelperClientError, HelperService
from tests.unit.test_api import PlanRecordingHelper
from tests.unit.test_dashboard_access import MAC, PEER, PeerApplier

OTHER = "02:00:00:00:00:02"
OLD = "02:00:00:00:00:99"


def mac(index):
    return "02:00:00:%02x:%02x:%02x" % (index >> 16 & 0xFF, index >> 8 & 0xFF, index & 0xFF)


def protected(*macs):
    return [{"mac_address": value, "name": "Phone"} for value in macs]


def status(service, operation="status", **values):
    return service.handle_payload(
        {"action": "dashboard-access", "operation": operation, "peer": PEER, **values}
    )


def sync(service, devices):
    return service.handle_payload(
        {"action": "dashboard-access", "operation": "protected-sync", "devices": devices}
    )


# ---- the store: separate reasons and their union ---------------------------


def test_protection_grants_access_without_touching_the_saved_file(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OTHER, "Mac")
    before = store.path.read_bytes()
    result = store.sync_protected(protected(MAC))
    assert result == {"protected_synced": True, "protected_count": 1}
    assert store.path.read_bytes() == before
    devices = {item["mac_address"]: item for item in store.list_devices()}
    assert "protected" not in devices[OTHER] and "manual" not in devices[OTHER]
    assert devices[MAC]["protected"] is True and devices[MAC]["manual"] is False
    assert devices[MAC]["name"] == "Phone"


def test_a_device_with_both_reasons_is_one_entry_with_both_flags(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(MAC, "Mac")
    store.sync_protected(protected(MAC))
    (entry,) = store.list_devices()
    assert entry["protected"] is True and "manual" not in entry and entry["name"] == "Mac"


def test_removing_a_saved_grant_keeps_protection_access_and_the_reverse(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(MAC, "Mac")
    store.sync_protected(protected(MAC))
    (entry,) = store.remove(MAC)
    assert entry["manual"] is False and entry["protected"] is True
    store.add(MAC, "Mac")
    store.sync_protected([])
    (entry,) = store.list_devices()
    assert "protected" not in entry
    # Only the saved grant is on disk.
    assert [item["mac_address"] for item in json.loads(store.path.read_text())["devices"]] == [MAC]


def test_protection_is_not_trusted_after_a_restart_until_synced_again(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    assert store.protected_synced is False
    store.sync_protected(protected(MAC))
    restarted = AccessStore(store.path)
    assert restarted.protected_synced is False
    assert restarted.list_devices() == []


def test_the_full_protected_limit_and_saved_limit_fit_together(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    for index in range(MAX_ACCESS_DEVICES):
        store.add(mac(index + 1), "Saved %d" % index)
    targets = protected(*(mac(0x10000 + index) for index in range(MAX_PROTECTED_ACCESS_DEVICES)))
    assert store.sync_protected(targets)["protected_count"] == 256
    devices = store.list_devices()
    assert len(devices) == 64 + 256
    assert all("replaced_by" not in item for item in devices)


def test_one_more_than_the_protected_limit_is_refused_and_keeps_the_previous_set(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.sync_protected(protected(MAC))
    too_many = protected(*(mac(0x10000 + index) for index in range(MAX_PROTECTED_ACCESS_DEVICES + 1)))
    with pytest.raises(AccessError, match="256"):
        store.sync_protected(too_many)
    assert [item["mac_address"] for item in store.list_devices()] == [MAC]


@pytest.mark.parametrize(
    "devices",
    [
        None,
        {"mac_address": MAC},
        [{"mac_address": MAC}, {"mac_address": MAC}],
        [{"mac_address": "01:00:5e:00:00:01"}],
        [{"mac_address": MAC, "name": "x" * 65}],
        [{"mac_address": MAC, "token": "secret"}],
        ["02:00:00:00:00:01"],
    ],
)
def test_invalid_protected_sets_are_refused_whole(tmp_path, devices):
    store = AccessStore(tmp_path / "access.json")
    store.sync_protected(protected(OTHER))
    with pytest.raises(AccessError):
        store.sync_protected(devices)
    assert [item["mac_address"] for item in store.list_devices()] == [OTHER]


def test_repeating_a_sync_is_idempotent_and_keeps_the_first_time(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.sync_protected(protected(MAC, OTHER))
    first = store.list_devices()
    store.sync_protected(protected(MAC, OTHER))
    assert store.list_devices() == first


def test_a_replaced_saved_entry_does_not_hide_a_protection_grant(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(MAC, "Mac")
    store.move(MAC, OTHER)
    store.sync_protected(protected(MAC))
    entries = [item for item in store.list_devices() if item["mac_address"] == MAC]
    assert len(entries) == 2
    assert any("replaced_by" in item for item in entries)
    assert any(item.get("protected") and "replaced_by" not in item for item in entries)
    # Undo of the saved move still works and is not confused by the derived grant.
    store.undo_move(MAC)
    assert [item["mac_address"] for item in store.list_devices()] == [MAC]


def test_a_saved_move_needs_a_saved_grant_not_only_protection(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.sync_protected(protected(OLD))
    with pytest.raises(AccessError):
        store.move(OLD, MAC)
    assert not store.path.exists() or json.loads(store.path.read_text())["devices"] == []


# ---- the helper -------------------------------------------------------------


def make_service(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    applier = PeerApplier()
    return HelperService(applier, access_store=store), store, applier


def test_a_protected_device_is_allowed_and_told_why(tmp_path):
    service, _, _ = make_service(tmp_path)
    before = status(service)
    assert before["allowed"] is False and before["protected_synced"] is False
    assert sync(service, protected(MAC))["protected_count"] == 1
    result = status(service)
    assert result["allowed"] is True
    assert result["grants"] == ["protection"]
    assert result["protected_synced"] is True


def test_a_saved_grant_and_protection_are_reported_together(tmp_path):
    service, store, _ = make_service(tmp_path)
    sync(service, protected(MAC))
    assert status(service, "register-self", name="Mac")["ok"]
    assert status(service)["grants"] == ["manual", "protection"]
    removed = status(service, "remove", mac_address=MAC)
    assert removed["allowed"] is True
    assert status(service)["grants"] == ["protection"]
    sync(service, [])
    assert status(service)["allowed"] is False
    assert status(service)["grants"] == []


@pytest.mark.parametrize("change", ["other-mac", "unassociated", "relay", "duplicate"])
def test_protection_does_not_help_a_spoofed_or_unverified_identity(tmp_path, change):
    service, _, applier = make_service(tmp_path)
    sync(service, protected(MAC))
    if change == "other-mac":
        # Same IP and name, different hardware address: nothing is inherited.
        applier.neighbors[0]["lladdr"] = OTHER
        applier.stations = "Station " + OTHER + " (on wlan0)\n"
    elif change == "unassociated":
        applier.stations = ""
    elif change == "relay":
        applier.route["gateway"] = "192.168.50.100"
    else:
        applier.neighbors *= 2
    assert status(service)["allowed"] is False


def test_an_unprotected_client_is_not_allowed(tmp_path):
    service, _, _ = make_service(tmp_path)
    sync(service, protected(OTHER))
    assert status(service)["allowed"] is False


def test_the_helper_refuses_a_sync_while_another_access_request_runs(tmp_path):
    service, _, _ = make_service(tmp_path)
    service._access_lock.acquire()
    try:
        result = sync(service, protected(MAC))
    finally:
        service._access_lock.release()
    assert result["ok"] is False and result["reason"] == "helper-busy"
    assert status(service)["allowed"] is False


def test_an_owned_move_without_saved_access_leaves_the_saved_file_alone(tmp_path):
    service, store, _ = make_service(tmp_path)
    sync(service, protected(OLD))
    result = status(
        service, "move-address-owned", mac_address=OLD, move_id="a" * 32,
        expected_mac=MAC, move_access=False,
    )
    assert result["ok"] and result["moved"] is False and result["allowed"] is False
    assert not store.path.exists()


def test_the_client_sends_only_addresses_and_names(tmp_path):
    sent = []

    class Recording(HelperClient):
        def _request(self, payload, **_):
            sent.append(payload)
            return {"ok": True, "protected_synced": True, "protected_count": 1}

    Recording(socket_path=tmp_path / "x.sock").dashboard_access_protected_sync(protected(MAC))
    assert sent == [
        {"action": "dashboard-access", "operation": "protected-sync", "devices": protected(MAC)}
    ]


# ---- the dashboard: one coordinated operation -----------------------------


class BridgeHelper(PlanRecordingHelper):
    """The fake network helper with the real root helper's access decisions behind it."""

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.fail_sync = 0
        self.sync_payloads = []
        self.operations = []

    def _call(self, payload):
        result = self.service.handle_payload(payload)
        if not result.get("ok"):
            raise HelperClientError(str(result.get("error")), reason=result.get("reason"))
        return result

    def dashboard_access(self, operation="status", **values):
        self.operations.append(operation)
        return self._call({"action": "dashboard-access", "operation": operation, **values})

    def dashboard_access_protected_sync(self, devices):
        self.sync_payloads.append(list(devices))
        if self.fail_sync:
            self.fail_sync -= 1
            raise HelperClientError("could not contact the networking helper")
        return self._call(
            {"action": "dashboard-access", "operation": "protected-sync", "devices": list(devices)}
        )


class ProtectedAccessServerTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.directory = tempfile.TemporaryDirectory()
        self.store = AccessStore(Path(self.directory.name) / "access.json")
        self.applier = PeerApplier()
        self.service = HelperService(self.applier, access_store=self.store)
        self.repository = ConfigRepository(":memory:")
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.helper = BridgeHelper(self.service)
        self.server.helper_client = self.helper

    def tearDown(self):
        self.server.server_close()
        self.repository.close()
        self.directory.cleanup()

    def protect(self, value=MAC, name="Phone", **extra):
        return self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": value, "display_name": name, **extra}
        )

    def unprotect(self, value=MAC, peer=None, **extra):
        payload = {"mac_address": value, **extra}
        if peer is not None:
            return self.server.remove_protection_for_peer(payload, peer)
        return self.server.write_response_for("/api/unaffected-devices/delete", payload)

    def allowed(self):
        return self.server.dashboard_access(PEER)["allowed"]

    def test_protecting_a_device_grants_access_and_says_so(self):
        self.assertFalse(self.allowed())
        status, _, body = self.protect()
        data = json.loads(body)
        self.assertEqual(status, 200, body)
        self.assertTrue(data["dashboard_access"]["synced"])
        self.assertEqual(data["dashboard_access"]["granted"], 1)
        self.assertFalse(data.get("warning"))
        identity = self.server.dashboard_access(PEER)
        self.assertTrue(identity["allowed"])
        self.assertEqual(identity["grants"], ["protection"])

    def test_removing_protection_removes_only_the_derived_grant(self):
        self.protect()
        self.protect(OTHER)
        self.store.add(OTHER, "Saved Mac")
        status, _, body = self.unprotect(MAC)
        self.assertEqual(status, 200, body)
        self.assertFalse(self.allowed())
        self.unprotect(OTHER)
        entries = self.helper.dashboard_access("local-list")["devices"]
        self.assertEqual([item["mac_address"] for item in entries], [OTHER])
        self.assertNotIn("protected", entries[0])

    def test_a_helper_failure_is_visible_and_recovers(self):
        self.helper.fail_sync = 1
        status, _, body = self.protect()
        data = json.loads(body)
        self.assertEqual(status, 200, body)
        self.assertFalse(data["dashboard_access"]["synced"])
        self.assertIn("not confirmed", data["warning"])
        self.assertFalse(self.allowed())
        listing = self.server.protected_access_status()
        self.assertFalse(listing["synced"])
        self.assertIn("networking helper", listing["error"])
        state = self.server.sync_protected_access()
        self.assertTrue(state["synced"])
        self.assertTrue(self.allowed())

    def test_a_failed_removal_sync_says_the_device_may_keep_access_until_retry(self):
        self.protect()
        self.helper.fail_sync = 1
        status, _, body = self.unprotect(MAC)
        data = json.loads(body)
        self.assertEqual(status, 200, body)
        self.assertFalse(data["dashboard_access"]["synced"])
        self.assertIn("may still open the dashboard until Retry succeeds", data["warning"])
        # The stale grant is exactly the window the warning describes.
        self.assertTrue(self.allowed())
        self.assertTrue(self.server.sync_protected_access()["synced"])
        self.assertFalse(self.allowed())

    def test_a_busy_helper_is_retried_before_failing(self):
        calls = []
        original = self.helper.dashboard_access_protected_sync

        def busy_once(devices):
            calls.append(1)
            if len(calls) == 1:
                raise HelperClientError("Device verification is busy. Try again.", reason="helper-busy")
            return original(devices)

        self.helper.dashboard_access_protected_sync = busy_once
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        self.assertTrue(self.server.sync_protected_access()["synced"])
        self.assertEqual(len(calls), 2)

    def test_a_restarted_helper_is_resynced_before_answering(self):
        self.protect()
        self.assertTrue(self.allowed())
        syncs = len(self.helper.sync_payloads)
        # The root helper restarts and forgets the derived grants.
        self.service = HelperService(self.applier, access_store=AccessStore(self.store.path))
        self.helper.service = self.service
        self.server._protected_access_attempted -= 2  # Restart checks run at most once a second.
        self.assertTrue(self.allowed())
        self.assertEqual(len(self.helper.sync_payloads), syncs + 1)
        # Once synced, polls do not sync again.
        self.assertTrue(self.allowed())
        self.assertEqual(len(self.helper.sync_payloads), syncs + 1)

    def test_existing_protected_devices_are_upgraded_once_and_repeatably(self):
        self.store.add(OTHER, "Saved Mac")
        saved = self.store.path.read_bytes()
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        self.server.recover_protected_access()
        self.assertTrue(self.allowed())
        self.server.recover_protected_access()
        self.assertTrue(self.allowed())
        self.assertEqual(self.store.path.read_bytes(), saved)
        # An interrupted upgrade (helper unreachable) leaves nothing half-granted.
        fresh = HelperService(self.applier, access_store=AccessStore(self.store.path))
        self.helper.service = fresh
        self.helper.fail_sync = 99
        self.server.recover_protected_access()
        self.assertFalse(self.server.protected_access_status()["synced"])
        self.assertFalse(self.allowed())
        self.helper.fail_sync = 0
        self.server.recover_protected_access()
        self.assertTrue(self.allowed())

    def test_every_protected_device_is_granted_or_listed_never_dropped(self):
        from unittest.mock import patch

        from jittersim.networking.dashboard_access import normalize_mac

        odd = mac(0x10000)
        for index in range(MAX_PROTECTED_ACCESS_DEVICES):
            self.repository.save_unaffected_device(UnaffectedDevice(mac(0x10000 + index), "", 1))

        def strict(value):
            # An address the protected list accepts but the helper could never verify.
            if value == odd:
                raise AccessError("Enter a valid device hardware address.")
            return normalize_mac(value)

        with patch("jittersim.api.server.normalize_access_mac", strict):
            state = self.server.sync_protected_access()
        self.assertTrue(state["synced"])
        self.assertEqual(state["protected"], 256)
        self.assertEqual(state["granted"], 255)
        self.assertEqual(state["not_eligible"], [{"mac_address": odd,
                                                  "reason": "This address cannot identify one Wi-Fi device."}])

    def test_concurrent_changes_end_in_the_saved_state(self):
        macs = [mac(0x20000 + index) for index in range(8)]
        errors = []

        def flip(value):
            try:
                self.protect(value)
                self.unprotect(value)
                self.protect(value)
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)

        threads = [threading.Thread(target=flip, args=(value,)) for value in macs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.server.sync_protected_access()
        granted = {
            item["mac_address"]
            for item in self.helper.dashboard_access("local-list")["devices"]
            if item.get("protected")
        }
        self.assertEqual(granted, {item.mac_address for item in self.repository.list_unaffected_devices()})

    def test_a_stale_sync_cannot_overwrite_a_newer_one(self):
        self.protect()
        original = self.helper.dashboard_access_protected_sync
        reached = threading.Event()
        release = threading.Event()

        def slow(devices):
            reached.set()
            release.wait(5)
            return original(devices)

        self.helper.dashboard_access_protected_sync = slow
        worker = threading.Thread(target=self.server.sync_protected_access)
        worker.start()
        reached.wait(5)
        # A newer change saves while the older sync is still in flight.
        self.repository.delete_unaffected_device(MAC)
        release.set()
        worker.join(5)
        self.helper.dashboard_access_protected_sync = original
        self.server.sync_protected_access()
        self.assertFalse(self.allowed())

    def test_removing_the_requesters_only_route_needs_a_choice(self):
        self.protect()
        status, _, body = self.unprotect(MAC, peer=PEER)
        self.assertEqual(status, 409, body)
        self.assertTrue(json.loads(body)["access_choice_required"])
        self.assertTrue(self.allowed())
        self.assertEqual(len(self.repository.list_unaffected_devices()), 1)

    def test_keeping_access_turns_it_into_a_saved_grant_for_the_verified_device(self):
        self.protect()
        status, _, body = self.unprotect(MAC, peer=PEER, dashboard_access="keep")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.repository.list_unaffected_devices(), ())
        identity = self.server.dashboard_access(PEER)
        self.assertTrue(identity["allowed"])
        self.assertEqual(identity["grants"], ["manual"])

    def test_removing_access_on_purpose_removes_both(self):
        self.protect()
        status, _, body = self.unprotect(MAC, peer=PEER, dashboard_access="remove")
        self.assertEqual(status, 200, body)
        self.assertFalse(self.allowed())

    def test_other_devices_and_saved_grants_need_no_choice(self):
        self.protect(OTHER)
        self.assertEqual(self.unprotect(OTHER, peer=PEER)[0], 200)
        self.store.add(MAC, "Mac")
        self.protect()
        self.assertEqual(self.unprotect(MAC, peer=PEER)[0], 200)
        self.assertEqual(self.server.dashboard_access(PEER)["grants"], ["manual"])

    def test_an_invalid_choice_is_refused(self):
        self.protect()
        status, _, _ = self.unprotect(MAC, peer=PEER, dashboard_access="maybe")
        self.assertEqual(status, 400)
        self.assertTrue(self.allowed())

    def test_an_import_that_drops_the_requesters_protection_keeps_its_access(self):
        self.protect()
        settings = json.loads(self.server.response_for("/api/export")[2])
        settings["everyone_else"]["unaffected_devices"] = []
        status, _, body = self.server.import_for_peer(settings, PEER)
        self.assertEqual(status, 200, body)
        self.assertIn("kept dashboard access", json.loads(body)["dashboard_access"]["message"])
        identity = self.server.dashboard_access(PEER)
        self.assertTrue(identity["allowed"])
        self.assertEqual(identity["grants"], ["manual"])

    def history_restore(self, identifier):
        preview = json.loads(self.server.write_response_for(
            "/api/history/preview", {"id": identifier})[2])
        return self.server.history_restore_for_peer(
            {"id": identifier, "current_version": preview["current_version"]}, PEER)

    def drop_protection(self, path):
        if path == "/api/import":
            settings = json.loads(self.server.response_for("/api/export")[2])
            settings["everyone_else"]["unaffected_devices"] = []
            return self.server.import_for_peer(settings, PEER)
        first = min(item["id"] for item in
                    json.loads(self.server.response_for("/api/history")[2])["versions"])
        return self.history_restore(first)

    def check_failed_apply_keeps_the_grant(self, path):
        self.protect()
        self.assertEqual(self.server.dashboard_access(PEER)["grants"], ["protection"])
        self.helper.fail_action = "apply"
        try:
            status, _, body = self.drop_protection(path)
        finally:
            self.helper.fail_action = ""
        # The replacement was saved but its live apply failed.
        self.assertEqual(status, 502, body)
        self.assertEqual(self.repository.list_unaffected_devices(), ())
        self.assertIn("kept dashboard access", json.loads(body)["dashboard_access"]["message"])
        identity = self.server.dashboard_access(PEER)
        self.assertTrue(identity["allowed"])
        self.assertEqual(identity["grants"], ["manual"])

    def test_a_failed_live_apply_after_an_import_keeps_the_saved_grant(self):
        self.check_failed_apply_keeps_the_grant("/api/import")

    def test_a_failed_live_apply_after_a_history_restore_keeps_the_saved_grant(self):
        self.check_failed_apply_keeps_the_grant("/api/history/restore")

    def test_full_saved_grants_refuse_the_replacement_and_change_nothing(self):
        for index in range(MAX_ACCESS_DEVICES):
            self.store.add(mac(0x30000 + index), "Saved %d" % index)
        self.protect()
        for path in ("/api/import", "/api/history/restore"):
            with self.subTest(path=path):
                before = self.repository.saved_settings()
                version = self.repository.get_runtime_state().configuration_version
                plans = len(self.helper.plans)
                status, _, body = self.drop_protection(path)
                self.assertIn(status, (409, 503), body)
                self.assertIn("Nothing changed", json.loads(body)["error"])
                self.assertEqual(self.repository.saved_settings(), before)
                self.assertEqual(
                    self.repository.get_runtime_state().configuration_version, version)
                self.assertEqual(len(self.helper.plans), plans)
                identity = self.server.dashboard_access(PEER)
                self.assertTrue(identity["allowed"])
                self.assertEqual(identity["grants"], ["protection"])

    def test_a_restore_refused_after_the_grant_puts_access_back_as_it_was(self):
        first = min(item["id"] for item in
                    json.loads(self.server.response_for("/api/history")[2])["versions"])
        self.protect()
        stale = self.repository.get_runtime_state().configuration_version - 1
        status, _, body = self.server.history_restore_for_peer(
            {"id": first, "current_version": stale}, PEER)
        self.assertEqual(status, 409, body)
        self.assertEqual(len(self.repository.list_unaffected_devices()), 1)
        self.assertEqual(self.helper.operations.count("register-self"), 1)
        self.assertEqual(self.server.dashboard_access(PEER)["grants"], ["protection"])

    def test_an_invalid_import_is_refused_before_any_grant_is_saved(self):
        self.protect()
        status, _, body = self.server.import_for_peer({"profiles": "nope"}, PEER)
        self.assertEqual(status, 400, body)
        self.assertNotIn("manual", self.server.dashboard_access(PEER)["grants"])
        self.assertEqual(len(self.repository.list_unaffected_devices()), 1)

    def test_a_history_restore_that_drops_the_requesters_protection_keeps_its_access(self):
        # The first saved version predates protection.
        first = min(item["id"] for item in json.loads(self.server.response_for("/api/history")[2])["versions"])
        self.protect()
        status, _, body = self.history_restore(first)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.repository.list_unaffected_devices(), ())
        self.assertIn("kept dashboard access", json.loads(body)["dashboard_access"]["message"])
        identity = self.server.dashboard_access(PEER)
        self.assertTrue(identity["allowed"])
        self.assertEqual(identity["grants"], ["manual"])

    def test_a_history_restore_for_another_device_needs_no_saved_grant(self):
        first = min(item["id"] for item in json.loads(self.server.response_for("/api/history")[2])["versions"])
        self.protect(OTHER)
        status, _, body = self.history_restore(first)
        self.assertEqual(status, 200, body)
        self.assertNotIn("message", json.loads(body).get("dashboard_access", {}))
        self.assertEqual(self.store.saved_devices(), [])
        self.server.reconcile_protected_access()
        self.assertEqual(self.helper.dashboard_access("local-list")["devices"], [])

    def test_restoring_settings_resyncs_protection(self):
        settings = json.loads(self.server.response_for("/api/export")[2])
        settings["everyone_else"]["unaffected_devices"] = [{"mac_address": MAC, "name": "Phone"}]
        status, _, body = self.server.write_response_for("/api/import", settings)
        self.assertEqual(status, 200, body)
        self.server.reconcile_protected_access()
        self.assertTrue(self.allowed())

    def test_records_and_logs_hold_no_secret(self):
        self.server.admin_token = "admin-token-secret"
        with self.assertLogs("jittersim", level=logging.DEBUG) as captured:
            logging.getLogger("jittersim").debug("start")
            self.protect()
            self.helper.fail_sync = 1
            self.server.sync_protected_access()
        joined = "\n".join(captured.output)
        self.assertNotIn("admin-token-secret", joined)
        for payload in self.helper.sync_payloads:
            for item in payload:
                self.assertEqual(set(item), {"mac_address", "name"})


class ProtectedAddressMoveTests(unittest.TestCase):
    """A changed private address moves protection and its grant together, with Undo."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.directory = tempfile.TemporaryDirectory()
        self.store = AccessStore(Path(self.directory.name) / "access.json")
        self.applier = PeerApplier()
        self.service = HelperService(self.applier, access_store=self.store)
        self.repository = ConfigRepository(":memory:")
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.server._station_devices = lambda: []
        self.helper = BridgeHelper(self.service)
        self.server.helper_client = self.helper
        self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": OLD, "display_name": "Air"}
        )

    def tearDown(self):
        self.server.server_close()
        self.repository.close()
        self.directory.cleanup()

    def test_the_new_address_inherits_nothing_until_moved(self):
        self.assertFalse(self.server.dashboard_access(PEER)["allowed"])

    def test_a_move_carries_the_protection_grant_and_undo_returns_it(self):
        status, _, body = self.server.move_device_address_for_peer(OLD, PEER)
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertTrue(data["allowed"])
        self.assertTrue(data["moved"]["protection"])
        self.assertFalse(data["moved"]["access"])
        self.assertEqual(self.server.dashboard_access(PEER)["grants"], ["protection"])
        status, _, body = self.server.undo_device_address_move(OLD, PEER)
        self.assertEqual(status, 200, body)
        self.assertFalse(self.server.dashboard_access(PEER)["allowed"])
        entries = self.helper.dashboard_access("local-list")["devices"]
        self.assertEqual([item["mac_address"] for item in entries], [OLD])
        self.assertTrue(entries[0]["protected"])

    def test_a_move_with_both_reasons_keeps_both_and_undo_proves_ownership(self):
        self.store.add(OLD, "Air")
        status, _, body = self.server.move_device_address_for_peer(OLD, PEER)
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["moved"]["access"])
        self.assertEqual(self.server.dashboard_access(PEER)["grants"], ["manual", "protection"])
        status, _, body = self.server.undo_device_address_move(OLD, PEER)
        self.assertEqual(status, 200, body)
        self.assertFalse(self.server.dashboard_access(PEER)["allowed"])


# ---- HTTP: no reads or writes without the token and a verified device ------


class WifiPeerServer(ControlServer):
    def get_request(self):
        connection, address = super().get_request()
        return connection, (PEER, address[1])


class ProtectedAccessHttpTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.directory = tempfile.TemporaryDirectory()
        self.store = AccessStore(Path(self.directory.name) / "access.json")
        self.applier = PeerApplier()
        self.service = HelperService(self.applier, access_store=self.store)
        self.repository = ConfigRepository(":memory:")
        self.server = WifiPeerServer(("127.0.0.1", 0), self.repository, admin_token="admin-token")
        self.server.helper_client = BridgeHelper(self.service)

    def tearDown(self):
        self.server.server_close()
        self.repository.close()
        self.directory.cleanup()

    def send(self, method, path, payload=None, token="admin-token"):
        import http.client

        outcome = []

        def run():
            connection = http.client.HTTPConnection(*self.server.server_address[:2], timeout=5)
            try:
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                body = None if payload is None else json.dumps(payload).encode()
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                outcome.append((response.status, response.read()))
            finally:
                connection.close()

        client = threading.Thread(target=run)
        client.start()
        try:
            self.server.timeout = 5
            self.server.handle_request()
        finally:
            client.join(timeout=5)
        return outcome[0]

    def test_protection_alone_never_bypasses_the_token(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        self.server.sync_protected_access()
        for path in ("/api/dashboard-access", "/api/dashboard-access/status", "/api/devices"):
            for token in (None, "wrong"):
                status, body = self.send("GET", path, token=token)
                self.assertEqual(status, 401, (path, token, body))
        for path in ("/api/dashboard-access/sync-protected", "/api/unaffected-devices/delete"):
            status, _ = self.send("POST", path, {"mac_address": MAC}, token=None)
            self.assertEqual(status, 401)
        self.assertEqual(len(self.repository.list_unaffected_devices()), 1)

    def test_panic_still_works_while_a_restart_resync_is_pending(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        self.server.sync_protected_access()
        # The root helper restarts: its protection set is empty until the dashboard resyncs.
        self.service = HelperService(self.applier, access_store=AccessStore(self.store.path))
        self.server.helper_client.service = self.service
        self.server._protected_access_attempted -= 2
        status, body = self.send("POST", "/api/normal-access", {})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.server.helper_client.access_plan is None
                        or not self.server.helper_client.access_plan.options.enabled)
        self.assertTrue(self.service._access_store.protected_synced)

    def test_panic_is_retryable_while_the_access_lock_is_held_and_works_after(self):
        import time

        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        self.server.sync_protected_access()
        self.service._access_lock.acquire()
        try:
            started = time.monotonic()
            status, body = self.send("POST", "/api/normal-access", {})
            # Refused at once with a status the page retries, never queued behind the lock.
            self.assertEqual(status, 503, body)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            self.service._access_lock.release()
        status, body = self.send("POST", "/api/normal-access", {})
        self.assertEqual(status, 200, body)

    def test_panic_from_the_management_network_ignores_access_lock_and_resync(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        self.server.helper_client.service = HelperService(
            self.applier, access_store=AccessStore(self.store.path))
        self.server.helper_client.service._access_lock.acquire()
        try:
            self.server.get_request = lambda: ControlServer.get_request(self.server)
            status, body = self.send("POST", "/api/normal-access", {})
        finally:
            self.server.helper_client.service._access_lock.release()
        self.assertEqual(status, 200, body)

    def test_an_unprotected_device_with_the_token_is_still_refused(self):
        for method, path, payload in (
            ("GET", "/api/devices", None),
            ("GET", "/api/dashboard-access", None),
            ("POST", "/api/dashboard-access/sync-protected", {}),
            ("POST", "/api/unaffected-devices", {"mac_address": MAC}),
        ):
            status, body = self.send(method, path, payload)
            self.assertEqual(status, 403, (path, body))
        self.assertEqual(self.repository.list_unaffected_devices(), ())

    def test_a_protected_device_with_the_token_reads_and_sees_the_reason(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        status, body = self.send("GET", "/api/dashboard-access/status")
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertTrue(data["allowed"])
        self.assertEqual(data["grants"], ["protection"])
        status, body = self.send("GET", "/api/dashboard-access")
        self.assertEqual(status, 200, body)
        listing = json.loads(body)
        self.assertTrue(listing["protected_access"]["synced"])
        self.assertEqual(listing["devices"][0]["protected"], True)
        status, body = self.send("POST", "/api/dashboard-access/sync-protected", {})
        self.assertEqual(status, 200, body)

    def test_a_history_restore_over_http_keeps_this_devices_only_route(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        status, body = self.send("GET", "/api/history")
        first = min(item["id"] for item in json.loads(body)["versions"])
        status, body = self.send("POST", "/api/history/preview", {"id": first})
        self.assertEqual(status, 200, body)
        version = json.loads(body)["current_version"]
        status, body = self.send(
            "POST", "/api/history/restore", {"id": first, "current_version": version})
        self.assertEqual(status, 200, body)
        self.assertIn("kept dashboard access", json.loads(body)["dashboard_access"]["message"])
        status, body = self.send("GET", "/api/dashboard-access/status")
        self.assertEqual(json.loads(body)["grants"], ["manual"])

    # G6: preserve the requester's only route before the replacement is committed.

    def drop_protection_over_http(self, path):
        if path == "/api/import":
            status, body = self.send("GET", "/api/export")
            settings = json.loads(body)
            settings["everyone_else"]["unaffected_devices"] = []
            return self.send("POST", "/api/import", settings)
        status, body = self.send("GET", "/api/history")
        first = min(item["id"] for item in json.loads(body)["versions"])
        status, body = self.send("POST", "/api/history/preview", {"id": first})
        version = json.loads(body)["current_version"]
        return self.send("POST", "/api/history/restore", {"id": first, "current_version": version})

    def check_failed_apply_over_http_keeps_the_grant(self, path):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        status, body = self.send("GET", "/api/dashboard-access/status")
        self.assertEqual(json.loads(body)["grants"], ["protection"])
        self.server.helper_client.fail_action = "apply"
        try:
            status, body = self.drop_protection_over_http(path)
        finally:
            self.server.helper_client.fail_action = ""
        self.assertEqual(status, 502, body)
        self.assertEqual(self.repository.list_unaffected_devices(), ())
        self.assertIn("kept dashboard access", json.loads(body)["dashboard_access"]["message"])
        status, body = self.send("GET", "/api/dashboard-access/status")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["grants"], ["manual"])

    def test_a_failed_live_apply_after_an_import_over_http_keeps_the_saved_grant(self):
        self.check_failed_apply_over_http_keeps_the_grant("/api/import")

    def test_a_failed_live_apply_after_a_restore_over_http_keeps_the_saved_grant(self):
        self.check_failed_apply_over_http_keeps_the_grant("/api/history/restore")

    def test_full_saved_grants_over_http_refuse_the_replacement_unchanged(self):
        for index in range(MAX_ACCESS_DEVICES):
            self.store.add(mac(0x30000 + index), "Saved %d" % index)
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        for path in ("/api/import", "/api/history/restore"):
            with self.subTest(path=path):
                before = self.repository.saved_settings()
                version = self.repository.get_runtime_state().configuration_version
                status, body = self.drop_protection_over_http(path)
                self.assertIn(status, (409, 503), body)
                self.assertIn("Nothing changed", json.loads(body)["error"])
                self.assertEqual(self.repository.saved_settings(), before)
                self.assertEqual(
                    self.repository.get_runtime_state().configuration_version, version)
                status, body = self.send("GET", "/api/dashboard-access/status")
                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body)["grants"], ["protection"])

    def test_unprotecting_this_device_over_http_asks_first(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone", 1))
        status, body = self.send("POST", "/api/unaffected-devices/delete", {"mac_address": MAC})
        self.assertEqual(status, 409, body)
        self.assertTrue(json.loads(body)["access_choice_required"])
        status, body = self.send(
            "POST", "/api/unaffected-devices/delete", {"mac_address": MAC, "dashboard_access": "keep"}
        )
        self.assertEqual(status, 200, body)
        status, body = self.send("GET", "/api/dashboard-access/status")
        self.assertEqual(json.loads(body)["grants"], ["manual"])
