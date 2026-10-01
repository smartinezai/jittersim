from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, DomainObservation, Profile
from jittersim.config.protection import (
    observation_is_protected,
    protected_addresses,
    protected_macs,
    station_macs_by_address,
)
from jittersim.config.storage import ConfigRepository

PROTECTED = "02:00:00:00:00:01"
OTHER = "02:00:00:00:00:02"


def make_repository(tmp_path):
    repository = ConfigRepository(tmp_path / "config.sqlite3")
    repository.save_profile(Profile(id="slow", name="Slow"))
    return repository


def test_protected_macs_are_sorted(tmp_path):
    repository = make_repository(tmp_path)
    repository.save_unaffected_device(UnaffectedDevice(OTHER, "b", 1))
    repository.save_unaffected_device(UnaffectedDevice(PROTECTED, "a", 1))
    assert protected_macs(repository) == (PROTECTED, OTHER)


def test_addresses_come_from_stations_and_saved_devices(tmp_path):
    repository = make_repository(tmp_path)
    repository.save_unaffected_device(UnaffectedDevice(PROTECTED, "a", 1))
    repository.save_device(
        Device(
            id="saved",
            name="Saved",
            ipv4_address="192.168.50.20",
            profile_id="slow",
            mac_address=PROTECTED,
        )
    )
    repository.save_device(
        Device(
            id="other",
            name="Other",
            ipv4_address="192.168.50.21",
            profile_id="slow",
            mac_address=OTHER,
        )
    )
    stations = [
        {"mac_address": PROTECTED, "ipv4_address": "192.168.50.30"},
        {"mac_address": OTHER, "ipv4_address": "192.168.50.31"},
        {"mac_address": PROTECTED, "ipv4_address": None},
        {"ipv4_address": "192.168.50.32"},
    ]
    assert protected_addresses(repository, stations) == {"192.168.50.20", "192.168.50.30"}


def test_nothing_is_protected_by_default(tmp_path):
    repository = make_repository(tmp_path)
    assert protected_macs(repository) == ()
    stations = [{"mac_address": OTHER, "ipv4_address": "192.168.50.31"}]
    assert protected_addresses(repository, stations) == set()


def test_station_macs_are_mapped_by_address(tmp_path):
    stations = [
        {"mac_address": "02:00:00:00:00:01", "ipv4_address": "192.168.50.10"},
        {"mac_address": "02-00-00-00-00-02", "ipv4_address": "192.168.50.11"},
        {"mac_address": None, "ipv4_address": "192.168.50.12"},
        {"mac_address": "02:00:00:00:00:04", "ipv4_address": None},
        {"mac_address": "not-a-mac", "ipv4_address": "192.168.50.14"},
        {"mac_address": "01:00:5e:00:00:01", "ipv4_address": "192.168.50.15"},
        {"ipv4_address": "192.168.50.16"},
    ]

    assert station_macs_by_address(stations) == {
        "192.168.50.10": "02:00:00:00:00:01",
        "192.168.50.11": "02:00:00:00:00:02",
    }


def test_an_address_with_two_owners_has_no_known_hardware_address(tmp_path):
    stations = [
        {"mac_address": "02:00:00:00:00:01", "ipv4_address": "192.168.50.10"},
        {"mac_address": "02:00:00:00:00:02", "ipv4_address": "192.168.50.10"},
        {"mac_address": "02:00:00:00:00:02", "ipv4_address": "192.168.50.11"},
        {"mac_address": "02:00:00:00:00:02", "ipv4_address": "192.168.50.11"},
    ]

    assert station_macs_by_address(stations) == {"192.168.50.11": "02:00:00:00:00:02"}


def test_an_observation_is_protected_by_its_hardware_address_or_its_address():
    macs = {PROTECTED}
    addresses = {"192.168.50.30"}

    def seen(address, mac=None):
        return DomainObservation(address, "example.com", 1, 1, 1, mac_address=mac)

    assert observation_is_protected(seen("192.168.50.99", PROTECTED), macs, addresses)
    assert observation_is_protected(seen("192.168.50.30"), macs, addresses)
    assert observation_is_protected(seen("192.168.50.30", OTHER), macs, addresses)
    assert not observation_is_protected(seen("192.168.50.99", OTHER), macs, addresses)
    assert not observation_is_protected(seen("192.168.50.99"), macs, addresses)
    assert not observation_is_protected(seen("192.168.50.99", PROTECTED), set(), set())
