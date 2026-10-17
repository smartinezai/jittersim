"""Following a saved device to its new private address: storage, moves, routes and scheduler."""

import json
import re
import time
import unittest
from unittest.mock import patch

from jittersim.api.server import ControlServer, WRITE_PATHS
from jittersim.config.address_follow import (
    HISTORY_LIMIT,
    UNDO_SECONDS,
    AddressFollowStore,
    FollowConflict,
    automatic_allowed,
    current_suggestions,
    follow_automatically,
    move_profile_and_label,
    undo_profile_and_label,
)
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import DataLimit, Device, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from tests.unit.test_api import PlanRecordingHelper
from tests.unit.test_runtime import ClientListingHelper

OLD = "aa:bb:cc:00:00:01"
NEW = "02:11:22:33:44:55"
NOW = int(time.time())


def station(mac=NEW, hostname="Dads-iPhone", ipv4="192.168.50.23", seconds=120,
            neighbor="reachable"):
    return {"mac_address": mac, "ipv4_address": ipv4, "state": "connected",
            "association_verified": True, "neighbor_state": neighbor, "hostname": hostname,
            "connected_seconds": seconds}


def snapshot(*stations):
    return {"devices": list(stations), "association_status_available": True}


def seed(repository, *, protected=False, label=True):
    repository.save_profile(Profile(id="slow", name="Slow"))
    repository.save_device(Device("dad", "Dad", "192.168.50.20", "slow", OLD))
    if label:
        repository.save_connected_device_label(
            mac_address=OLD, display_name="Dad's phone", device_type="phone")
    if protected:
        repository.save_unaffected_device(UnaffectedDevice(OLD, "Dad's phone", 1))
    # Dad's phone was seen with its old address five minutes ago.
    store = AddressFollowStore(repository)
    store.observe([{"mac_address": OLD, "hostname": "dads-iphone", "ipv4_address": "192.168.50.20",
                    "connected_seconds": 10, "ipv4_verified": True}], saved={OLD}, now=NOW - 300)
    return store


class StoreAndMoveTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.store = seed(self.repository)

    def suggestions(self, *stations, now=NOW):
        return current_suggestions(self.repository, snapshot(*stations), now=now)[1]

    def test_ask_is_the_default_mode(self):
        self.assertEqual(self.store.mode(), "ask")

    def test_a_sighting_keeps_only_saved_addresses(self):
        self.suggestions(station(), station(mac=OLD, hostname="dads-iphone"))
        self.assertEqual(set(self.store.sightings()), {OLD})

    def test_a_missing_host_name_keeps_the_one_seen_before(self):
        self.store.observe([{"mac_address": OLD, "hostname": "", "ipv4_address": None,
                             "connected_seconds": 10, "ipv4_verified": False}],
                           saved={OLD}, now=NOW - 200)
        self.assertEqual(self.store.sightings()[OLD][0], "dads-iphone")

    def test_old_and_unsaved_sightings_are_forgotten(self):
        self.store.observe([], saved={OLD}, now=NOW + 31 * 24 * 60 * 60)
        self.assertEqual(self.store.sightings(), {})
        seed(self.repository)
        self.store.observe([], saved=set(), now=NOW)
        self.assertEqual(self.store.sightings(), {})

    def test_off_keeps_dismissals_and_vetoes(self):
        self.store.dismiss(OLD, NEW, until=NOW + 90 * 86400, now=NOW)
        self.store.observe([{"mac_address": OLD, "hostname": "", "ipv4_address": None,
                             "connected_seconds": 1, "ipv4_verified": False},
                            {"mac_address": "06:00:00:00:00:42", "hostname": "",
                             "ipv4_address": None, "connected_seconds": 1,
                             "ipv4_verified": False}], saved={OLD}, now=NOW - 200)
        self.store.set_mode("off")
        self.store.set_mode("automatic")
        self.assertIn((OLD, NEW), self.store.dismissed(now=NOW))
        self.assertIn((OLD, "06:00:00:00:00:42"), self.store.vetoed())
        # Automatic does not move onto the refused pair.
        seed(self.repository)
        self.assertEqual(follow_automatically(
            self.repository, snapshot(station(ipv4="192.168.50.20")), now=NOW), [])
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)

    def test_activity_lines_name_no_address(self):
        [item] = self.suggestions(station())
        move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        undo_profile_and_label(self.repository, self.store, self.store.history()[0], now=NOW)
        messages = [event["message"] for event in self.repository.list_activity()]
        self.assertEqual(len(messages), 2)
        for message in messages:
            self.assertIsNone(re.search(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", message), message)
            self.assertNotIn("dads-iphone", message.lower())

    def test_a_full_label_list_does_not_stop_a_move(self):
        for index in range(255):
            self.repository.save_connected_device_label(
                mac_address=f"aa:00:00:00:{index // 256:02x}:{index % 256:02x}",
                display_name=f"Device {index}", device_type="phone")
        [item] = self.suggestions(station())
        move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        undo_profile_and_label(self.repository, self.store, self.store.history()[0], now=NOW)
        self.assertIn(OLD, self.repository.list_connected_device_labels())

    def test_off_clears_host_names_and_suggests_nothing(self):
        self.store.set_mode("off")
        self.assertEqual(self.store.sightings(), {})
        self.assertEqual(current_suggestions(self.repository, snapshot(station()), now=NOW),
                         ("off", []))

    def test_without_a_verified_station_list_nothing_is_suggested_or_recorded(self):
        for bad in ({"devices": [station()], "association_status_available": False},
                    {**snapshot(station()), "stale": True}, None):
            self.assertEqual(current_suggestions(self.repository, bad, now=NOW)[1], [])

    def test_an_unverified_station_is_not_an_arrival(self):
        self.assertEqual(self.suggestions({**station(), "association_verified": False}), [])

    def test_a_returning_device_is_suggested(self):
        [item] = self.suggestions(station())
        self.assertEqual((item.old.mac_address, item.new.mac_address), (OLD, NEW))
        self.assertEqual(item.evidence.reasons, ("hostname", "timing"))

    def test_an_address_two_stations_claim_earns_nothing(self):
        [item] = self.suggestions(station(ipv4="192.168.50.20"),
                                  station(mac="aa:00:00:00:00:09", hostname="", ipv4="192.168.50.20"))
        self.assertNotIn("ipv4", item.evidence.reasons)
        [item] = self.suggestions(station(ipv4="192.168.50.20"))
        self.assertIn("ipv4", item.evidence.reasons)

    def test_a_pair_seen_online_together_stays_vetoed_after_a_reconnect(self):
        # Dad's old address and the new one are online together: two devices.
        self.assertEqual(self.suggestions(station(), station(mac=OLD, hostname="dads-iphone")), [])
        # Dad leaves and the other device reconnects. The veto still holds.
        self.assertEqual(self.suggestions(station(seconds=5), now=NOW + 60), [])

    def test_off_set_during_a_check_stops_its_writes(self):
        self.store.set_mode("off")
        self.store.observe([{"mac_address": OLD, "hostname": "dads-iphone", "ipv4_address": None,
                             "connected_seconds": 1, "ipv4_verified": False}],
                           saved={OLD}, now=NOW)
        self.assertEqual(self.store.sightings(), {})

    def test_expired_data_goes_even_without_a_station_list(self):
        later = NOW + 31 * 24 * 60 * 60
        current_suggestions(self.repository, None, now=later)
        self.assertEqual(self.store.sightings(), {})

    def test_undo_puts_the_ipv4_address_back_so_the_rejected_device_is_not_shaped(self):
        [item] = self.suggestions(station())
        move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        self.assertTrue(self.repository.move_device_addresses(
            {"dad": ("192.168.50.20", "192.168.50.23")}))
        undo_profile_and_label(self.repository, self.store, self.store.history()[0], now=NOW)
        device = self.repository.get_device("dad")
        self.assertEqual((device.mac_address, str(device.ipv4_address)), (OLD, "192.168.50.20"))

    def test_the_profile_and_name_move_and_undo(self):
        [item] = self.suggestions(station())
        done = move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        self.assertEqual(done["moved_profile"], True)
        self.assertEqual(done["moved_label"], True)
        device = self.repository.get_device("dad")
        # Only the MAC changes. The scheduler's address check moves the IPv4 address.
        self.assertEqual((device.mac_address, str(device.ipv4_address)), (NEW, "192.168.50.20"))
        labels = self.repository.list_connected_device_labels()
        self.assertEqual(set(labels), {NEW})
        [entry] = self.store.history()
        self.assertEqual(entry["evidence"], ["hostname", "timing"])
        self.assertNotIn("dads-iphone", json.dumps(entry))  # no host name in the history
        undo_profile_and_label(self.repository, self.store, entry, now=NOW + 5)
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)
        self.assertEqual(set(self.repository.list_connected_device_labels()), {OLD})
        self.assertIsNotNone(self.store.history()[0]["undone_at"])
        with self.assertRaises(FollowConflict):
            undo_profile_and_label(self.repository, self.store, self.store.history()[0], now=NOW)

    def test_quota_and_other_device_settings_follow_the_device_id(self):
        self.repository.save_data_limit(DataLimit("dad", "slow", 1_000_000))
        [item] = self.suggestions(station())
        move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        self.assertEqual(self.repository.get_data_limit("dad").limit_bytes, 1_000_000)

    def test_a_stale_edit_stops_the_move(self):
        [item] = self.suggestions(station())
        self.repository.save_device(Device("dad", "Dad", "192.168.50.20", "slow", "aa:bb:cc:00:00:07"))
        with self.assertRaises(FollowConflict):
            move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        self.assertEqual(self.store.history(), [])
        self.assertEqual(set(self.repository.list_connected_device_labels()), {OLD})

    def test_undo_refuses_after_the_device_was_edited(self):
        [item] = self.suggestions(station())
        move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        device = self.repository.get_device("dad")
        self.repository.save_device(Device("dad", "Renamed", "192.168.50.20", "slow", NEW),
                                    expected=device)
        with self.assertRaises(FollowConflict):
            undo_profile_and_label(self.repository, self.store, self.store.history()[0], now=NOW)
        self.assertEqual(self.repository.get_device("dad").mac_address, NEW)

    def test_undo_accepts_the_ipv4_move_the_scheduler_made(self):
        [item] = self.suggestions(station())
        move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        self.assertTrue(self.repository.move_device_addresses({"dad": ("192.168.50.20",
                                                                       "192.168.50.23")}))
        undo_profile_and_label(self.repository, self.store, self.store.history()[0], now=NOW)
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)

    def test_undo_refuses_when_a_later_move_touched_the_device(self):
        [item] = self.suggestions(station())
        move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        first = self.store.history()[0]
        # The device rotates again and that move is accepted too.
        self.store.observe([{"mac_address": NEW, "hostname": "dads-iphone",
                             "ipv4_address": None, "connected_seconds": 1,
                             "ipv4_verified": False}], saved={NEW}, now=NOW + 10)
        [again] = self.suggestions(station(mac="06:00:00:00:00:42", seconds=5), now=NOW + 20)
        move_profile_and_label(self.repository, self.store, again, mode="ask", now=NOW + 20)
        with self.assertRaises(FollowConflict):
            undo_profile_and_label(self.repository, self.store, first, now=NOW + 30)

    def test_undo_runs_out_after_seven_days(self):
        [item] = self.suggestions(station())
        move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        with self.assertRaises(FollowConflict):
            undo_profile_and_label(self.repository, self.store, self.store.history()[0],
                                   now=NOW + UNDO_SECONDS + 1)

    def test_history_never_drops_an_undoable_entry_and_then_refuses(self):
        [item] = self.suggestions(station())
        for index in range(HISTORY_LIMIT):
            with self.repository.transaction():
                self.store.add_history({"mode": "ask", "name": "x", "old_mac": OLD,
                                        "new_mac": NEW, "evidence": []}, now=NOW - index)
        self.assertTrue(self.store.history_full(now=NOW))
        with self.assertRaises(FollowConflict):
            move_profile_and_label(self.repository, self.store, item, mode="ask", now=NOW)
        self.assertEqual(len(self.store.history()), HISTORY_LIMIT)

    def test_dismissals_expire(self):
        self.store.dismiss(OLD, NEW, until=NOW + 10, now=NOW)
        self.assertEqual(self.suggestions(station()), [])
        self.assertEqual(len(self.suggestions(station(seconds=130), now=NOW + 11)), 1)

    def test_automatic_needs_strong_stable_evidence_and_respects_limits(self):
        [item] = self.suggestions(station(ipv4="192.168.50.20"))
        self.assertTrue(item.evidence.strong)
        self.assertTrue(automatic_allowed(self.store, item, now=NOW))
        [young] = self.suggestions(station(ipv4="192.168.50.20", seconds=30))
        self.assertFalse(automatic_allowed(self.store, young, now=NOW))
        # Rotation: after one move another one waits at least ten minutes, at most three a day.
        with self.repository.transaction():
            self.store.add_history({"mode": "automatic", "device_id": "dad", "name": "x",
                                    "old_mac": OLD, "new_mac": NEW, "evidence": []}, now=NOW - 60)
        self.assertFalse(automatic_allowed(self.store, item, now=NOW))
        self.assertTrue(automatic_allowed(self.store, item, now=NOW + 600))
        with self.repository.transaction():
            for offset in (2000, 3000):
                self.store.add_history({"mode": "automatic", "device_id": "dad", "name": "x",
                                        "old_mac": OLD, "new_mac": NEW, "evidence": []},
                                       now=NOW - offset)
        self.assertFalse(automatic_allowed(self.store, item, now=NOW + 600))

    def test_automatic_never_moves_a_protected_device(self):
        self.repository.save_unaffected_device(UnaffectedDevice(OLD, "Dad's phone", 1))
        self.store.set_mode("automatic")
        self.assertEqual(follow_automatically(
            self.repository, snapshot(station(ipv4="192.168.50.20")), now=NOW), [])
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)

    def test_automatic_moves_only_in_automatic_mode_and_stops_when_not_allowed(self):
        stations = snapshot(station(ipv4="192.168.50.20"))
        self.assertEqual(follow_automatically(self.repository, stations, now=NOW), [])
        self.store.set_mode("automatic")
        self.assertEqual(
            follow_automatically(self.repository, stations, now=NOW,
                                 fence=lambda publish: (False, None)), [])
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)
        self.assertEqual(self.store.history(), [])
        [done] = follow_automatically(self.repository, stations, now=NOW)
        self.assertTrue(done["moved_profile"])
        self.assertEqual(self.repository.get_device("dad").mac_address, NEW)
        self.assertEqual(self.store.history()[0]["mode"], "automatic")
        activity = self.repository.list_activity()[0]["message"]
        self.assertIn("moved automatically", activity)


class SuggestionHelper(PlanRecordingHelper):
    def __init__(self):
        super().__init__()
        self.stations = [station()]

    def connected_devices(self):
        return self.stations

    def connected_device_snapshot(self):
        return snapshot(*self.stations)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.addCleanup(self.server.server_close)
        self.helper = SuggestionHelper()
        self.server.helper_client = self.helper
        self.store = seed(self.repository)

    def read(self):
        status, _, body = self.server.response_for("/api/address-follow")
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def write(self, path, payload):
        status, _, body = self.server.write_response_for(path, payload)
        return status, json.loads(body)

    def test_the_writes_need_the_admin_token_like_every_write(self):
        for path in ("/api/address-follow/settings", "/api/address-follow/accept",
                     "/api/address-follow/dismiss", "/api/address-follow/undo"):
            self.assertIn(path, WRITE_PATHS)

    def test_the_suggestion_says_what_moves_and_what_stays(self):
        data = self.read()
        self.assertEqual(data["mode"], "ask")
        [item] = data["suggestions"]
        self.assertEqual((item["old_mac"], item["new_mac"], item["name"]),
                         (OLD, NEW, "Dad's phone"))
        self.assertEqual(item["moves"], {"profile": True, "name": True, "protection": False})
        self.assertEqual(item["stays"], {"dashboard_access": False, "mac_list": False})
        self.assertIn('It sends the same device name "dads-iphone".', item["evidence"])

    def test_yes_moves_the_profile_and_undo_reverses_it(self):
        status, result = self.write("/api/address-follow/accept",
                                    {"old_mac": OLD, "new_mac": NEW, "move_protection": False})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.repository.get_device("dad").mac_address, NEW)
        [entry] = self.read()["history"]
        self.assertTrue(entry["can_undo"] and entry["moved_profile"])
        status, result = self.write("/api/address-follow/undo", {"id": entry["id"]})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)
        # After an Undo the same pair is not suggested again.
        self.assertEqual(self.read()["suggestions"], [])

    def test_a_suggestion_that_is_no_longer_current_is_refused(self):
        self.helper.stations = [station(), station(mac=OLD, hostname="dads-iphone")]
        status, result = self.write("/api/address-follow/accept",
                                    {"old_mac": OLD, "new_mac": NEW, "move_protection": False})
        self.assertEqual(status, 409)
        self.assertIn("no longer current", result["error"])
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)

    def test_a_made_up_pair_is_refused(self):
        status, _ = self.write("/api/address-follow/accept",
                               {"old_mac": OLD, "new_mac": "02:99:99:99:99:99",
                                "move_protection": False})
        self.assertEqual(status, 409)
        status, _ = self.write("/api/address-follow/accept", {"old_mac": OLD, "new_mac": NEW})
        self.assertEqual(status, 400)

    def test_a_protected_device_moves_only_with_its_protection_and_undo_returns_both(self):
        self.repository.save_unaffected_device(UnaffectedDevice(OLD, "Dad's phone", 1))
        [item] = self.read()["suggestions"]
        self.assertTrue(item["moves"]["protection"])
        status, result = self.write("/api/address-follow/accept",
                                    {"old_mac": OLD, "new_mac": NEW, "move_protection": False})
        self.assertEqual(status, 409)
        self.assertIn("protection too", result["error"])
        status, result = self.write("/api/address-follow/accept",
                                    {"old_mac": OLD, "new_mac": NEW, "move_protection": True})
        self.assertEqual(status, 200, result)
        self.assertEqual({item.mac_address for item in self.repository.list_unaffected_devices()},
                         {NEW})
        self.assertEqual(self.repository.get_device("dad").mac_address, NEW)
        # The existing move recorded it, so its own Undo can reverse it.
        self.assertEqual(self.repository.get_address_move(OLD).new_mac, NEW)
        [entry] = self.read()["history"]
        self.assertTrue(entry["moved_protection"])
        status, result = self.write("/api/address-follow/undo", {"id": entry["id"]})
        self.assertEqual(status, 200, result)
        self.assertEqual({item.mac_address for item in self.repository.list_unaffected_devices()},
                         {OLD})
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)
        self.assertIsNone(self.repository.get_address_move(OLD))

    def test_a_failed_profile_move_leaves_protection_where_it_was(self):
        self.repository.save_unaffected_device(UnaffectedDevice(OLD, "Dad's phone", 1))

        def refuse(*args, **kwargs):
            raise FollowConflict("The saved device changed. Nothing was moved.")

        with patch("jittersim.api.address_follow.move_profile_and_label", refuse):
            status, result = self.write("/api/address-follow/accept",
                                        {"old_mac": OLD, "new_mac": NEW, "move_protection": True})
        self.assertEqual(status, 409, result)
        # One transaction: protection, the move record and the profile all stay as they were.
        self.assertEqual({item.mac_address for item in self.repository.list_unaffected_devices()},
                         {OLD})
        self.assertIsNone(self.repository.get_address_move(OLD))
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)
        self.assertEqual(self.read()["history"], [])

    def test_undo_of_a_protected_move_is_refused_before_anything_changes(self):
        self.repository.save_unaffected_device(UnaffectedDevice(OLD, "Dad's phone", 1))
        self.write("/api/address-follow/accept",
                   {"old_mac": OLD, "new_mac": NEW, "move_protection": True})
        # Another saved device takes the old address meanwhile.
        self.repository.save_device(Device("other", "Other", "192.168.50.40", "slow", OLD))
        [entry] = self.read()["history"]
        status, result = self.write("/api/address-follow/undo", {"id": entry["id"]})
        self.assertEqual(status, 409, result)
        self.assertEqual({item.mac_address for item in self.repository.list_unaffected_devices()},
                         {NEW})
        self.assertEqual(self.repository.get_device("dad").mac_address, NEW)

    def test_undo_under_dashboard_access_also_puts_the_profile_and_name_back(self):
        self.repository.save_unaffected_device(UnaffectedDevice(OLD, "Dad's phone", 1))
        status, result = self.write("/api/address-follow/accept",
                                    {"old_mac": OLD, "new_mac": NEW, "move_protection": True})
        self.assertEqual(status, 200, result)
        # The other route: the existing Undo under Dashboard access.
        status, _, body = self.server.undo_device_address_move(OLD, "")
        self.assertEqual(status, 200, body)
        self.assertEqual({item.mac_address for item in self.repository.list_unaffected_devices()},
                         {OLD})
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)
        self.assertEqual(set(self.repository.list_connected_device_labels()), {OLD})
        [entry] = self.read()["history"]
        self.assertIsNotNone(entry["undone_at"])
        self.assertFalse(entry["can_undo"])
        self.assertIsNone(self.repository.get_address_move(OLD))

    def test_undo_under_dashboard_access_refuses_before_anything_changes(self):
        self.repository.save_unaffected_device(UnaffectedDevice(OLD, "Dad's phone", 1))
        self.write("/api/address-follow/accept",
                   {"old_mac": OLD, "new_mac": NEW, "move_protection": True})
        device = self.repository.get_device("dad")
        self.repository.save_device(Device("dad", "Renamed", "192.168.50.20", "slow", NEW),
                                    expected=device)
        status, _, body = self.server.undo_device_address_move(OLD, "")
        self.assertEqual(status, 409, body)
        self.assertEqual({item.mac_address for item in self.repository.list_unaffected_devices()},
                         {NEW})
        # Nothing is left half undone: the move record is still saved, not in recovery.
        self.assertEqual(self.repository.get_address_move(OLD).phase, "saved")

    def test_every_undoable_move_stays_visible(self):
        with self.repository.transaction():
            for index in range(30):
                self.store.add_history({"mode": "ask", "name": f"x{index}", "old_mac": OLD,
                                        "new_mac": NEW, "evidence": []}, now=NOW - index)
        self.assertEqual(len(self.read()["history"]), 30)

    def test_dashboard_access_and_the_mac_list_are_named_as_manual(self):
        self.helper.dashboard_access = lambda *args, **kwargs: {
            "devices": [{"mac_address": OLD, "name": "Dad", "manual": True}]}
        [item] = self.read()["suggestions"]
        self.assertTrue(item["stays"]["dashboard_access"])

    def test_no_and_not_this_device(self):
        status, _ = self.write("/api/address-follow/dismiss",
                               {"old_mac": OLD, "new_mac": NEW, "scope": "later"})
        self.assertEqual(status, 200)
        self.assertEqual(self.read()["suggestions"], [])
        status, _ = self.write("/api/address-follow/dismiss",
                               {"old_mac": OLD, "new_mac": NEW, "scope": "forever"})
        self.assertEqual(status, 400)

    def test_settings(self):
        for mode in ("automatic", "off", "ask"):
            status, result = self.write("/api/address-follow/settings", {"mode": mode})
            self.assertEqual((status, result["mode"]), (200, mode))
        status, _ = self.write("/api/address-follow/settings", {"mode": "sometimes"})
        self.assertEqual(status, 400)

    def test_off_reads_nothing_from_the_helper(self):
        self.write("/api/address-follow/settings", {"mode": "off"})
        self.helper.connected_device_snapshot = None
        data = self.read()
        self.assertEqual((data["mode"], data["suggestions"]), ("off", []))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.store = seed(self.repository)
        self.store.set_mode("automatic")
        self.helper = ClientListingHelper()
        self.scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=self.helper, poll_seconds=3_600)
        self.addCleanup(self.scheduler.stop)
        self.scheduler.activate(applied_payload={})

    def test_automatic_moves_the_profile_and_the_ip_follow_moves_it_in_the_same_tick(self):
        self.helper.clients = [station(ipv4="192.168.50.20")]
        self.scheduler.tick(repository=self.repository, now=NOW)
        device = self.repository.get_device("dad")
        self.assertEqual((device.mac_address, str(device.ipv4_address)), (NEW, "192.168.50.20"))
        self.helper.clients = [station(ipv4="192.168.50.23", seconds=150)]
        self.scheduler.tick(repository=self.repository, now=NOW + 31)
        self.assertEqual(str(self.repository.get_device("dad").ipv4_address), "192.168.50.23")
        self.assertEqual(str(self.helper.plans[-1].mark_rules[0].ipv4_address), "192.168.50.23")

    def test_ask_mode_moves_nothing_in_the_scheduler(self):
        self.store.set_mode("ask")
        self.helper.clients = [station(ipv4="192.168.50.20")]
        self.scheduler.tick(repository=self.repository, now=NOW)
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)

    def test_after_reset_the_scheduler_moves_nothing(self):
        self.repository.update_runtime_state(observed_state="reset", last_action="reset",
                                             last_result="success", last_error=None)
        self.helper.clients = [station(ipv4="192.168.50.20")]
        self.scheduler.tick(repository=self.repository, now=NOW)
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)

    def test_a_paused_scheduler_moves_nothing(self):
        self.scheduler.pause()
        self.helper.clients = [station(ipv4="192.168.50.20")]
        self.scheduler.tick(repository=self.repository, now=NOW)
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)

    def test_a_panic_during_the_check_stops_the_commit(self):
        self.helper.clients = [station(ipv4="192.168.50.20")]
        original = self.helper.connected_device_snapshot

        def panic_while_reading():
            result = original()
            self.scheduler.halt()
            return result

        self.helper.connected_device_snapshot = panic_while_reading
        self.scheduler.tick(repository=self.repository, now=NOW)
        self.assertEqual(self.repository.get_device("dad").mac_address, OLD)
        self.assertEqual(self.store.history(), [])
