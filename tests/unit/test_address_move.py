"""A device that comes back with a new private Wi-Fi address keeps its access and protection."""

import json
import unittest

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.storage import ConfigRepository
from jittersim.networking.dashboard_access import AccessError, AccessStore
from jittersim.networking.helper import HelperService
from tests.unit.test_api import FakeHelper, PlanRecordingHelper
from tests.unit.test_dashboard_access import MAC, PEER, PeerApplier

OLD = "02:00:00:00:00:99"


def move_request(operation="move-address", **values):
    return {"action": "dashboard-access", "operation": operation, "peer": PEER, **values}


# ---- the saved access list -------------------------------------------------


def test_a_move_keeps_the_old_entry_marked_replaced(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Aeromac")
    devices = store.move(OLD, MAC)
    old, new = devices
    assert old["mac_address"] == OLD and old["replaced_by"] == MAC
    assert isinstance(old["replaced_at"], int)
    assert new["mac_address"] == MAC and new["name"] == "Aeromac"
    assert "replaced_by" not in new
    # The file is valid after a restart.
    assert AccessStore(store.path).list_devices() == devices


def test_a_move_needs_an_active_old_entry_and_a_new_address(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Aeromac")
    with pytest.raises(AccessError):
        store.move("02:00:00:00:00:55", MAC)
    store.add(MAC, "Already")
    with pytest.raises(AccessError):
        store.move(OLD, MAC)
    store.move(OLD, "02:00:00:00:00:77")
    with pytest.raises(AccessError):
        store.move(OLD, "02:00:00:00:00:78")  # Replaced entries cannot move twice.


def test_undo_restores_the_old_entry_and_removes_the_new_one(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Aeromac")
    store.move(OLD, MAC)
    devices = store.undo_move(OLD)
    assert [item["mac_address"] for item in devices] == [OLD]
    assert "replaced_by" not in devices[0]
    with pytest.raises(AccessError):
        store.undo_move(OLD)


def test_undo_refuses_when_the_new_address_moved_on(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Aeromac")
    store.move(OLD, MAC)
    store.move(MAC, "02:00:00:00:00:77")
    with pytest.raises(AccessError):
        store.undo_move(OLD)


def test_moving_back_to_a_replaced_address_works(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Aeromac")
    store.move(OLD, MAC)
    devices = store.move(MAC, OLD)
    active = [item["mac_address"] for item in devices if "replaced_by" not in item]
    assert active == [OLD]


def test_a_half_marked_entry_is_invalid(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Aeromac")
    data = json.loads(store.path.read_text())
    data["devices"][0]["replaced_by"] = MAC
    store.path.write_text(json.dumps(data))
    with pytest.raises(AccessError):
        store.list_devices()


# ---- the helper -------------------------------------------------------------


def make_service(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    applier = PeerApplier()
    return HelperService(applier, access_store=store), store, applier


def test_a_replaced_address_no_longer_grants_access(tmp_path):
    service, store, applier = make_service(tmp_path)
    store.add(MAC, "Aeromac")
    assert service.handle_payload(move_request("status"))["allowed"]
    store.move(MAC, OLD)
    assert not service.handle_payload(move_request("status"))["allowed"]


def test_move_address_uses_the_verified_peer_and_returns_it(tmp_path):
    service, store, _ = make_service(tmp_path)
    store.add(OLD, "Aeromac")
    result = service.handle_payload(move_request(mac_address=OLD))
    assert result["ok"] and result["moved"] and result["allowed"]
    assert result["mac_address"] == MAC
    assert [item["mac_address"] for item in store.list_devices()] == [OLD, MAC]


def test_move_address_without_a_registered_old_entry_only_verifies(tmp_path):
    service, store, _ = make_service(tmp_path)
    result = service.handle_payload(move_request(mac_address=OLD))
    assert result["ok"] and not result["moved"] and not result["allowed"]
    assert result["mac_address"] == MAC
    assert store.list_devices() == []


@pytest.mark.parametrize("state", [["STALE"], ["DELAY"]])
def test_move_address_needs_a_freshly_confirmed_device(tmp_path, state):
    service, store, applier = make_service(tmp_path)
    store.add(OLD, "Aeromac")
    applier.neighbors[0]["state"] = state
    result = service.handle_payload(move_request(mac_address=OLD))
    assert not result["ok"]
    assert [item["mac_address"] for item in store.list_devices()] == [OLD]


def test_move_address_fails_closed_for_relays_and_missing_stations(tmp_path):
    service, store, applier = make_service(tmp_path)
    store.add(OLD, "Aeromac")
    applier.stations = ""
    assert not service.handle_payload(move_request(mac_address=OLD))["ok"]
    applier.stations = "Station " + MAC + " (on wlan0)\n"
    applier.route["gateway"] = "192.168.50.100"
    assert not service.handle_payload(move_request(mac_address=OLD))["ok"]
    assert [item["mac_address"] for item in store.list_devices()] == [OLD]


def test_undo_move_needs_an_allowed_caller(tmp_path):
    service, store, _ = make_service(tmp_path)
    store.add(OLD, "Aeromac")
    store.move(OLD, "02:00:00:00:00:77")
    refused = service.handle_payload(move_request("undo-move", mac_address=OLD))
    assert not refused["ok"]
    store.add(MAC, "This Mac")
    done = service.handle_payload(move_request("undo-move", mac_address=OLD))
    assert done["ok"] and done["allowed"]
    assert "replaced_by" not in next(
        item for item in store.list_devices() if item["mac_address"] == OLD
    )


# ---- the stored move record ------------------------------------------------


def test_move_records_survive_in_the_database():
    from jittersim.config.exemptions import AddressMove

    with ConfigRepository(":memory:") as repository:
        move = AddressMove(OLD, MAC, "Aeromac", True, True, 5)
        repository.save_address_move(move)
        assert repository.get_address_move(OLD) == move
        assert repository.list_address_moves() == (move,)
        assert repository.delete_address_move(OLD)
        assert not repository.delete_address_move(OLD)
        assert repository.list_address_moves() == ()


# ---- the dashboard server --------------------------------------------------


class AddressMoveServerTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.helper = PlanRecordingHelper()
        self.server.helper_client = self.helper
        self.repository.save_unaffected_device(UnaffectedDevice(OLD, "Aeromac", 1))

    def tearDown(self):
        self.server.server_close()
        self.repository.close()

    def protected(self):
        return {item.mac_address for item in self.repository.list_unaffected_devices()}

    def test_moving_protection_is_one_change_with_one_plan(self):
        status, _, body = self.server.move_device_address(OLD, MAC, access_moved=False)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.protected(), {MAC})
        self.assertEqual(self.repository.list_unaffected_devices()[0].display_name, "Aeromac")
        # The old address is never unprotected in a plan without the new one.
        self.assertEqual(len(self.helper.plans), 1)
        self.assertTrue(json.loads(body)["live_applied"])
        move = self.repository.get_address_move(OLD)
        self.assertEqual(
            (move.new_mac, move.moved_protection, move.moved_access), (MAC, True, False)
        )

    def test_returning_to_an_earlier_address_keeps_undo_available(self):
        self.assertEqual(self.server.move_device_address(OLD, MAC, access_moved=False)[0], 200)
        self.assertEqual(self.server.move_device_address(MAC, OLD, access_moved=False)[0], 200)
        self.assertEqual(self.server.undo_device_address_move(MAC, PEER)[0], 200)
        self.assertEqual(self.protected(), {MAC})

    def test_move_and_undo_carry_the_current_ad_blocking_choice(self):
        self.helper.configure_ad_blocking = lambda *args, **kwargs: {"available": True}
        self.helper.ad_blocking_status = lambda: {"available": True}
        self.repository.set_ad_blocking(OLD, True)
        self.assertEqual(self.server.move_device_address(OLD, MAC, access_moved=False)[0], 200)
        self.assertEqual(self.repository.list_ad_blocking_devices(), (MAC,))
        # A switch change after the move belongs to the device when undoing too.
        self.repository.set_ad_blocking(MAC, False)
        self.assertEqual(self.server.undo_device_address_move(OLD, PEER)[0], 200)
        self.assertEqual(self.protected(), {OLD})
        self.assertEqual(self.repository.list_ad_blocking_devices(), ())

    def test_undo_refuses_protection_saved_independently_even_with_identical_fields(self):
        self.server.move_device_address(OLD, MAC, access_moved=False)
        moved = self.repository.list_unaffected_devices()[0]
        self.repository.delete_unaffected_device(MAC)
        self.repository.save_unaffected_device(moved)
        status, _, body = self.server.undo_device_address_move(OLD, PEER)
        self.assertEqual(status, 409, body)
        self.assertEqual(self.protected(), {MAC})

    def test_a_move_of_something_already_protected_changes_nothing(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Already", 1))
        status, _, _ = self.server.move_device_address(OLD, MAC, access_moved=False)
        self.assertEqual(status, 409)
        self.assertEqual(self.protected(), {OLD, MAC})
        self.assertEqual(self.helper.plans, [])
        self.assertIsNone(self.repository.get_address_move(OLD))

    def test_a_move_from_an_unknown_address_is_refused(self):
        status, _, _ = self.server.move_device_address("02:00:00:00:00:55", MAC, access_moved=False)
        self.assertEqual(status, 409)
        self.assertEqual(self.protected(), {OLD})

    def test_only_access_moved_needs_no_plan(self):
        self.repository.delete_unaffected_device(OLD)
        status, _, body = self.server.move_device_address(OLD, MAC, access_moved=True)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.helper.plans, [])
        self.assertTrue(self.repository.get_address_move(OLD).moved_access)

    def test_a_failed_save_leaves_the_old_protection_and_no_record(self):
        original = self.repository.save_unaffected_device

        def broken(device):
            if device.mac_address == MAC:
                raise ValueError("simulated")
            original(device)

        self.repository.save_unaffected_device = broken
        status, _, _ = self.server.move_device_address(OLD, MAC, access_moved=False)
        self.assertEqual(status, 409)
        self.assertEqual(self.protected(), {OLD})
        self.assertIsNone(self.repository.get_address_move(OLD))

    def test_undo_restores_the_old_protection_with_one_plan(self):
        self.server.move_device_address(OLD, MAC, access_moved=False)
        self.helper.plans.clear()
        status, _, body = self.server.undo_device_address_move(OLD, PEER)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.protected(), {OLD})
        self.assertEqual(len(self.helper.plans), 1)
        self.assertIsNone(self.repository.get_address_move(OLD))

    def test_undo_of_an_unknown_move_is_not_found(self):
        status, _, _ = self.server.undo_device_address_move(OLD, PEER)
        self.assertEqual(status, 404)

    def test_the_offer_lists_protected_names_for_an_unprotected_new_address(self):
        offer = self.server.address_move_offer(
            {"network": "test", "mac_address": MAC, "allowed": True, "can_register": True}
        )
        self.assertTrue(offer["offer"])
        self.assertEqual(offer["candidates"][0]["name"], "Aeromac")
        self.assertEqual(offer["candidates"][0]["mac_address"], OLD)
        self.assertTrue(offer["candidates"][0]["protected"])
        self.assertTrue(offer["current"]["private_address"])
        self.assertTrue(offer["current"]["registered"])
        self.assertFalse(offer["current"]["protected"])

    def test_no_offer_when_the_device_is_already_recognised_or_unverified(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Me", 1))
        both = {"network": "test", "mac_address": MAC, "allowed": True, "can_register": True}
        self.assertFalse(self.server.address_move_offer(both)["offer"])
        self.repository.delete_unaffected_device(MAC)
        for identity in (
            {"network": "management", "mac_address": None, "allowed": True},
            {"network": "test", "mac_address": None, "allowed": False},
        ):
            self.assertFalse(self.server.address_move_offer(identity)["offer"])

    def test_no_offer_for_a_device_that_is_still_connected_under_its_address(self):
        class Present(FakeHelper):
            def connected_devices(self):
                return [
                    {
                        "ipv4_address": "192.168.50.20",
                        "mac_address": OLD,
                        "state": "connected",
                        "association_verified": True,
                    }
                ]

        self.server._station_devices = lambda: [
            {"ipv4_address": "192.168.50.20", "mac_address": OLD, "hostname": None}
        ]
        offer = self.server.address_move_offer(
            {"network": "test", "mac_address": MAC, "allowed": True, "can_register": True}
        )
        self.assertFalse(offer["offer"])

    def test_the_offer_lists_recent_moves_to_a_registered_device_only(self):
        self.server.move_device_address(OLD, MAC, access_moved=False)
        identity = {"network": "test", "mac_address": MAC, "allowed": True, "can_register": True}
        self.assertEqual(self.server.address_move_offer(identity)["replaced"][0]["old_mac"], OLD)
        identity["allowed"] = False
        self.assertEqual(self.server.address_move_offer(identity)["replaced"], [])


# ---- review fixes ----------------------------------------------------------

from jittersim.networking.helper import HelperClientError  # noqa: E402

THIRD = "02:00:00:00:00:77"


class AccessListHelper(PlanRecordingHelper):
    """Answers the saved access list and records what the server asks the helper to do."""

    def __init__(self, devices):
        super().__init__()
        self.devices = devices
        self.calls = []

    def dashboard_access(self, operation, **values):
        self.calls.append(operation)
        if operation == "local-list":
            return {"ok": True, "devices": self.devices}
        if operation == "undo-move":
            raise HelperClientError("There is no move to undo for that device.")
        return {
            "ok": True,
            "network": "test",
            "allowed": True,
            "can_register": True,
            "mac_address": MAC,
        }


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.server.helper_client = AccessListHelper([])
        self.repository.save_unaffected_device(UnaffectedDevice(OLD, "Aeromac", 1))

    def tearDown(self):
        self.server.server_close()
        self.repository.close()

    def identity(self, **values):
        return {
            "network": "test",
            "mac_address": MAC,
            "allowed": True,
            "can_register": True,
            **values,
        }

    def test_a_device_that_is_not_a_candidate_cannot_be_the_source_of_a_move(self):
        self.assertTrue(self.server.is_address_move_source(self.identity(), OLD))
        self.assertFalse(self.server.is_address_move_source(self.identity(), THIRD))
        # A device that is still connected under its own address is not an old address.
        self.server._station_devices = lambda: [
            {"ipv4_address": "192.168.50.20", "mac_address": OLD, "hostname": None}
        ]
        self.assertFalse(self.server.is_address_move_source(self.identity(), OLD))

    def test_an_unverified_requester_is_not_offered_a_move(self):
        offer = self.server.address_move_offer(self.identity(can_register=False))
        self.assertFalse(offer["offer"])
        self.assertEqual(offer["candidates"], [])

    def test_an_earlier_move_cannot_be_undone_after_the_device_moved_again(self):
        self.server.move_device_address(OLD, MAC, access_moved=False)
        self.server.move_device_address(MAC, THIRD, access_moved=False)
        status, _, body = self.server.undo_device_address_move(OLD, PEER)
        self.assertEqual(status, 409, body)
        self.assertIn("latest move first", json.loads(body)["error"])
        self.assertEqual(
            {item.mac_address for item in self.repository.list_unaffected_devices()}, {THIRD}
        )
        self.assertEqual(self.server.undo_device_address_move(MAC, PEER)[0], 200)
        self.assertEqual(self.server.undo_device_address_move(OLD, PEER)[0], 200)
        self.assertEqual(
            {item.mac_address for item in self.repository.list_unaffected_devices()}, {OLD}
        )

    def test_undo_refuses_when_the_access_ownership_evidence_was_removed(self):
        self.repository.delete_unaffected_device(OLD)
        self.server.move_device_address(OLD, MAC, access_moved=True)
        status, _, body = self.server.undo_device_address_move(OLD, PEER)
        self.assertEqual(status, 409, body)
        self.assertIsNotNone(self.repository.get_address_move(OLD))
        self.assertNotIn("undo-move", self.server.helper_client.calls)

    def test_legacy_replacement_metadata_does_not_prove_access_ownership(self):
        self.repository.delete_unaffected_device(OLD)
        self.server.helper_client.devices = [
            {
                "mac_address": OLD,
                "name": "Aeromac",
                "added_at": 1,
                "replaced_by": MAC,
                "replaced_at": 2,
            }
        ]
        self.server.move_device_address(OLD, MAC, access_moved=True)
        status, _, _ = self.server.undo_device_address_move(OLD, PEER)
        self.assertEqual(status, 409)
        self.assertNotIn("undo-move", self.server.helper_client.calls)
        self.assertIsNotNone(self.repository.get_address_move(OLD))


def test_access_undo_refuses_a_destination_registered_independently(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Original")
    store.move(OLD, MAC)
    store.remove(MAC)
    store.add(MAC, "Independent")
    before = store.list_devices()
    with pytest.raises(AccessError, match="changed|ownership"):
        store.undo_move(OLD)
    assert store.list_devices() == before


def test_legacy_access_move_is_readable_but_cannot_claim_undo_ownership(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Legacy")
    store.move(OLD, MAC)
    data = json.loads(store.path.read_text())
    for entry in data["devices"]:
        for key in ("owner_id", "replacement_id", "undo_id"):
            entry.pop(key, None)
    store.path.write_text(json.dumps(data))
    before = AccessStore(store.path).list_devices()
    with pytest.raises(AccessError, match="ownership|recovery"):
        store.undo_move(OLD)
    assert store.list_devices() == before


def test_move_reservations_cannot_be_replaced_or_evicted():
    from dataclasses import replace
    from jittersim.config.exemptions import AddressMove
    from jittersim.config.storage import StorageError

    with ConfigRepository(":memory:") as repository:
        first = AddressMove(OLD, MAC, move_id="a" * 32, phase="pending", prepared_version=0)
        repository.save_address_move(first)
        with pytest.raises(StorageError, match="existing"):
            repository.save_address_move(replace(first, move_id="b" * 32))
        for index in range(1, 64):
            repository.save_address_move(
                AddressMove(
                    "02:11:00:00:00:%02x" % index,
                    MAC,
                    move_id="%032x" % index,
                    phase="pending",
                    prepared_version=0,
                )
            )
        with pytest.raises(StorageError, match="full"):
            repository.save_address_move(
                AddressMove(
                    "02:11:00:00:00:ff", MAC, move_id="f" * 32, phase="pending", prepared_version=0
                )
            )
        assert repository.get_address_move(OLD) == first
        assert len(repository.list_address_moves()) == 64


def test_overflow_history_is_detected_instead_of_hiding_recovery():
    from jittersim.config.storage import StorageError

    with ConfigRepository(":memory:") as repository:
        for index in range(65):
            repository._connection.execute(
                "INSERT INTO address_moves (old_mac, new_mac, name, moved_access, "
                "moved_protection, moved_at) VALUES (?, ?, '', 0, 0, 0)",
                ("02:11:00:00:00:%02x" % index, MAC),
            )
        with pytest.raises(StorageError, match="exceeds"):
            repository.list_address_moves()


def test_old_move_schema_upgrades_without_inventing_ownership(tmp_path):
    import sqlite3

    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE address_moves (old_mac TEXT PRIMARY KEY, new_mac TEXT NOT NULL, "
            "name TEXT NOT NULL, moved_access INTEGER NOT NULL, "
            "moved_protection INTEGER NOT NULL, moved_at INTEGER NOT NULL);"
        )
        connection.execute("INSERT INTO address_moves VALUES (?, ?, 'Legacy', 1, 1, 1)", (OLD, MAC))
    for _ in range(2):
        with ConfigRepository(database) as repository:
            move = repository.get_address_move(OLD)
            assert move.move_id == "" and move.phase == "saved" and move.prepared_version == -1


def test_database_backup_does_not_prove_current_root_ownership(tmp_path):
    store = AccessStore(tmp_path / "access.json")
    store.add(OLD, "Original")
    with ConfigRepository(":memory:") as repository:
        server = ControlServer(("127.0.0.1", 0), repository)
        try:
            server.helper_client = AccessListHelper([])
            repository.save_unaffected_device(UnaffectedDevice(OLD, "Original", 1))
            assert server.move_device_address(OLD, MAC, access_moved=True)[0] == 200
            record = repository.get_address_move(OLD)
            store.move(OLD, MAC, move_id=record.move_id)
            snapshot = tmp_path / "snapshot.db"
            snapshot.write_bytes(repository.backup_bytes())
        finally:
            server.server_close()
    store.remove(MAC)
    store.add(MAC, "Independent")
    with ConfigRepository(snapshot) as restored:
        server = ControlServer(("127.0.0.1", 0), restored)
        try:
            helper = AccessListHelper(store.list_devices())
            server.helper_client = helper
            assert server.undo_device_address_move(OLD, PEER)[0] == 409
            assert "undo-move" not in helper.calls
            assert restored.get_address_move(OLD) == record
            assert [item.mac_address for item in restored.list_unaffected_devices()] == [MAC]
        finally:
            server.server_close()


@pytest.mark.parametrize(
    "change",
    [
        {"move_id": None},
        {"move_id": ""},
        {"move_id": "not-an-id"},
        {"expected_mac": None},
        {"expected_mac": OLD},
        {"move_access": 1},
    ],
)
def test_owned_move_refuses_missing_or_changed_ownership_before_mutation(tmp_path, change):
    service, store, _ = make_service(tmp_path)
    store.add(OLD, "Original")
    before = store.list_devices()
    payload = move_request(
        "move-address-owned", mac_address=OLD, expected_mac=MAC, move_id="a" * 32, move_access=True
    )
    assert not service.handle_payload({**payload, **change})["ok"]
    assert store.list_devices() == before


def test_owned_move_false_preserves_access_after_a_fresh_identity_check(tmp_path):
    service, store, _ = make_service(tmp_path)
    store.add(OLD, "Original")
    before = store.list_devices()
    result = service.handle_payload(
        move_request(
            "move-address-owned",
            mac_address=OLD,
            expected_mac=MAC,
            move_id="a" * 32,
            move_access=False,
        )
    )
    assert result["ok"] and not result["moved"] and not result["allowed"]
    assert store.list_devices() == before
