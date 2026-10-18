"""The stored format and the upgrades of older databases (mutation testing, 2026-10-18).

A mutation run showed that the older tests held the data an upgrade keeps but not the exact
text it stores, the limits it enforces or the messages it gives when an old database cannot be
upgraded. The text matters: a newer and an older release read the same database file.
"""

import json
import sqlite3

import pytest

from jittersim.config import storage
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Impairment, Profile, Randomness
from jittersim.config.storage import ConfigRepository, StorageError

# The stored text of an impairment and of profile settings


RANDOM = '{"enabled_probability": 100, "variation_percent": 0}'


def test_an_impairment_is_stored_as_sorted_json():
    text = storage._serialize_impairment(Impairment(delay_ms=5, loss_percent=2))
    assert text == (
        '{"bandwidth_kbit": null, "bandwidth_randomness": ' + RANDOM + ', "corrupt_percent": 0, '
        '"corrupt_randomness": ' + RANDOM + ', "delay_ms": 5, "delay_randomness": ' + RANDOM
        + ', "duplicate_percent": 0, "duplicate_randomness": ' + RANDOM + ', "jitter_ms": 0, '
        '"jitter_randomness": ' + RANDOM + ', "loss_burst_packets": 0, "loss_percent": 2, '
        '"loss_randomness": ' + RANDOM + ', "reorder_percent": 0, "reorder_randomness": '
        + RANDOM + '}')


def test_an_impairment_round_trips_through_its_stored_text():
    impairment = Impairment(
        delay_ms=5, jitter_ms=3, loss_percent=2, loss_burst_packets=4, bandwidth_kbit=900,
        delay_randomness=Randomness(variation_percent=5.5, enabled_probability=70),
        jitter_randomness=Randomness(mode="range", minimum=1, maximum=9),
        loss_randomness=Randomness(variation_percent=1),
        bandwidth_randomness=Randomness(variation_percent=2),
        reorder_percent=3, reorder_randomness=Randomness(variation_percent=3),
        duplicate_percent=4, duplicate_randomness=Randomness(variation_percent=4),
        corrupt_percent=6, corrupt_randomness=Randomness(variation_percent=6),
    )
    assert storage._deserialize_impairment(storage._serialize_impairment(impairment)) == impairment


@pytest.mark.parametrize("field", [
    "delay_randomness", "jitter_randomness", "loss_randomness", "bandwidth_randomness",
    "reorder_randomness", "duplicate_randomness", "corrupt_randomness",
])
def test_each_stored_randomness_field_is_read_back_as_randomness(field):
    data = json.loads(storage._serialize_impairment(Impairment()))
    data[field] = {"variation_percent": 7, "enabled_probability": 50}
    assert getattr(storage._deserialize_impairment(json.dumps(data)), field) == Randomness(
        variation_percent=7, enabled_probability=50)


def test_a_malformed_stored_impairment_is_refused_with_its_reason():
    with pytest.raises(StorageError) as raised:
        storage._deserialize_impairment("[]")
    assert str(raised.value) == "stored impairment is malformed"
    assert str(raised.value.__cause__) == "impairment JSON must contain an object"
    with pytest.raises(StorageError) as raised:
        storage._deserialize_impairment('{"delay_randomness": 5}')
    assert str(raised.value.__cause__) == "delay_randomness must contain an object"
    for bad in ("not json", '{"delay_ms": -1}', '{"unknown": 1}', "null"):
        with pytest.raises(StorageError, match="^stored impairment is malformed$"):
            storage._deserialize_impairment(bad)


def test_profile_settings_leave_out_what_the_columns_and_the_defaults_hold():
    assert storage._serialize_profile_settings(Profile("p", "P")) == (
        '{"outage_duration_randomness": ' + RANDOM + ', "outage_duration_seconds": 0, '
        '"outage_interval_randomness": ' + RANDOM + ', "outage_interval_seconds": 0, '
        '"outage_probability": 0, "reroll_interval_randomness": ' + RANDOM
        + ', "reroll_interval_seconds": 0, "website_domains": [], "website_mode": '
        '"unrestricted", "website_randomness": ' + RANDOM + '}')


def test_profile_settings_keep_a_chance_and_a_fresh_pattern_when_they_are_set():
    text = storage._serialize_profile_settings(
        Profile("p", "P", homepage_bounce_percent=1, random_mode="fresh"))
    data = json.loads(text)
    assert data["homepage_bounce_percent"] == 1
    assert data["random_mode"] == "fresh"
    assert "random_seed" not in data
    assert list(data) == sorted(data)
    only_chance = json.loads(storage._serialize_profile_settings(
        Profile("p", "P", homepage_bounce_percent=5)))
    assert only_chance["homepage_bounce_percent"] == 5 and "random_mode" not in only_chance
    only_fresh = json.loads(storage._serialize_profile_settings(
        Profile("p", "P", random_mode="fresh")))
    assert only_fresh["random_mode"] == "fresh" and "homepage_bounce_percent" not in only_fresh


def test_missing_profile_settings_read_as_an_empty_object():
    assert storage._deserialize_profile_settings("") == {}
    assert storage._deserialize_profile_settings(None) == {}
    assert storage._deserialize_profile_settings("{}") == {}


def test_profile_settings_that_are_not_an_object_are_refused_with_their_reason():
    with pytest.raises(ValueError) as raised:
        storage._deserialize_profile_settings("[1]")
    assert str(raised.value) == "profile settings JSON must contain an object"
    with pytest.raises(ValueError) as raised:
        storage._deserialize_profile_settings('{"website_randomness": "x"}')
    assert str(raised.value) == "website_randomness must contain an object"


@pytest.mark.parametrize("field", [
    "reroll_interval_randomness", "outage_duration_randomness", "outage_interval_randomness",
    "website_randomness",
])
def test_each_stored_profile_randomness_field_is_read_back_as_randomness(field):
    data = storage._deserialize_profile_settings(json.dumps(
        {field: {"variation_percent": 7, "enabled_probability": 50}}))
    assert data[field] == Randomness(variation_percent=7, enabled_probability=50)


# Upgrading an older protection list


def legacy_protection(path, columns, rows):
    with ConfigRepository(path):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE unaffected_devices")
        connection.execute(f"CREATE TABLE unaffected_devices ({columns})")
        connection.executemany(
            f"INSERT INTO unaffected_devices VALUES ({', '.join('?' * len(rows[0]))})", rows)


def stored_columns(path):
    """The first three columns. A later upgrade adds one more at the end."""

    with sqlite3.connect(path) as connection:
        return [row[1] for row in connection.execute(
            "PRAGMA table_info(unaffected_devices)")][:3]


def test_a_list_with_a_name_column_but_no_creation_time_is_renamed_and_extended(tmp_path):
    path = tmp_path / "a.db"
    legacy_protection(path, "mac_address TEXT PRIMARY KEY, name TEXT NOT NULL",
                      [("02:00:00:00:00:01", "One")])
    with ConfigRepository(path) as repository:
        assert repository.list_unaffected_devices() == (
            UnaffectedDevice("02:00:00:00:00:01", "One", 0),)
    assert stored_columns(path) == ["mac_address", "display_name", "created_at"]


def test_a_list_with_a_display_name_but_no_creation_time_gets_the_time_column(tmp_path):
    path = tmp_path / "b.db"
    legacy_protection(path, "mac_address TEXT PRIMARY KEY, display_name TEXT NOT NULL",
                      [("02:00:00:00:00:01", "One")])
    with ConfigRepository(path) as repository:
        assert repository.list_unaffected_devices() == (
            UnaffectedDevice("02:00:00:00:00:01", "One", 0),)
    assert stored_columns(path) == ["mac_address", "display_name", "created_at"]


def test_a_list_with_a_creation_time_but_an_old_name_column_is_renamed(tmp_path):
    path = tmp_path / "c.db"
    legacy_protection(
        path, "mac_address TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "created_at INTEGER NOT NULL DEFAULT 0 CHECK (created_at >= 0)",
        [("02:00:00:00:00:01", "One", 9)])
    with ConfigRepository(path) as repository:
        assert repository.list_unaffected_devices() == (
            UnaffectedDevice("02:00:00:00:00:01", "One", 9),)
    assert stored_columns(path) == ["mac_address", "display_name", "created_at"]


def test_a_current_list_is_left_alone(tmp_path):
    path = tmp_path / "d.db"
    with ConfigRepository(path) as repository:
        repository.save_unaffected_device(UnaffectedDevice("02:00:00:00:00:01", "One", 9))
    before = stored_columns(path)
    with ConfigRepository(path) as repository:
        assert repository.list_unaffected_devices() == (
            UnaffectedDevice("02:00:00:00:00:01", "One", 9),)
    assert stored_columns(path) == before == ["mac_address", "display_name", "created_at"]


def test_a_current_list_is_not_checked_again_when_the_database_is_opened(tmp_path):
    path = tmp_path / "d2.db"
    with ConfigRepository(path):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO unaffected_devices (mac_address, display_name, "
                           "created_at) VALUES ('not-an-address', 'Odd', 0)")
    with ConfigRepository(path):
        pass
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT mac_address FROM unaffected_devices").fetchall() == [("not-an-address",)]


def test_a_list_without_any_name_column_is_refused_with_a_clear_message(tmp_path):
    path = tmp_path / "e.db"
    legacy_protection(path, "mac_address TEXT PRIMARY KEY, label TEXT",
                      [("02:00:00:00:00:01", "x")])
    with pytest.raises(StorageError) as raised:
        ConfigRepository(path)
    assert str(raised.value) == "The saved protection list has an unsupported layout"


def test_addresses_of_an_old_list_are_stored_in_their_normal_form(tmp_path):
    path = tmp_path / "f.db"
    legacy_protection(path, "mac_address TEXT PRIMARY KEY, name TEXT NOT NULL",
                      [("02-00-00-00-00-AA", "Upper"), ("02:00:00:00:00:bb", "Lower")])
    with ConfigRepository(path):
        pass
    with sqlite3.connect(path) as connection:
        assert sorted(row[0] for row in connection.execute(
            "SELECT mac_address FROM unaffected_devices")) == [
            "02:00:00:00:00:aa", "02:00:00:00:00:bb"]
        assert connection.execute(
            "SELECT display_name FROM unaffected_devices WHERE mac_address = '02:00:00:00:00:aa'"
        ).fetchone()[0] == "Upper"


def test_an_old_list_of_exactly_256_devices_is_accepted(tmp_path):
    path = tmp_path / "g.db"
    legacy_protection(path, "mac_address TEXT PRIMARY KEY, name TEXT NOT NULL", [
        (f"02:00:00:00:{index // 256:02x}:{index % 256:02x}", "Device") for index in range(256)])
    with ConfigRepository(path) as repository:
        assert len(repository.list_unaffected_devices()) == 256


def test_an_old_list_of_257_devices_is_refused_with_a_clear_message(tmp_path):
    path = tmp_path / "h.db"
    legacy_protection(path, "mac_address TEXT PRIMARY KEY, name TEXT NOT NULL", [
        (f"02:00:00:00:{index // 256:02x}:{index % 256:02x}", "Device") for index in range(257)])
    with pytest.raises(StorageError) as raised:
        ConfigRepository(path)
    assert str(raised.value) == "The unaffected-device list exceeds its limit"


def test_an_old_list_with_an_invalid_device_is_refused_with_a_clear_message(tmp_path):
    path = tmp_path / "i.db"
    legacy_protection(path, "mac_address TEXT PRIMARY KEY, name TEXT NOT NULL",
                      [("not-an-address", "Bad")])
    with pytest.raises(StorageError) as raised:
        ConfigRepository(path)
    assert str(raised.value) == "The saved protection list contains an invalid device"


def test_an_old_list_that_repeats_a_device_is_refused_with_a_clear_message(tmp_path):
    path = tmp_path / "j.db"
    legacy_protection(path, "mac_address TEXT PRIMARY KEY, name TEXT NOT NULL",
                      [("aa:bb:cc:dd:ee:02", "One"), ("AA:BB:CC:DD:EE:02", "Same")])
    with pytest.raises(StorageError) as raised:
        ConfigRepository(path)
    assert str(raised.value) == "The saved protection list repeats a device identity"


# Upgrading older traffic observations


def observations_without_hardware_addresses(path, protected, rows):
    with ConfigRepository(path) as repository:
        for mac in protected:
            repository.save_unaffected_device(UnaffectedDevice(mac, "Protected", 1))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE domain_observations")
        connection.execute(
            "CREATE TABLE domain_observations (ipv4_address TEXT NOT NULL, domain TEXT NOT NULL, "
            "query_count INTEGER NOT NULL, first_seen INTEGER NOT NULL, "
            "last_seen INTEGER NOT NULL, PRIMARY KEY (ipv4_address, domain))")
        connection.executemany("INSERT INTO domain_observations VALUES (?, ?, 3, 10, 20)", rows)


def test_the_new_column_is_text_and_the_clearing_is_recorded_with_the_row_count(tmp_path):
    path = tmp_path / "k.db"
    observations_without_hardware_addresses(
        path, ["02:00:00:00:00:01"], [("192.168.50.20", "a.example.com")])
    with ConfigRepository(path) as repository:
        assert repository.list_domain_observations() == ()
        cleared_at, rows = repository.get_domain_list_clearing()
        assert rows == 1 and cleared_at > 0
    with sqlite3.connect(path) as connection:
        columns = {row[1]: row[2] for row in connection.execute(
            "PRAGMA table_info(domain_observations)")}
    assert columns["mac_address"] == "TEXT"


def test_two_cleared_rows_are_recorded_as_two(tmp_path):
    path = tmp_path / "l.db"
    observations_without_hardware_addresses(
        path, ["02:00:00:00:00:01"],
        [("192.168.50.20", "a.example.com"), ("192.168.50.21", "b.example.com")])
    with ConfigRepository(path) as repository:
        assert repository.get_domain_list_clearing()[1] == 2


def test_nothing_is_recorded_when_a_device_is_protected_but_no_row_is_old(tmp_path):
    path = tmp_path / "m.db"
    observations_without_hardware_addresses(path, ["02:00:00:00:00:01"], [])
    with ConfigRepository(path) as repository:
        assert repository.get_domain_list_clearing() is None


def test_nothing_is_cleared_or_recorded_without_a_protected_device(tmp_path):
    path = tmp_path / "n.db"
    observations_without_hardware_addresses(path, [], [("192.168.50.20", "a.example.com")])
    with ConfigRepository(path) as repository:
        assert len(repository.list_domain_observations()) == 1
        assert repository.get_domain_list_clearing() is None


# Upgrading an older schedules table


OLD_SCHEDULES = """
DROP TABLE schedules;
CREATE TABLE schedules (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    days_json TEXT NOT NULL,
    start_minute INTEGER NOT NULL,
    end_minute INTEGER NOT NULL,
    enabled INTEGER NOT NULL
);
"""


def test_an_upgrade_that_fails_is_reported_and_leaves_the_old_table_alone(tmp_path, monkeypatch):
    from jittersim.config.models import Device

    path = tmp_path / "o.db"
    with ConfigRepository(path) as repository:
        repository.save_profile(Profile("slow", "Slow"))
        repository.save_device(Device("alice", "Alice", "192.168.50.10", "slow"))
    with sqlite3.connect(path) as connection:
        connection.executescript(OLD_SCHEDULES)
        connection.execute("INSERT INTO schedules VALUES ('s', 'alice', 'slow', '[0]', 0, 60, 1)")
    real = storage._SCHEDULES_TABLE_SQL

    class FailsForTheNewTable(str):
        def format(self, *args, **kwargs):
            if kwargs.get("name") == "schedules_upgrade":
                raise sqlite3.OperationalError("simulated failure")
            return real.format(*args, **kwargs)

    monkeypatch.setattr(storage, "_SCHEDULES_TABLE_SQL", FailsForTheNewTable(real))
    with pytest.raises(StorageError) as raised:
        ConfigRepository(path)
    assert str(raised.value) == "could not upgrade the saved schedules"
    assert isinstance(raised.value.__cause__, sqlite3.Error)
    with sqlite3.connect(path) as connection:
        assert [row[1] for row in connection.execute("PRAGMA table_info(schedules)")] == [
            "id", "device_id", "profile_id", "days_json", "start_minute", "end_minute", "enabled"]
        assert connection.execute("SELECT id FROM schedules").fetchall() == [("s",)]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'schedules_upgrade'").fetchone() is None
