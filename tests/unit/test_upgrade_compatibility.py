"""Protect live upgrades from the original device-list layout."""

import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import DomainObservation, Profile
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.networking.helper import HelperClient, UnixHelperServer
from tests.unit.test_api import FakeHelper


def legacy_database(path, rows):
    with ConfigRepository(path) as repository:
        repository.save_profile(Profile("normal", "Keep this profile"))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE unaffected_devices")
        connection.execute(
            "CREATE TABLE unaffected_devices (mac_address TEXT PRIMARY KEY, name TEXT NOT NULL)"
        )
        connection.executemany("INSERT INTO unaffected_devices VALUES (?, ?)", rows)


def test_original_protection_list_upgrades_and_remains_editable(tmp_path):
    path = tmp_path / "settings.db"
    legacy_database(path, [("AA:BB:CC:DD:EE:01", "Keep this device")])
    with ConfigRepository(path) as repository:
        saved = repository.list_unaffected_devices()
        assert saved == (UnaffectedDevice("aa:bb:cc:dd:ee:01", "Keep this device", 0),)
        assert repository.get_profile("normal").name == "Keep this profile"
        repository.save_unaffected_device(UnaffectedDevice(saved[0].mac_address, "Renamed", 15))
        repository.save_unaffected_device(UnaffectedDevice("aa:bb:cc:dd:ee:02", "New", 20))
    with ConfigRepository(path) as repository:
        assert [
            (item.display_name, item.created_at) for item in repository.list_unaffected_devices()
        ] == [("Renamed", 0), ("New", 20)]
        assert repository.delete_unaffected_device("aa:bb:cc:dd:ee:01")
    with ConfigRepository(path) as repository:
        assert repository.list_unaffected_devices() == (
            UnaffectedDevice("aa:bb:cc:dd:ee:02", "New", 20),
        )


def test_observations_saved_before_hardware_addresses_were_recorded_are_kept(tmp_path):
    path = tmp_path / "settings.db"
    with ConfigRepository(path) as repository:
        repository.save_profile(Profile("normal", "Keep this profile"))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE domain_observations")
        connection.execute(
            "CREATE TABLE domain_observations ("
            "ipv4_address TEXT NOT NULL, domain TEXT NOT NULL, query_count INTEGER NOT NULL, "
            "first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, "
            "PRIMARY KEY (ipv4_address, domain))"
        )
        connection.execute(
            "INSERT INTO domain_observations VALUES ('192.168.50.20', 'amazon.com', 3, 10, 20)"
        )
    with ConfigRepository(path) as repository:
        (kept,) = repository.list_domain_observations()
        assert (str(kept.ipv4_address), kept.domain, kept.query_count) == (
            "192.168.50.20",
            "amazon.com",
            3,
        )
        assert kept.mac_address is None
        repository.save_domain_observations(
            [
                DomainObservation(
                    "192.168.50.20", "amazon.com", 1, 30, 30, mac_address="02:00:00:00:00:01"
                )
            ],
            now=30,
        )
    with ConfigRepository(path) as repository:
        (kept,) = repository.list_domain_observations()
        assert (kept.query_count, kept.mac_address) == (4, "02:00:00:00:00:01")
        assert repository.delete_domain_observations([], ["02:00:00:00:00:01"]) == 1


@pytest.mark.parametrize(
    "invalid_rows",
    [
        [("aa:bb:cc:dd:ee:01", "One"), ("AA:BB:CC:DD:EE:01", "Same identity")],
        [(f"02:00:00:00:{index // 256:02x}:{index % 256:02x}", "Device") for index in range(257)],
    ],
)
def test_rejected_upgrade_keeps_the_original_columns_and_rows(tmp_path, invalid_rows):
    path = tmp_path / "settings.db"
    legacy_database(path, invalid_rows)
    with pytest.raises(StorageError):
        ConfigRepository(path)
    with sqlite3.connect(path) as connection:
        assert [
            item[1] for item in connection.execute("PRAGMA table_info(unaffected_devices)")
        ] == ["mac_address", "name"]
        assert (
            connection.execute("SELECT mac_address, name FROM unaffected_devices").fetchall()
            == invalid_rows
        )
        assert (
            connection.execute("SELECT name FROM profiles WHERE id = 'normal'").fetchone()[0]
            == "Keep this profile"
        )


@pytest.mark.parametrize("action", ["router-sync-ap", "router-wifi", "router-check"])
def test_router_operations_can_finish_after_the_normal_helper_timeout(action):
    class SlowImport:
        def handle_payload(self, payload):
            assert payload["action"] == action
            time.sleep(0.05)
            return {"ok": True, "ssid": "Fixture network"}

    with (
        tempfile.TemporaryDirectory(prefix="jits-", dir="/tmp") as directory,
        UnixHelperServer(Path(directory) / "helper.sock", SlowImport()) as server,
    ):
        path = Path(directory) / "helper.sock"
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            client = HelperClient(socket_path=path, timeout_seconds=0.01)
            if action == "router-sync-ap":
                result = client.sync_access_point_from_router(admin_password="fixture password", expected_fingerprint="a" * 64)
            elif action == "router-check":
                result = client.check_router_password(admin_password="fixture password")
            else:
                result = client.set_router_wifi(admin_password="fixture password", enabled=False)
            assert result["ok"]
            assert client.timeout_seconds == 0.01
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_reset_traffic_does_not_query_removed_counter_tables():
    class CounterHelper(FakeHelper):
        counter_reads = 0

        def traffic_counters(self):
            self.counter_reads += 1
            return super().traffic_counters()

    helper = CounterHelper()
    with (
        ConfigRepository(":memory:") as repository,
        ControlServer(("127.0.0.1", 0), repository, helper_client=helper) as server,
    ):
        status, _, body = server.response_for("/api/traffic")
        assert status == 200
        assert json.loads(body)["available"]
        assert helper.counter_reads == 1
        status, _, _ = server.write_response_for("/api/reset", {})
        assert status == 200
        status, _, body = server.response_for("/api/traffic")
        assert status == 200
        assert not json.loads(body)["available"]
        assert helper.counter_reads == 1
